import { memo, useState, type MouseEvent } from "react";

type Props = {
  value: string;
  title?: string;
};

export const CopyButton = memo(function CopyButton({ value, title = "Копировать" }: Props) {
  const [ok, setOk] = useState(false);

  async function onCopy(e: MouseEvent) {
    e.stopPropagation();
    try {
      await navigator.clipboard.writeText(value);
      setOk(true);
      window.setTimeout(() => setOk(false), 1200);
    } catch {
      // ignore
    }
  }

  return (
    <button
      type="button"
      onClick={onCopy}
      title={ok ? "Скопировано" : title}
      className="inline-flex h-7 w-7 items-center justify-center rounded-md border border-transparent text-[var(--muted)] transition hover:border-[var(--border)] hover:bg-[var(--accent-dim)] hover:text-[var(--accent)]"
      aria-label={title}
    >
      {ok ? (
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M20 6 9 17l-5-5" />
        </svg>
      ) : (
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <rect x="9" y="9" width="13" height="13" rx="2" />
          <path d="M5 15V5a2 2 0 0 1 2-2h10" />
        </svg>
      )}
    </button>
  );
});
