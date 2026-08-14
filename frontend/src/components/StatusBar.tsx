import type { RemnawaveVersions } from "../api/client";
import type { NodeItem, OnlineMap } from "../types";

type Props = {
  nodes: NodeItem[];
  statuses: OnlineMap;
  remnawaveVersions?: RemnawaveVersions | null;
  remnawaveLoading?: boolean;
  onRefreshVersions?: () => void;
};

export function StatusBar({
  nodes,
  statuses,
  remnawaveVersions,
  remnawaveLoading,
  onRefreshVersions,
}: Props) {
  let online = 0;
  let offline = 0;
  let pending = 0;

  for (const node of nodes) {
    const st = statuses[node.id];
    if (!st) {
      pending += 1;
    } else if (st.online) {
      online += 1;
    } else {
      offline += 1;
    }
  }

  return (
    <footer className="flex min-h-9 shrink-0 flex-wrap items-center gap-x-4 gap-y-1 border-t border-[var(--border)] bg-[var(--sidebar)] px-3 py-2 text-xs text-[var(--muted)] sm:px-4">
      <span>
        Нод: <span className="text-[var(--text)]">{nodes.length}</span>
      </span>
      <span className="text-[var(--success)]">
        Online: <span className="font-semibold">{online}</span>
      </span>
      <span className="text-[var(--danger)]">
        Offline: <span className="font-semibold">{offline}</span>
      </span>
      {pending > 0 && <span>Проверка…: {pending}</span>}

      {(remnawaveVersions || remnawaveLoading) && (
        <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-1 sm:ml-auto">
          <span>
            Panel{" "}
            <span className="font-semibold text-[var(--text)]">
              {remnawaveVersions?.panel_version ? `v${remnawaveVersions.panel_version}` : "—"}
            </span>
            {" · "}
            Node{" "}
            <span className="font-semibold text-[var(--text)]">
              {remnawaveVersions?.node_version ? `v${remnawaveVersions.node_version}` : "—"}
            </span>
          </span>
          {onRefreshVersions && (
            <button
              type="button"
              onClick={onRefreshVersions}
              disabled={remnawaveLoading}
              className="rounded border border-[var(--border)] px-1.5 py-0.5 text-[10px] hover:text-[var(--accent)] disabled:opacity-50"
              title="Проверить версии на GitHub"
            >
              {remnawaveLoading ? "…" : "Проверить"}
            </button>
          )}
          {remnawaveVersions?.error && (
            <span className="text-[var(--warning)]" title={remnawaveVersions.error}>
              GH err
            </span>
          )}
        </span>
      )}
    </footer>
  );
}
