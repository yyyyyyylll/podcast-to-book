// 开源版：埋点已禁用，所有调用都是 no-op
export const analytics = {
  init: () => {},
  track: (_event: string, _props?: Record<string, unknown>) => {},
  page: (_path: string) => {},
  reset: () => {},
  identify: (_userId: string, _props?: Record<string, unknown>) => {},
  bindTaskAttempt: (_taskId?: string, _attemptId?: string) => {},
  ensureAttempt: (_taskId?: string) => '',
  getAttemptIdForTask: (_taskId?: string): string | undefined => undefined,
};
