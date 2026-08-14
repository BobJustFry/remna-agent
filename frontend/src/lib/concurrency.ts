const STORAGE_KEY = "remna.queue.concurrency";
const DEFAULT = 5;
const MIN = 1;
const MAX = 10;

export function getQueueConcurrency(): number {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT;
    const n = Number(raw);
    if (!Number.isFinite(n)) return DEFAULT;
    return Math.min(MAX, Math.max(MIN, Math.round(n)));
  } catch {
    return DEFAULT;
  }
}

export function setQueueConcurrency(value: number): number {
  const n = Math.min(MAX, Math.max(MIN, Math.round(value)));
  localStorage.setItem(STORAGE_KEY, String(n));
  return n;
}

export const QUEUE_CONCURRENCY_BOUNDS = { min: MIN, max: MAX, default: DEFAULT } as const;
