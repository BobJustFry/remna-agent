type Props = {
  username: string;
  onLogout: () => void;
};

export function Sidebar({ username, onLogout }: Props) {
  return (
    <aside className="flex w-[220px] shrink-0 flex-col border-r border-[var(--border)] bg-[var(--sidebar)]">
      <div className="border-b border-[var(--border)] px-5 py-5">
        <div className="text-lg font-semibold tracking-tight text-[var(--text)]">Remna Agent</div>
        <div className="mt-1 text-xs text-[var(--muted)]">Управление нодами</div>
      </div>

      <nav className="flex flex-1 flex-col gap-1 p-3">
        <div className="px-3 pb-2 pt-1 text-[10px] font-semibold uppercase tracking-[0.14em] text-[var(--muted)]">
          Управление
        </div>
        <a
          href="/"
          className="flex items-center gap-2 rounded-lg border-l-2 border-[var(--accent)] bg-[var(--accent-dim)] px-3 py-2 text-sm font-medium text-[var(--accent)]"
        >
          <span className="inline-flex h-5 w-5 items-center justify-center rounded bg-[rgba(34,211,187,0.2)] text-[11px]">
            N
          </span>
          Ноды
        </a>
        <button
          type="button"
          disabled
          className="flex cursor-not-allowed items-center gap-2 rounded-lg px-3 py-2 text-left text-sm text-[var(--muted)] opacity-50"
          title="Скоро"
        >
          Установка
        </button>
      </nav>

      <div className="border-t border-[var(--border)] p-4">
        <div className="truncate text-sm text-[var(--text)]">{username}</div>
        <button
          type="button"
          onClick={onLogout}
          className="mt-2 text-xs text-[var(--muted)] transition hover:text-[var(--accent)]"
        >
          Выйти
        </button>
      </div>
    </aside>
  );
}
