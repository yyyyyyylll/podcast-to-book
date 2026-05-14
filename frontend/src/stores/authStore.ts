// 开源版：单用户，无登录态。全套 API 保留接口，但实现都是 noop。
import { create } from 'zustand';

export interface UserInfo {
  id: string;
  phone: string;
  nickname: string;
  avatar_url: string;
  guide_completed?: boolean;
}

interface AuthState {
  token: string | null;
  refreshToken: string | null;
  user: UserInfo | null;
  showLogin: boolean;
  loginTrigger: string | null;
  ready: boolean;

  setAuth: (token: string, refreshToken: string, user: UserInfo) => void;
  logout: () => void;
  openLogin: (trigger?: string) => void;
  closeLogin: () => void;
  tryRefresh: () => Promise<boolean>;
  loadFromStorage: () => void;
  initAuth: () => Promise<void>;
  markGuideCompleted: () => Promise<void>;
}

const LOCAL_USER: UserInfo = {
  id: 'local',
  phone: '',
  nickname: '本地用户',
  avatar_url: '',
  guide_completed: true,
};

export const useAuthStore = create<AuthState>((set) => ({
  token: 'local-no-auth',
  refreshToken: null,
  user: LOCAL_USER,
  showLogin: false,
  loginTrigger: null,
  ready: true,
  setAuth: () => {},
  logout: () => {},
  openLogin: (_trigger?: string) => {},
  closeLogin: () => {},
  tryRefresh: async () => true,
  loadFromStorage: () => {},
  initAuth: async () => { set({ ready: true, user: LOCAL_USER }); },
  markGuideCompleted: async () => { set({ user: { ...LOCAL_USER, guide_completed: true } }); },
}));
