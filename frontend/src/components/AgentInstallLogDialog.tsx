import { useEffect, useRef } from "react";
import type { InstallJob } from "../hooks/useAgentInstallQueue";

type Props = {
  job: InstallJob;
  onClose: () => void;
  onCancel: () => void;
  onRetry: () => void;
  onDismiss: () => void;
};

export function AgentInstallLogDialog({ job, onClose, onCancel, onRetry, onDismiss }: Props) {
  const logRef = useRef<HTMLPreElement>(null);
  const active = job.phase === "queued" || job.phase === "running";
  const cancelled =
    job.phase === "error" && (job.statusMessage?.toLowerCase().includes("отменена") ?? false);

  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [job.lines]);

  const title =
    job.phase === "queued"
      ? "В очереди…"
      : job.phase === "running"
        ? "Установка агента…"
        : job.phase === "done"
          ? "Агент установлен"
          : cancelled
            ? "Установка отменена"
            : "Ошибка установки";

  return (
    <div className="fixed inset-0 z-[60] flex items-end justify-center bg-black/60 p-0 backdrop-blur-sm sm:items-center sm:p-4">
      <div
        role="dialog"
        aria-modal="true"
        className="flex max-h-[min(100dvh,860px)] w-full max-w-2xl flex-col overflow-hidden rounded-t-[var(--radius)] border border-[var(--border)] bg-[var(--bg-elevated)] shadow-2xl sm:rounded-[var(--radius)]"
      >
        <div className="flex items-start justify-between gap-3 border-b border-[var(--border)] px-5 py-4">
          <div className="min-w-0">
            <h2 className="text-base font-semibold text-[var(--text)]">{title}</h2>
            <p className="mt-1 truncate font-mono text-xs text-[var(--muted)]">
              {job.nodeName} · {job.sshLabel}
            </p>
          </div>
          {job.phase === "queued" && (
            <span className="mt-0.5 shrink-0 rounded-md border border-[var(--border)] bg-[var(--bg)] px-2.5 py-1 text-[11px] font-medium text-[var(--muted)]">
              QUEUE
            </span>
          )}
          {job.phase === "running" && (
            <span className="mt-0.5 inline-flex shrink-0 items-center gap-2 rounded-md border border-[var(--border)] bg-[var(--bg)] px-2.5 py-1 text-[11px] font-medium text-[var(--accent)]">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-[var(--accent)]" />
              SSH
            </span>
          )}
          {job.phase === "done" && (
            <span className="mt-0.5 shrink-0 rounded-md border border-[rgba(61,214,140,0.35)] bg-[rgba(61,214,140,0.1)] px-2.5 py-1 text-[11px] font-medium text-[var(--success)]">
              OK
            </span>
          )}
          {job.phase === "error" && (
            <span className="mt-0.5 shrink-0 rounded-md border border-[rgba(240,113,120,0.35)] bg-[rgba(240,113,120,0.1)] px-2.5 py-1 text-[11px] font-medium text-[var(--danger)]">
              {cancelled ? "CANCELLED" : "ERROR"}
            </span>
          )}
        </div>

        <div className="px-5 py-4">
          <div className="overflow-hidden rounded-lg border border-[#1a2a32] bg-[#070c0f] shadow-inner">
            <div className="flex items-center gap-1.5 border-b border-[#1a2a32] px-3 py-2">
              <span className="h-2.5 w-2.5 rounded-full bg-[#3a4550]" />
              <span className="h-2.5 w-2.5 rounded-full bg-[#3a4550]" />
              <span className="h-2.5 w-2.5 rounded-full bg-[#3a4550]" />
              <span className="ml-2 font-mono text-[10px] tracking-wide text-[#5a6a75]">
                install · remna-agent
              </span>
            </div>
            <pre
              ref={logRef}
              className="max-h-[min(50vh,420px)] min-h-[220px] overflow-auto px-3 py-3 font-mono text-[12px] leading-relaxed text-[#c8d4dc]"
            >
              {job.lines.length === 0 && active ? (
                <span className="text-[#5a6a75]">
                  {job.phase === "queued" ? "Ожидание слота…" : "Ожидание вывода…"}
                </span>
              ) : (
                job.lines.map((line, i) => (
                  <div
                    key={i}
                    className={
                      line.startsWith("✗")
                        ? "text-[var(--danger)]"
                        : line.startsWith("✓") || line.startsWith("Готово")
                          ? "text-[var(--success)]"
                          : line.startsWith("$")
                            ? "text-[var(--accent)]"
                            : line.startsWith("→")
                              ? "text-[#9eb0bc]"
                              : undefined
                    }
                  >
                    {line || "\u00a0"}
                  </div>
                ))
              )}
              {job.phase === "running" && (
                <div className="mt-1 inline-block h-3.5 w-1.5 animate-pulse bg-[var(--accent)] align-middle" />
              )}
            </pre>
          </div>
          {job.statusMessage && !active && (
            <p
              className={`mt-3 text-sm ${
                job.phase === "error" ? "text-[var(--danger)]" : "text-[var(--success)]"
              }`}
            >
              {job.statusMessage}
            </p>
          )}
        </div>

        <div className="flex flex-wrap justify-end gap-2 border-t border-[var(--border)] px-5 py-4">
          {active && (
            <>
              <button
                type="button"
                onClick={onClose}
                className="rounded-lg border border-[var(--border)] px-4 py-2 text-sm text-[var(--muted)] transition hover:text-[var(--text)]"
              >
                В фоне
              </button>
              <button
                type="button"
                onClick={onCancel}
                className="rounded-lg border border-[rgba(240,113,120,0.45)] bg-[rgba(240,113,120,0.1)] px-4 py-2 text-sm font-semibold text-[var(--danger)] transition hover:bg-[rgba(240,113,120,0.18)]"
              >
                Отменить
              </button>
            </>
          )}
          {job.phase === "error" && (
            <>
              <button
                type="button"
                onClick={() => {
                  onDismiss();
                  onClose();
                }}
                className="rounded-lg border border-[var(--border)] px-4 py-2 text-sm text-[var(--muted)] transition hover:text-[var(--text)]"
              >
                Закрыть
              </button>
              <button
                type="button"
                onClick={onRetry}
                className="rounded-lg bg-[var(--accent)] px-4 py-2 text-sm font-semibold text-[#06221e] transition hover:brightness-110"
              >
                Повторить
              </button>
            </>
          )}
          {job.phase === "done" && (
            <button
              type="button"
              onClick={() => {
                onDismiss();
                onClose();
              }}
              className="rounded-lg bg-[var(--accent)] px-4 py-2 text-sm font-semibold text-[#06221e] transition hover:brightness-110"
            >
              Готово
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
