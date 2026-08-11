import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import { NodeForm } from "../components/NodeForm";
import { NodeRow } from "../components/NodeRow";
import { Sidebar } from "../components/Sidebar";
import { useNodesOnline } from "../hooks/useNodesOnline";
import type { NodeFormValues, NodeItem } from "../types";

type Props = {
  username: string;
  onLogout: () => void;
};

export function NodesPage({ username, onLogout }: Props) {
  const [nodes, setNodes] = useState<NodeItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<NodeItem | null>(null);
  const [formBusy, setFormBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  const { statuses } = useNodesOnline(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.listNodes();
      setNodes(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить ноды");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const filtered = nodes.filter((n) => {
    const q = query.trim().toLowerCase();
    if (!q) return true;
    return (
      n.name.toLowerCase().includes(q) ||
      n.host.toLowerCase().includes(q) ||
      (n.provider ?? "").toLowerCase().includes(q)
    );
  });

  async function handleSubmit(values: NodeFormValues) {
    setFormBusy(true);
    setFormError(null);
    try {
      if (editing) {
        const updated = await api.updateNode(editing.id, values);
        setNodes((prev) => prev.map((n) => (n.id === updated.id ? updated : n)));
      } else {
        const created = await api.createNode(values);
        setNodes((prev) => [...prev, created].sort((a, b) => a.name.localeCompare(b.name)));
      }
      setFormOpen(false);
      setEditing(null);
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Ошибка сохранения");
    } finally {
      setFormBusy(false);
    }
  }

  async function handleDelete(node: NodeItem) {
    if (!window.confirm(`Удалить ноду «${node.name}»?`)) return;
    try {
      await api.deleteNode(node.id);
      setNodes((prev) => prev.filter((n) => n.id !== node.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось удалить");
    }
  }

  return (
    <div className="flex min-h-screen">
      <Sidebar username={username} onLogout={onLogout} />

      <main className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between border-b border-[var(--border)] px-6 py-4">
          <div>
            <h1 className="text-xl font-semibold tracking-tight">Ноды</h1>
            <p className="mt-0.5 text-sm text-[var(--muted)]">
              Список серверов Remnawave. Online обновляется каждые 5 сек.
            </p>
          </div>
          <button
            type="button"
            onClick={() => {
              setEditing(null);
              setFormError(null);
              setFormOpen(true);
            }}
            className="rounded-lg bg-[var(--accent)] px-4 py-2 text-sm font-semibold text-[#06221e] transition hover:brightness-110"
          >
            + Добавить
          </button>
        </header>

        <div className="flex items-center gap-3 border-b border-[var(--border)] px-6 py-3">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Поиск по имени, IP, провайдеру…"
            className="w-full max-w-md rounded-lg border border-[var(--border)] bg-[var(--bg-elevated)] px-3 py-2 text-sm outline-none focus:border-[var(--accent)]"
          />
          <button
            type="button"
            onClick={() => void load()}
            className="rounded-lg border border-[var(--border)] px-3 py-2 text-sm text-[var(--muted)] hover:text-[var(--text)]"
          >
            Обновить
          </button>
        </div>

        <div className="flex-1 space-y-2 overflow-auto px-6 py-4">
          <div className="grid grid-cols-[140px_minmax(180px,1.2fr)_minmax(160px,1fr)_110px_90px_44px] gap-3 px-4 text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--muted)]">
            <div>Online</div>
            <div>Нода</div>
            <div>Host</div>
            <div>Доступ</div>
            <div>Действия</div>
            <div />
          </div>

          {loading && <div className="px-4 py-8 text-sm text-[var(--muted)]">Загрузка…</div>}
          {error && (
            <div className="rounded-lg border border-[rgba(240,113,120,0.35)] bg-[rgba(240,113,120,0.08)] px-4 py-3 text-sm text-[var(--danger)]">
              {error}
            </div>
          )}
          {!loading && !error && filtered.length === 0 && (
            <div className="rounded-[var(--radius)] border border-dashed border-[var(--border)] px-4 py-10 text-center text-sm text-[var(--muted)]">
              Нод пока нет. Добавьте первую.
            </div>
          )}

          {filtered.map((node) => (
            <NodeRow
              key={node.id}
              node={node}
              status={statuses[node.id]}
              onEdit={(n) => {
                setEditing(n);
                setFormError(null);
                setFormOpen(true);
              }}
              onDelete={(n) => void handleDelete(n)}
            />
          ))}
        </div>
      </main>

      <NodeForm
        open={formOpen}
        initial={editing}
        busy={formBusy}
        error={formError}
        onClose={() => {
          setFormOpen(false);
          setEditing(null);
        }}
        onSubmit={handleSubmit}
      />
    </div>
  );
}
