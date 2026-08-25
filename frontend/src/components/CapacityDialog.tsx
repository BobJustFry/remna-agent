import { useEffect, useState } from "react";
import { api, type VpsCapacity } from "../api/client";
import type { NodeItem } from "../types";

type Props = {
  node: NodeItem;
  onClose: () => void;
};

export function CapacityDialog({ node, onClose }: Props) {
  const [data, setData] = useState<VpsCapacity | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    void api
      .getCapacity(node.id)
      .then((res) => {
        if (cancelled) return;
        setData(res);
        if (res.error) setError(res.error);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Не удалось проверить VPS");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [node.id]);

  return (
    <div className="fixed inset-0 z-[70] flex items-end justify-center bg-black/60 p-0 backdrop-blur-sm sm:items-center sm:p-4">
      <div className="flex max-h-[min(100dvh,720px)] w-full max-w-lg flex-col overflow-hidden rounded-t-[var(--radius)] border border-[var(--border)] bg-[var(--bg-elevated)] shadow-2xl sm:rounded-[var(--radius)]">
        <div className="border-b border-[var(--border)] px-5 py-4">
          <h2 className="text-base font-semibold">Проверка VPS</h2>
          <p className="mt-1 font-mono text-xs text-[var(--muted)]">
            {node.name} · {node.host}
          </p>
        </div>

        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-5 py-4 text-sm">
          {loading && <p className="text-[var(--muted)]">Снимаю CPU, RAM, диск…</p>}

          {error && (
            <div className="rounded-lg border border-[rgba(240,113,120,0.35)] bg-[rgba(240,113,120,0.08)] px-3 py-2 text-xs text-[var(--danger)]">
              {error}
            </div>
          )}

          {data && !loading && !data.error && (
            <>
              <div className="grid grid-cols-3 gap-2">
                <Big n={data.comfort} label="комфорт онлайн" />
                <Big n={data.ceiling} label="потолок" />
                <Big n={data.panel_users} label="учёток в панели" />
              </div>
              <p className="text-[13px] leading-relaxed text-[var(--text)]">{data.summary}</p>
              <div className="grid gap-1 rounded-lg border border-[var(--border)] bg-[var(--bg)] px-3 py-2 text-[11px] text-[var(--muted)] sm:grid-cols-2">
                <div>ОС: {data.os || "—"}</div>
                <div>CPU: {data.cpu_cores} × {data.cpu_model || "—"}</div>
                <div>
                  RAM: {data.ram_total_mb} МБ (свободно {data.ram_avail_mb})
                </div>
                <div>
                  Диск: {data.disk_free_gb} / {data.disk_total_gb} ГБ
                </div>
                <div>Virt: {data.virt || "—"}</div>
                <div>Упирается в: {data.limiter}</div>
              </div>
              {data.notes.length > 0 && (
                <ul className="list-disc space-y-1 pl-4 text-[12px] text-[var(--muted)]">
                  {data.notes.map((note) => (
                    <li key={note}>{note}</li>
                  ))}
                </ul>
              )}
            </>
          )}
        </div>

        <div className="flex justify-end gap-2 border-t border-[var(--border)] px-5 py-4">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg bg-[var(--accent)] px-4 py-2 text-sm font-semibold text-[#06221e] hover:brightness-110"
          >
            Закрыть
          </button>
        </div>
      </div>
    </div>
  );
}

function Big({ n, label }: { n: number; label: string }) {
  return (
    <div className="rounded-lg border border-[var(--border)] bg-[var(--bg)] px-2 py-2 text-center">
      <div className="text-xl font-semibold tabular-nums text-[var(--accent)]">{n}</div>
      <div className="text-[10px] uppercase tracking-wide text-[var(--muted)]">{label}</div>
    </div>
  );
}
