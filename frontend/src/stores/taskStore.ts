import { create } from 'zustand';
import { getTaskStatus, type TaskStatus } from '../services/api';

interface TaskState {
  currentTask: TaskStatus | null;
  polling: boolean;
  pollTimer: ReturnType<typeof setTimeout> | null;

  setTask: (task: TaskStatus) => void;
  startPolling: (taskId: string) => void;
  stopPolling: () => void;
  reset: () => void;
}

export const useTaskStore = create<TaskState>((set, get) => ({
  currentTask: null,
  polling: false,
  pollTimer: null,

  setTask: (task) => set({ currentTask: task }),

  startPolling: (taskId: string) => {
    const { stopPolling } = get();
    stopPolling();
    set({ polling: true });

    const poll = async () => {
      if (!get().polling) return;
      try {
        const task = await getTaskStatus(taskId);
        set({ currentTask: task });
        if (task.status === 'pending' || task.status === 'processing') {
          const timer = setTimeout(poll, 3000);
          set({ pollTimer: timer });
        } else if (task.status === 'needs_speaker_input') {
          set({ polling: false });
        } else {
          set({ polling: false });
        }
      } catch {
        const timer = setTimeout(poll, 5000);
        set({ pollTimer: timer });
      }
    };
    poll();
  },

  stopPolling: () => {
    const { pollTimer } = get();
    if (pollTimer) clearTimeout(pollTimer);
    set({ polling: false, pollTimer: null });
  },

  reset: () => {
    const { stopPolling } = get();
    stopPolling();
    set({ currentTask: null });
  },
}));
