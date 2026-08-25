import { useEffect, useRef } from "react";
import type { ScriptJob } from "../hooks/useScriptQueue";

type Props = {
  job: ScriptJob;
  onClose: () => void;
  onCancel: () => void;
  onDismiss: () => void;
};

export function ScriptJobLogDialog({ job, onClose, onCancel, onDismiss }: Props) {
  const logRef = useRef<HTMLPreElement>(null);
  const active = job.phase === "queued" || job.phase === "running";

  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [job.lines]);

  const actionLabel =
    job.action === "warp"
      ? "Установка WARP"
      : job.action === "install"
        ? "Установка RemnaNode"
        : job.action === "reinstall"
          ? "Переустановка"
          : job.action === "update"
            ? "Обновление RemnaNode"
            : "Параметры";

  return (
    <div className="fixed inset-0 z-[60] flex items-end justify-center bg-black/60 p-0 backdrop-blur-sm sm:items-center sm:p-4">
      <div className="flex max-h-[min(100dvh,860px)] w-full max-w-2xl flex-col overflow-hidden rounded-t-[var(--radius)] border border-[var(--border)] bg-[var(--bg-elevated)] shadow-2xl sm:rounded-[var(--radius)]">
        <div className="flex items-start justify-between gap-3 border-b border-[var(--border)] px-5 py-4">
          <div className="min-w-0">
            <h2 className="text-base font-semibold">
              {job.phase === "done" ? "Готово" : job.phase === "error" ? "Ошибка" : `${actionLabel}…`}
            </h2>
            <p className="mt-1 truncate font-mono text-xs text-[var(--muted)]">
              {job.nodeName} · {job.sshLabel}
            </p>
          </div>
        </div>
        <div className="px-5 py-4">
          <pre
            ref={logRef}
            className="max-h-[min(50vh,420px)] min-h-[220px] overflow-auto rounded-lg border border-[#1a2a32] bg-[#070c0f] px-3 py-3 font-mono text-[12px] leading-relaxed text-[#c8d4dc]"
          >
            {job.lines.length === 0 && active ? (
              <span className="text-[#5a6a75]">{job.phase === "queued" ? "В очереди…" : "Ожидание…"}</span>
            ) : (
              job.lines.map((line, i) => (
                <div
                  key={i}
                  className={
                    line.startsWith("✗")
                      ? "text-[var(--danger)]"
                      : line.startsWith("✓")
                        ? "text-[var(--success)]"
                        : line.startsWith("$") || line.startsWith("→")
                          ? "text-[var(--accent)]"
                          : undefined
                  }
                >
                  {line || "\u00a0"}
                </div>
              ))
            )}
          </pre>
        </div>
        <div className="flex flex-wrap justify-end gap-2 border-t border-[var(--border)] px-5 py-4">
          {active ? (
            <>
              <button type="button" onClick={onClose} className={btnGhost}>
                В фоне
              </button>
              <button type="button" onClick={onCancel} className={btnDanger}>
                Отменить
              </button>
            </>
          ) : (
            <button
              type="button"
              onClick={() => {
                onDismiss();
                onClose();
              }}
              className={btnPrimary}
            >
              Закрыть
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

const btnPrimary =
  "rounded-lg bg-[var(--accent)] px-4 py-2 text-sm font-semibold text-[#06221e] hover:brightness-110";
const btnGhost =
  "rounded-lg border border-[var(--border)] px-4 py-2 text-sm text-[var(--muted)] hover:text-[var(--text)]";
const btnDanger =
  "rounded-lg border border-[rgba(240,113,120,0.45)] bg-[rgba(240,113,120,0.1)] px-4 py-2 text-sm font-semibold text-[var(--danger)]";
