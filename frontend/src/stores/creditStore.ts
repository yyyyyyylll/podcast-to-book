// 开源版：积分系统已禁用，余额恒为无限大。
import { create } from 'zustand';

interface CreditState {
  balance: number | null;
  loading: boolean;
  fetchBalance: () => Promise<void>;
  clearBalance: () => void;
}

export const useCreditStore = create<CreditState>((set) => ({
  balance: 999999,
  loading: false,
  fetchBalance: async () => { set({ balance: 999999 }); },
  clearBalance: () => set({ balance: null }),
}));
