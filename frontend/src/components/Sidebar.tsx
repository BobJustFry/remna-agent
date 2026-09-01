import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";

type Props = {
  username: string;
  onLogout: () => void;
  open: boolean;
  onClose: () => void;
};

export function Sidebar({ username, onLogout, open, onClose }: Props) {
  return (
    <>
      {open && (
        <button
          type="button"
          aria-label="Закрыть меню"
          className="fixed inset-0 z-40 bg-black/55 backdrop-blur-[2px] lg:hidden"
          onClick={onClose}
        />
      )}
      <aside
        className={[
          "fixed inset-y-0 left-0 z-50 flex w-[min(220px,85vw)] shrink-0 flex-col border-r border-[var(--border)] bg-[var(--sidebar)] pt-[env(safe-area-inset-top,0px)] pb-[env(safe-area-inset-bottom,0px)] transition-transform duration-200 ease-out",
          "lg:static lg:z-auto lg:w-[220px] lg:translate-x-0 lg:pt-0 lg:pb-0",
          open ? "translate-x-0" : "-translate-x-full",
        ].join(" ")}
      >
        <div className="flex items-start justify-between border-b border-[var(--border)] px-5 py-5">
          <div>
            <div className="text-lg font-semibold tracking-tight text-[var(--text)]">Remna Agent</div>
            <div className="mt-1 text-xs text-[var(--muted)]">Управление нодами</div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-1 text-[var(--muted)] hover:text-[var(--text)] lg:hidden"
            aria-label="Закрыть"
          >
            ✕
          </button>
        </div>

        <nav className="flex flex-1 flex-col gap-4 px-3 py-4">
          <NavSection title="Основное">
            <NavItem to="/" end onNavigate={onClose}>
              Дашборд
            </NavItem>
            <NavItem to="/nodes" onNavigate={onClose}>
              Ноды
            </NavItem>
            <NavItem to="/hostings" onNavigate={onClose}>
              Хостинги
            </NavItem>
          </NavSection>

          <NavSection title="Управление">
            <NavItem to="/scripts" onNavigate={onClose}>
              Скрипты
            </NavItem>
            <NavItem to="/settings" onNavigate={onClose}>
              Настройки
            </NavItem>
          </NavSection>
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
    </>
  );
}

function NavSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div>
      <div className="-ml-1 px-1 pb-1.5 text-[11px] font-bold uppercase tracking-[0.12em] text-[var(--text)]/70">
        {title}
      </div>
      <div className="flex flex-col gap-0.5 pl-1.5">{children}</div>
    </div>
  );
}

function NavItem({
  to,
  end,
  children,
  onNavigate,
}: {
  to: string;
  end?: boolean;
  children: ReactNode;
  onNavigate: () => void;
}) {
  return (
    <NavLink
      to={to}
      end={end}
      onClick={onNavigate}
      className={({ isActive }) =>
        [
          "flex items-center gap-2 rounded-md px-2.5 py-1.5 text-[13px] leading-snug transition",
          isActive
            ? "border-l-2 border-[var(--accent)] bg-[var(--accent-dim)] font-medium text-[var(--accent)]"
            : "border-l-2 border-transparent text-[var(--muted)] hover:bg-[var(--bg-row)] hover:text-[var(--text)]",
        ].join(" ")
      }
    >
      {children}
    </NavLink>
  );
}
