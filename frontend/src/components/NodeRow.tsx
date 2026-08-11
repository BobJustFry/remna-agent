import { memo, useState } from "react";
import type { NodeItem, OnlineStatus } from "../types";
import { api } from "../api/client";
import { CopyButton } from "./CopyButton";
import { OnlineBadge } from "./OnlineBadge";

type Props = {
  node: NodeItem;
  status?: OnlineStatus;
  onEdit: (node: NodeItem) => void;
  onDelete: (node: NodeItem) => void;
};

export const NodeRow = memo(function NodeRow({ node, status, onEdit, onDelete }: Props) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [copyingSecret, setCopyingSecret] = useState(false);

  async function copySecret() {
    setCopyingSecret(true);
    try {
      const data = await api.getSecret(node.id);
      await navigator.clipboard.writeText(data.secret);
    } catch {
      // ignore
    } finally {
      setCopyingSecret(false);
      setMenuOpen(false);
    }
  }

  return (
    <div className="grid grid-cols-[140px_minmax(180px,1.2fr)_minmax(160px,1fr)_110px_90px_44px] items-center gap-3 rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-row)] px-4 py-3 transition hover:bg-[var(--bg-row-hover)]">
      <OnlineBadge status={status} />

      <div className="min-w-0">
        <div className="flex items-center gap-2">
          {node.country_code && (
            <span className="rounded bg-[var(--accent-dim)] px-1.5 py-0.5 text-[10px] font-semibold text-[var(--accent)]">
              {node.country_code}
            </span>
          )}
          <div className="truncate font-medium text-[var(--text)]">{node.name}</div>
        </div>
        <div className="mt-0.5 truncate text-xs text-[var(--muted)]">
          {node.ssh_user}@{node.host}:{node.ssh_port}
        </div>
      </div>

      <div className="flex min-w-0 items-center gap-2">
        {node.provider && (
          <span className="shrink-0 rounded-full border border-[rgba(34,211,187,0.35)] bg-[var(--accent-dim)] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-[var(--accent)]">
            {node.provider}
          </span>
        )}
        <span className="truncate font-mono text-sm text-[var(--text)]">{node.host}</span>
        <CopyButton value={node.host} title="Копировать IP / host" />
      </div>

      <div className="text-xs text-[var(--muted)]">
        {node.auth_type === "password" ? "Пароль" : "SSH ключ"}
        {(node.has_password || node.has_private_key) && (
          <button
            type="button"
            onClick={() => void copySecret()}
            disabled={copyingSecret}
            className="ml-2 inline-flex align-middle text-[var(--accent)] hover:underline disabled:opacity-50"
            title="Копировать секрет"
          >
            <CopyIcon />
          </button>
        )}
      </div>

      <button
        type="button"
        disabled
        title="Скоро"
        className="cursor-not-allowed rounded-md border border-[var(--border)] px-2 py-1 text-[11px] text-[var(--muted)] opacity-50"
      >
        Установка
      </button>

      <div className="relative justify-self-end">
        <button
          type="button"
          onClick={() => setMenuOpen((v) => !v)}
          className="inline-flex h-8 w-8 items-center justify-center rounded-md text-[var(--muted)] hover:bg-[var(--accent-dim)] hover:text-[var(--text)]"
          aria-label="Меню"
        >
          ⋮
        </button>
        {menuOpen && (
          <>
            <div className="fixed inset-0 z-10" onClick={() => setMenuOpen(false)} />
            <div className="absolute right-0 z-20 mt-1 w-36 overflow-hidden rounded-lg border border-[var(--border)] bg-[var(--bg-elevated)] py-1 shadow-xl">
              <button
                type="button"
                className="block w-full px-3 py-2 text-left text-sm hover:bg-[var(--accent-dim)]"
                onClick={() => {
                  setMenuOpen(false);
                  onEdit(node);
                }}
              >
                Редактировать
              </button>
              <button
                type="button"
                className="block w-full px-3 py-2 text-left text-sm text-[var(--danger)] hover:bg-[rgba(240,113,120,0.08)]"
                onClick={() => {
                  setMenuOpen(false);
                  onDelete(node);
                }}
              >
                Удалить
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
});

function CopyIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="inline">
      <rect x="9" y="9" width="13" height="13" rx="2" />
      <path d="M5 15V5a2 2 0 0 1 2-2h10" />
    </svg>
  );
}
