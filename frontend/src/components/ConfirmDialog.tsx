type Props = {
  open: boolean;
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  busy?: boolean;
  busyLabel?: string;
  danger?: boolean;
  zClass?: string;
  onConfirm: () => void;
  onCancel: () => void;
};

export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = "Удалить",
  cancelLabel = "Отмена",
  busy,
  busyLabel = "Подождите…",
  danger = true,
  zClass = "z-[60]",
  onConfirm,
  onCancel,
}: Props) {
  if (!open) return null;

  const confirmCls = danger
    ? "rounded-lg border border-[rgba(240,113,120,0.45)] bg-[rgba(240,113,120,0.12)] px-4 py-2 text-sm font-semibold text-[var(--danger)] transition hover:bg-[rgba(240,113,120,0.2)] disabled:opacity-60"
    : "rounded-lg bg-[var(--accent)] px-4 py-2 text-sm font-semibold text-[#06221e] transition hover:brightness-110 disabled:opacity-60";

  return (
    <div className={`fixed inset-0 ${zClass} flex items-end justify-center bg-black/55 p-0 backdrop-blur-sm sm:items-center sm:p-4`}>
      <div
        role="dialog"
        aria-modal="true"
        className="w-full max-w-md rounded-t-[var(--radius)] border border-[var(--border)] bg-[var(--bg-elevated)] shadow-2xl sm:rounded-[var(--radius)]"
      >
        <div className="border-b border-[var(--border)] px-5 py-4">
          <h2 className="text-base font-semibold text-[var(--text)]">{title}</h2>
        </div>
        <div className="px-5 py-4 text-sm text-[var(--muted)]">{message}</div>
        <div className="flex flex-col-reverse gap-2 px-5 pb-[max(1rem,env(safe-area-inset-bottom,0px))] sm:flex-row sm:justify-end sm:pb-4">
          <button
            type="button"
            disabled={busy}
            onClick={onCancel}
            className="rounded-lg border border-[var(--border)] px-4 py-2.5 text-sm text-[var(--muted)] transition hover:text-[var(--text)] disabled:opacity-60 sm:py-2"
          >
            {cancelLabel}
          </button>
          <button type="button" disabled={busy} onClick={onConfirm} className={confirmCls}>
            {busy ? busyLabel : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
