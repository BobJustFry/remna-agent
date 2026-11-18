import { useState } from "react";
import { api, type MemWatchNode } from "../api/client";
import { ConfirmDialog } from "./ConfirmDialog";

/** Красная пометка «ПАМЯТЬ» у ноды в тревоге памятьсторожа; клик ведёт в раздел «Память». */
export function MemBadge({ info, compact }: { info?: MemWatchNode; compact?: boolean }) {
  if (!info?.alarm) return null;
  const title = [
    info.critical ? "КРИТИЧНО" : "Тревога по памяти",
    ...info.reasons,
    info.action_reason ? `Решение: ${info.action_reason}` : "",
  ]
    .filter(Boolean)
    .join("\n");
  return (
    <a
      href="/memory"
      title={title}
      onClick={(e) => e.stopPropagation()}
      className={[
        "shrink-0 rounded font-bold tracking-wide text-white",
        info.critical ? "ping-blink bg-[var(--danger)]" : "bg-[var(--warning)] !text-black",
        compact ? "px-1 py-px text-[8px]" : "px-1.5 py-0.5 text-[9px]",
      ].join(" ")}
    >
      ПАМЯТЬ
    </a>
  );
}

/** Ручной перезапуск ядра xray: с подтверждением; действие пишется в журнал памятьсторожа. */
export function MemRestartButton({
  nodeId,
  nodeName,
  rssMb,
  onDone,
  className,
}: {
  nodeId: string;
  nodeName: string;
  rssMb?: number | null;
  onDone?: () => void;
  className?: string;
}) {
  const [ask, setAsk] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  async function run() {
    setBusy(true);
    try {
      const r = await api.memWatchRestart(nodeId);
      setNote(r.message ?? "Перезапуск отправлен");
      onDone?.();
    } catch (err) {
      setNote(err instanceof Error ? err.message : "Не удалось перезапустить");
    } finally {
      setBusy(false);
      setAsk(false);
    }
  }

  return (
    <>
      <button
        type="button"
        onClick={() => {
          setNote(null);
          setAsk(true);
        }}
        title="Перезапустить ядро xray через панель Remnawave (forceRestart). Запишется в журнал."
        className={
          className ??
          "rounded border border-[var(--border)] px-2 py-1 text-[11px] text-[var(--muted)] hover:text-[var(--text)]"
        }
      >
        Перезапустить ядро
      </button>
      {note && <span className="text-[11px] text-[var(--muted)]">{note}</span>}
      <ConfirmDialog
        open={ask}
        title={`Перезапустить ядро на ${nodeName}?`}
        message={`Через панель Remnawave будет выполнен forceRestart ядра xray${
          rssMb != null ? ` (сейчас rw-core ${Math.round(rssMb)} МБ)` : ""
        }. Клиенты на ноде на несколько секунд потеряют соединение. Действие попадёт в журнал.`}
        confirmLabel="Перезапустить"
        busy={busy}
        busyLabel="Отправляю…"
        onConfirm={() => void run()}
        onCancel={() => setAsk(false)}
      />
    </>
  );
}
