import axios from 'axios';
import { useAuthStore } from '../stores/authStore';

const api = axios.create({
  baseURL: '/api/v1',
  timeout: 120000,
});

// Attach access token to every request
api.interceptors.request.use((config) => {
  const token = useAuthStore.getState().token;
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// On 401, try refresh once then retry; if refresh fails, prompt login
let isRefreshing = false;
let pendingQueue: Array<{
  resolve: (token: string) => void;
  reject: (err: unknown) => void;
}> = [];

api.interceptors.response.use(
  (res) => res,
  async (error) => {
    const original = error.config;
    if (error.response?.status !== 401 || original._retried) {
      return Promise.reject(error);
    }

    if (original.url?.includes('/auth/')) {
      return Promise.reject(error);
    }

    if (isRefreshing) {
      return new Promise((resolve, reject) => {
        pendingQueue.push({
          resolve: (token: string) => {
            original.headers.Authorization = `Bearer ${token}`;
            original._retried = true;
            resolve(api(original));
          },
          reject,
        });
      });
    }

    isRefreshing = true;
    original._retried = true;

    const success = await useAuthStore.getState().tryRefresh();
    isRefreshing = false;

    if (success) {
      const newToken = useAuthStore.getState().token!;
      pendingQueue.forEach((p) => p.resolve(newToken));
      pendingQueue = [];
      original.headers.Authorization = `Bearer ${newToken}`;
      return api(original);
    } else {
      pendingQueue.forEach((p) => p.reject(error));
      pendingQueue = [];
      useAuthStore.getState().openLogin();
      return Promise.reject(error);
    }
  }
);

export default api;

/**
 * 从 axios 错误里提取给用户看的错误原因。
 *
 * 优先级：
 *   1. 后端返回的 detail（FastAPI HTTPException 的标准字段）
 *   2. 特殊 HTTP 状态码的定制文案（如 nginx 413、网络断开）
 *   3. 传入的 fallback
 *
 * nginx 级别的错误（如 413 Request Entity Too Large）返回的是 HTML 而不是 JSON，
 * 所以拿不到 detail，需要按 status 给话术。
 */
export function getApiErrorMessage(err: unknown, fallback = '请求失败，请稍后重试'): string {
  const anyErr = err as { response?: { status?: number; data?: { detail?: string } }; code?: string; message?: string };
  const status = anyErr?.response?.status;
  const detail = anyErr?.response?.data?.detail;

  if (typeof detail === 'string' && detail.trim()) {
    return detail;
  }

  if (status === 413) {
    return '文件过大，请压缩后重试';
  }
  if (status === 401) {
    return '登录已过期，请重新登录';
  }
  if (status === 403) {
    return '没有权限执行此操作';
  }
  if (status === 404) {
    return '资源不存在或已被删除';
  }
  if (status && status >= 500) {
    return '服务器开小差了，请稍后重试';
  }

  if (anyErr?.code === 'ECONNABORTED') {
    return '请求超时，请检查网络后重试';
  }
  if (anyErr?.message === 'Network Error') {
    return '网络连接异常，请检查网络';
  }

  return fallback;
}

// ─── Auth API ──────────────────────────────────────────────

export async function sendCode(phone: string): Promise<{ dev_mode?: boolean }> {
  const res = await api.post('/auth/send-code', { phone });
  return res.data;
}

export async function loginWithCode(
  phone: string,
  code: string,
  referralCode?: string,
): Promise<{
  access_token: string;
  refresh_token: string;
  is_new_user?: boolean;
  user: { id: string; phone: string; nickname: string; avatar_url: string; guide_completed?: boolean };
}> {
  const payload: Record<string, string> = { phone, code };
  if (referralCode) payload.referral_code = referralCode;
  const res = await api.post('/auth/login', payload);
  return res.data;
}

export async function getMe() {
  const res = await api.get('/auth/me');
  return res.data;
}

export async function updateMe(data: { nickname?: string }) {
  const res = await api.put('/auth/me', data);
  return res.data;
}

export async function uploadAvatar(file: File): Promise<{ avatar_url: string }> {
  const formData = new FormData();
  formData.append('file', file);
  const res = await api.put('/auth/me/avatar', formData);
  return res.data;
}

export async function sendChangePhoneCode(phone: string): Promise<{ dev_mode?: boolean }> {
  const res = await api.post('/auth/change-phone/send-code', { phone });
  return res.data;
}

export async function changePhone(
  newPhone: string,
  code: string
): Promise<{
  access_token: string;
  refresh_token: string;
  user: { id: string; phone: string; nickname: string; avatar_url: string };
}> {
  const res = await api.post('/auth/change-phone', { new_phone: newPhone, code });
  return res.data;
}

export async function deleteAccount(): Promise<{ message: string }> {
  const res = await api.delete('/auth/me');
  return res.data;
}

// ─── My Tasks (Orders) ────────────────────────────────────

export interface MyTaskItem {
  id: string;
  title: string;
  author: string;
  podcast_name: string;
  status: 'pending' | 'processing' | 'completed' | 'failed' | 'needs_speaker_input' | 'regenerating';
  current_stage: string | null;
  progress: number;
  cover_url: string | null;
  pdf_path: string | null;
  error_message: string | null;
  credit_charged: number | null;
  unlock_level?: UnlockLevel;
  created_at: string | null;
  completed_at: string | null;
}

export async function getMyTasks(page = 1, pageSize = 20): Promise<{
  page: number;
  page_size: number;
  items: MyTaskItem[];
}> {
  const res = await api.get('/my/tasks', { params: { page, page_size: pageSize } });
  return res.data;
}

// ─── Task Types & API ──────────────────────────────────────

export interface SpeakerInputData {
  sample_segments: Array<{ index: number; speaker: string; text: string }>;
  speaker_count: number;
}

export type UnlockLevel = 'preview' | 'full_text' | 'full_access';

export interface TaskStatus {
  id: string;
  title: string;
  author: string;
  description: string;
  status: 'pending' | 'processing' | 'completed' | 'failed' | 'needs_speaker_input' | 'regenerating';
  current_stage: string | null;
  progress: number;
  error_message: string | null;
  stages: Record<string, string>;
  pdf_path: string | null;
  cover_url: string | null;
  unlock_level?: UnlockLevel;
  created_at: string | null;
  completed_at: string | null;
  speaker_input?: SpeakerInputData;
}

export interface TaskResult extends TaskStatus {
  results: {
    transcription: any;
    composed: any;
    highlights: any;
    annotated: any;
    illustrations: any;
  };
  typst_source: string | null;
  pdf_url: string | null;
  pdf_missing?: boolean;
}

export interface UploadParams {
  podcast_url: string;
  title?: string;
  host_name?: string;
  guest_names?: string;
  editor_preface?: string;
  cover_style?: string;
}

export interface CoverStyle {
  id: string;
  name: string;
  description: string;
  default_colors: { primary: string; accent: string };
  preview: string;
}

export async function getCoverStyles(): Promise<CoverStyle[]> {
  const res = await api.get('/cover-styles');
  return res.data;
}

export async function uploadPodcastUrl(
  params: UploadParams
): Promise<{ task_id: string; title: string; author: string }> {
  const formData = new FormData();
  formData.append('podcast_url', params.podcast_url);
  if (params.title) formData.append('title', params.title);
  if (params.host_name) formData.append('host_name', params.host_name);
  if (params.guest_names) formData.append('guest_names', params.guest_names);
  if (params.editor_preface) formData.append('editor_preface', params.editor_preface);
  if (params.cover_style) formData.append('cover_style', params.cover_style);
  const res = await api.post('/upload-url', formData);
  return res.data;
}

export async function getTaskStatus(taskId: string): Promise<TaskStatus> {
  const res = await api.get(`/tasks/${taskId}`);
  return res.data;
}

export async function submitSpeakers(
  taskId: string,
  hostName: string,
  guestNames: string,
): Promise<{ task_id: string; status: string; message: string }> {
  const res = await api.post(`/tasks/${taskId}/speakers`, {
    host_name: hostName,
    guest_names: guestNames,
  });
  return res.data;
}

export async function getTaskResult(taskId: string): Promise<TaskResult> {
  const res = await api.get(`/tasks/${taskId}/result`);
  return res.data;
}

export function getDownloadUrl(taskId: string): string {
  return `/api/v1/tasks/${taskId}/download`;
}

export function getEpubDownloadUrl(taskId: string): string {
  return `/api/v1/tasks/${taskId}/download/epub`;
}

export async function downloadFile(url: string, filename: string): Promise<void> {
  const res = await api.get(url.replace('/api/v1', ''), { responseType: 'blob' });
  const blobUrl = URL.createObjectURL(res.data);
  const a = document.createElement('a');
  a.href = blobUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(blobUrl);
}

/**
 * 向后端申请一次性下载 URL（带短 TTL 的 download token，直接挂在 query 上）。
 * 前端拿到后用原生浏览器下载，避开 blob 整包下载在移动端/弱网下的坑。
 */
export async function requestDownloadUrl(
  taskId: string,
  kind: 'pdf' | 'epub',
): Promise<{ url: string; expires_in: number }> {
  const res = await api.get(`/tasks/${taskId}/download-url`, { params: { kind } });
  return res.data;
}

/**
 * 使用浏览器原生下载一个书稿文件（PDF / EPUB）。
 *
 * 做法：先 GET `/tasks/{id}/download-url` 拿一条带 download_token 的 URL，
 * 再用 `<a download href=url>` 触发；成功后浏览器会走原生下载管理器，
 * 支持断点续传、进度条、后台下载，兼容 iOS Safari 和微信内置浏览器。
 *
 * 行为：
 *   - 触发失败（网络/权限错误）抛异常，由调用方 toast。
 *   - 成功返回 void，不等待下载完成。
 */
export async function downloadTaskFile(
  taskId: string,
  kind: 'pdf' | 'epub',
  filename: string,
): Promise<void> {
  const { url } = await requestDownloadUrl(taskId, kind);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.rel = 'noopener';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

// ─── Unlock API ──────────────────────────────────────────

export async function unlockFulltext(taskId: string): Promise<{ unlock_level: string; message: string }> {
  const res = await api.post(`/tasks/${taskId}/unlock-fulltext`);
  return res.data;
}

export async function unlockEdit(taskId: string): Promise<{ unlock_level: string; message: string }> {
  const res = await api.post(`/tasks/${taskId}/unlock-edit`);
  return res.data;
}

// ─── Referral API ──────────────────────────────────────────

export interface ReferralInfo {
  referral_code: string;
  invited_count: number;
  rewarded_count: number;
  max_rewards: number;
  total_earned: number;
}

export interface ReferralRecord {
  id: string;
  user_phone: string;
  credits: number;
  created_at: string | null;
}

export async function getReferralCode(): Promise<ReferralInfo> {
  const res = await api.get('/referral/my-code');
  return res.data;
}

export async function getReferralRecords(page = 1, pageSize = 20): Promise<{
  total: number;
  page: number;
  page_size: number;
  items: ReferralRecord[];
}> {
  const res = await api.get('/referral/records', { params: { page, page_size: pageSize } });
  return res.data;
}

export async function retryTask(taskId: string): Promise<{ task_id: string; status: string; message: string }> {
  const res = await api.post(`/tasks/${taskId}/retry`);
  return res.data;
}

export async function rebuildPdf(taskId: string): Promise<{
  status: string;
  message?: string;
  pdf_url?: string;
}> {
  const res = await api.post(`/tasks/${taskId}/rebuild-pdf`);
  return res.data;
}

export interface LLMCallLog {
  call_id: number;
  stage: string;
  label: string;
  model: string;
  prompt_chars: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  duration_s: number;
  tokens_per_sec: number;
  status: 'running' | 'success' | 'error' | 'timeout' | 'mock';
  started_at: number;
  finished_at: number | null;
  error: string | null;
  is_search?: boolean;
  is_image?: boolean;
  is_vlm?: boolean;
  elapsed_s?: number;
}

export interface LLMLogsSummary {
  total_calls: number;
  completed: number;
  running: number;
  failed: number;
  total_duration_s: number;
  total_tokens: number;
  total_prompt_tokens: number;
  total_completion_tokens: number;
  avg_speed: number;
}

export interface LLMLogsResponse {
  task_id: string;
  task_status: string;
  summary: LLMLogsSummary;
  calls: LLMCallLog[];
}

export async function getTaskLLMLogs(taskId: string): Promise<LLMLogsResponse> {
  const res = await api.get(`/tasks/${taskId}/llm-logs`);
  return res.data;
}

// ─── Credits API ──────────────────────────────────────────

export interface CreditBalance {
  balance: number;
  total_recharged: number;
  total_consumed: number;
}

export interface CreditTransaction {
  id: string;
  type: 'welcome' | 'recharge' | 'consume' | 'refund';
  amount: number;
  balance_after: number;
  description: string;
  created_at: string | null;
}

export interface CreditPack {
  index: number;
  name: string;
  credits: number;
  price_yuan: string;
  price_cents: number;
  books?: number;
  price_per_book_yuan?: string;
}

export interface EbookCosts {
  podcast: number;
  standard: number;
  premium: number;
}

export interface PrintCosts {
  mid: number;
  high: number;
}

export interface CustomPricing {
  price_per_unit_yuan: number;
  min_credits: number;
}

export interface CreditPacksResponse {
  packs: CreditPack[];
  payment_provider?: string;
  currency?: string;
  ebook_costs: EbookCosts;
  print_costs: PrintCosts;
  pricing?: {
    original_price_yuan: number;
    promo_price_yuan: number;
    promo_active: boolean;
    cost_per_book: number;
  };
  custom_pricing?: CustomPricing;
}

export interface RechargeResult {
  order_id: string;
  order_no: string;
  payment_method?: string;
  checkout_url?: string;
  pay_url: string;
  currency?: string;
  dev_mode?: boolean;
  sandbox_hint?: string;
}

export interface OrderStatus {
  order_id: string;
  order_no: string;
  status: 'pending' | 'paid' | 'expired' | 'refunded';
  credits: number;
  amount_yuan: string;
  amount_display?: string;
  currency?: string;
  payment_method?: string;
  product_name: string;
  created_at: string | null;
  paid_at: string | null;
}

export async function getCreditBalance(): Promise<CreditBalance> {
  const res = await api.get('/credits/balance');
  return res.data;
}

export async function getCreditTransactions(page = 1, pageSize = 20): Promise<{
  page: number;
  page_size: number;
  items: CreditTransaction[];
}> {
  const res = await api.get('/credits/transactions', { params: { page, page_size: pageSize } });
  return res.data;
}

export async function getCreditPacks(): Promise<CreditPacksResponse> {
  const res = await api.get('/credits/packs');
  return res.data;
}

export async function getGenerationCost(): Promise<{ cost: number; is_first_book: boolean }> {
  const res = await api.get('/credits/generation-cost');
  return res.data;
}

export async function createRechargeOrder(
  packIndex?: number,
  customCredits?: number,
): Promise<RechargeResult> {
  const body: { pack_index?: number; custom_credits?: number } = {};
  if (customCredits != null) {
    body.custom_credits = customCredits;
  } else {
    body.pack_index = packIndex ?? 0;
  }
  const res = await api.post('/credits/recharge', body);
  return res.data;
}

export async function getRechargeStatus(orderId: string): Promise<OrderStatus> {
  const res = await api.get(`/credits/recharge/${orderId}/status`);
  return res.data;
}

export async function getRechargeStatusByOrderNo(orderNo: string): Promise<OrderStatus> {
  const res = await api.get('/credits/recharge/status-by-no', { params: { order_no: orderNo } });
  return res.data;
}

export interface OrderItem {
  id: string;
  order_no: string;
  product_name: string;
  amount_yuan: string;
  amount_display?: string;
  credits: number;
  status: 'pending' | 'paid' | 'expired' | 'refunded';
  payment_method: string;
  currency?: string;
  created_at: string | null;
  paid_at: string | null;
}

export async function getOrders(page = 1, pageSize = 20): Promise<{
  page: number;
  page_size: number;
  items: OrderItem[];
}> {
  const res = await api.get('/credits/orders', { params: { page, page_size: pageSize } });
  return res.data;
}

// ─── Redemption Code API ──────────────────────────────────

export interface RedeemResult {
  credits: number;
  balance: number;
  message: string;
}

export async function redeemCode(code: string): Promise<RedeemResult> {
  const res = await api.post('/credits/redeem', { code });
  return res.data;
}

export interface RedemptionCodeItem {
  id: string;
  code: string;
  credits: number;
  max_uses: number;
  used_count: number;
  expires_at: string | null;
  note: string;
  status: string;
  code_type: string;
  channel: string;
  batch_name: string;
  creator_phone: string;
  creator_nickname: string;
  created_at: string | null;
}

export async function adminGenerateCodes(
  data: {
    credits: number;
    count: number;
    max_uses: number;
    expires_at?: string;
    note?: string;
    code_type?: string;
    channel?: string;
    batch_name?: string;
    custom_tag?: string;
  },
  adminToken: string,
): Promise<{ codes: string[]; count: number }> {
  const res = await api.post('/admin/redemption-codes', data, {
    headers: { 'x-admin-token': adminToken },
  });
  return res.data;
}

export async function adminListRedemptionCodes(
  page: number,
  pageSize: number,
  adminToken: string,
  filters?: { code_type?: string; channel?: string; status?: string; batch_name?: string },
): Promise<{ total: number; page: number; page_size: number; items: RedemptionCodeItem[] }> {
  const res = await api.get('/admin/redemption-codes', {
    params: { page, page_size: pageSize, ...filters },
    headers: { 'x-admin-token': adminToken },
  });
  return res.data;
}

export async function adminDisableRedemptionCode(codeId: string, adminToken: string): Promise<{ ok: boolean }> {
  const res = await api.put(`/admin/redemption-codes/${codeId}/disable`, null, {
    headers: { 'x-admin-token': adminToken },
  });
  return res.data;
}

export async function adminBatchDisableCodes(codeIds: string[], adminToken: string): Promise<{ disabled: number }> {
  const res = await api.put('/admin/benefit-codes/batch-disable', { code_ids: codeIds }, {
    headers: { 'x-admin-token': adminToken },
  });
  return res.data;
}

// ─── Feedback API ─────────────────────────────────────────

export async function submitFeedback(data: FormData): Promise<{ id: string; message: string }> {
  const res = await api.post('/feedback', data);
  return res.data;
}

export interface FeedbackItem {
  id: string;
  user_id: string;
  user_phone: string;
  user_nickname: string;
  task_id: string;
  category: string;
  category_label: string;
  content: string;
  image_paths: string[];
  status: string;
  admin_note: string;
  created_at: string | null;
}

export interface FeedbackListResponse {
  feedbacks: FeedbackItem[];
  total: number;
  pending_count: number;
  page: number;
  page_size: number;
}

// ─── Rating API ──────────────────────────────────────────────

export interface MyRating {
  id: string;
  task_id: string;
  score: number;
  nps_score: number;
  comment: string;
  image_paths: string[];
  contact_ok: boolean;
  created_at: string | null;
  updated_at: string | null;
}

export async function submitRating(data: FormData): Promise<{ id: string; message: string }> {
  const res = await api.post('/ratings', data);
  return res.data;
}

export async function getMyRating(taskId: string): Promise<MyRating | null> {
  const res = await api.get(`/ratings/${taskId}`);
  return res.data;
}

export interface RatingItem {
  id: string;
  task_id: string;
  task_title: string;
  user_id: string;
  user_phone: string;
  user_nickname: string;
  score: number;
  nps_score: number;
  comment: string;
  image_paths: string[];
  contact_ok: boolean;
  created_at: string | null;
  updated_at: string | null;
}

export interface RatingListResponse {
  ratings: RatingItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface RatingStats {
  total: number;
  avg_score: number;
  avg_nps: number;
  contact_ok_count: number;
  distribution: Record<string, number>;
}

// ─── Edit API ─────────────────────────────────────────────

export interface EditCoverStyle {
  id: string;
  name: string;
  preview_url: string;
}

export interface EditIllustration {
  filename: string;
  preview_url: string;
  chapter_title: string;
  after_paragraph: number;
  caption: string;
  size?: string;
  type?: string;
  source?: string;
}

export interface EditData {
  annotated_content: any;
  highlights: { quotes: any[] };
  illustrations: { images: EditIllustration[] };
  editor_preface: string;
  user_title: string;
  user_host_name: string;
  user_guest_names: string[];
  host_name: string;
  guest_names: string[];
  cover_style: string;
  custom_full_cover_url: string;
  custom_back_cover_url: string;
  original_cover_url: string;
  available_cover_styles: EditCoverStyle[];
  original_title: string;
  task_title: string;
}

export interface EditRequest {
  annotated_content?: any;
  highlights?: any;
  illustrations?: any;
  editor_preface?: string;
  user_title?: string;
  user_host_name?: string;
  user_guest_names?: string[];
  cover_style?: string;
  custom_cover_url?: string;
  custom_full_cover_url?: string;
  custom_back_cover_url?: string;
  restore_original_cover?: boolean;
}

export async function getEditData(taskId: string): Promise<EditData> {
  const res = await api.get(`/tasks/${taskId}/edit-data`);
  return res.data;
}

export async function saveAndRegenerate(
  taskId: string,
  data: EditRequest,
): Promise<{ status: string; pdf_url: string | null }> {
  const res = await api.patch(`/tasks/${taskId}/edit`, data, { timeout: 600000 });
  return res.data;
}

export async function uploadIllustration(
  taskId: string,
  file: File,
): Promise<{ filename: string }> {
  const formData = new FormData();
  formData.append('file', file);
  const res = await api.post(`/tasks/${taskId}/illustrations/upload`, formData);
  return res.data;
}

export async function uploadCustomCover(
  taskId: string,
  file: File,
): Promise<{ cover_url: string }> {
  const formData = new FormData();
  formData.append('file', file);
  const res = await api.post(`/tasks/${taskId}/cover/upload`, formData);
  return res.data;
}

export async function uploadFullCover(
  taskId: string,
  file: File,
): Promise<{ full_cover_url: string }> {
  const formData = new FormData();
  formData.append('file', file);
  const res = await api.post(`/tasks/${taskId}/full-cover/upload`, formData);
  return res.data;
}

export async function uploadBackCover(
  taskId: string,
  file: File,
): Promise<{ back_cover_url: string }> {
  const formData = new FormData();
  formData.append('file', file);
  const res = await api.post(`/tasks/${taskId}/back-cover/upload`, formData);
  return res.data;
}
