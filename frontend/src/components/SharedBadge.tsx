import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { SharingUserHit } from "../types";

export function SharedBadge({
  nodeId,
  hits,
  compact,
}: {
  nodeId: string;
  hits: SharingUserHit[];
  compact?: boolean;
}) {
  const [open, setOpen] = useState(false);
  if (!hits.length) return null;
  const n = hits.length;
  const title = hits
    .map((h) => `${h.username || h.user_id}: ${h.conc_ips} IP одновременно, ${h.conc_nets} операторов`)
    .join("\n");
  return (
    <>
      <button
        type="button"
        title={title}
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          setOpen(true);
        }}
        className={[
          "ping-blink shrink-0 rounded font-bold tracking-wide text-white",
          "bg-[var(--danger)] hover:brightness-110",
          compact ? "px-1 py-px text-[8px]" : "px-1.5 py-0.5 text-[9px]",
        ].join(" ")}
      >
        SHARED{n > 1 ? ` ${n}` : ""}
      </button>
      {open && (
        <SharingDossierModal nodeId={nodeId} hits={hits} onClose={() => setOpen(false)} />
      )}
    </>
  );
}

function SharingDossierModal({
  nodeId,
  hits,
  onClose,
}: {
  nodeId: string;
  hits: SharingUserHit[];
  onClose: () => void;
}) {
  const [text, setText] = useState("загрузка…");
  const [filename, setFilename] = useState("sharing.txt");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .sharingDossier(nodeId)
      .then((d) => {
        if (cancelled) return;
        setText(d.text);
        setFilename(d.filename);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "не удалось собрать досье");
        setText("");
      });
    return () => {
      cancelled = true;
    };
  }, [nodeId]);

  function download() {
    const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div
      className="fixed inset-0 z-[80] flex items-end justify-center bg-black/65 p-0 backdrop-blur-[2px] sm:items-center sm:p-4"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        className="flex max-h-[min(96dvh,100%)] w-full max-w-[720px] flex-col overflow-hidden rounded-t-xl border border-[var(--border)] bg-[var(--bg-elevated)] shadow-2xl sm:rounded-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex shrink-0 items-center gap-2 border-b border-[var(--border)] px-4 py-2.5">
          <span className="rounded bg-[var(--danger)] px-1.5 py-0.5 text-[10px] font-bold text-white">
            SHARED
          </span>
          <h2 className="text-sm font-semibold">{filename}</h2>
          <div className="ml-auto flex gap-1">
            <button
              type="button"
              onClick={download}
              disabled={!text || !!error}
              className="rounded border border-[var(--border)] px-2 py-1 text-xs text-[var(--accent)] hover:bg-[var(--accent-dim)] disabled:opacity-40"
            >
              Скачать
            </button>
            <button
              type="button"
              onClick={onClose}
              className="rounded border border-[var(--border)] px-2 py-1 text-xs text-[var(--muted)] hover:text-[var(--text)]"
            >
              Закрыть
            </button>
          </div>
        </header>
        <div className="border-b border-[var(--border)] px-4 py-1.5 text-[11px] text-[var(--muted)]">
          {hits.map((h) => (
            <span key={h.user_id} className="mr-3">
              {h.username || h.user_id}: {h.conc_ips} IP одновременно, {h.conc_nets} оп.
            </span>
          ))}
        </div>
        <pre className="min-h-0 flex-1 overflow-auto px-4 py-3 font-mono text-[11px] leading-snug text-[var(--text)]">
          {error || text}
        </pre>
      </div>
    </div>
  );
}
