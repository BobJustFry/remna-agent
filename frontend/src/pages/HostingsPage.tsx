import { useState, type FormEvent } from "react";
import { useOutletContext } from "react-router-dom";
import { api } from "../api/client";
import type { AppOutletContext } from "../components/AppShell";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { HostingLogo } from "../components/HostingLogo";
import type { HostingItem } from "../types";

export function HostingsPage() {
  const { hostings, reloadHostings, reloadNodes } = useOutletContext<AppOutletContext>();
  const [name, setName] = useState("");
  const [websiteUrl, setWebsiteUrl] = useState("");
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<HostingItem | null>(null);
  const [editName, setEditName] = useState("");
  const [editWebsiteUrl, setEditWebsiteUrl] = useState("");
  const [editNotes, setEditNotes] = useState("");
  const [pendingDelete, setPendingDelete] = useState<HostingItem | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);

  async function onCreate(e: FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await api.createHosting({
        name: name.trim(),
        website_url: websiteUrl.trim() || null,
        notes: notes.trim() || null,
      });
      setName("");
      setWebsiteUrl("");
      setNotes("");
      await reloadHostings();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка");
    } finally {
      setBusy(false);
    }
  }

  async function onSaveEdit() {
    if (!editing) return;
    setBusy(true);
    setError(null);
    try {
      await api.updateHosting(editing.id, {
        name: editName.trim(),
        website_url: editWebsiteUrl.trim() || null,
        notes: editNotes.trim() || null,
      });
      setEditing(null);
      await reloadHostings();
      await reloadNodes();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка");
    } finally {
      setBusy(false);
    }
  }

  async function onRefreshFavicon(item: HostingItem) {
    if (!item.website_url) return;
    setBusy(true);
    setError(null);
    try {
      await api.refreshHostingFavicon(item.id);
      await reloadHostings();
      await reloadNodes();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка");
    } finally {
      setBusy(false);
    }
  }

  async function confirmDelete() {
    if (!pendingDelete) return;
    setDeleteBusy(true);
    setError(null);
    try {
      await api.deleteHosting(pendingDelete.id);
      setPendingDelete(null);
      await reloadHostings();
      await reloadNodes();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка");
    } finally {
      setDeleteBusy(false);
    }
  }

  return (
    <div className="flex h-full flex-col">
      <header className="border-b border-[var(--border)] px-4 py-4 sm:px-6">
        <h1 className="text-xl font-semibold tracking-tight">Хостинги</h1>
        <p className="mt-0.5 text-sm text-[var(--muted)]">
          Справочник хостингов. По URL сайта подтягивается favicon как логотип.
        </p>
      </header>

      <div className="flex-1 space-y-4 overflow-auto px-3 py-3 sm:px-6 sm:py-4">
        <form
          onSubmit={(e) => void onCreate(e)}
          className="grid gap-3 rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-elevated)] p-4 md:grid-cols-[1fr_1.2fr_1fr_auto]"
        >
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Название"
            className={inputCls}
            required
          />
          <input
            value={websiteUrl}
            onChange={(e) => setWebsiteUrl(e.target.value)}
            placeholder="https://hosting.example"
            className={inputCls}
          />
          <input
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="Заметка"
            className={inputCls}
          />
          <button type="submit" disabled={busy} className={btnPrimary}>
            Добавить
          </button>
        </form>

        {error && (
          <div className="rounded-lg border border-[rgba(240,113,120,0.35)] bg-[rgba(240,113,120,0.08)] px-4 py-3 text-sm text-[var(--danger)]">
            {error}
          </div>
        )}

        <div className="space-y-2">
          {hostings.length === 0 && (
            <div className="rounded-[var(--radius)] border border-dashed border-[var(--border)] px-4 py-10 text-center text-sm text-[var(--muted)]">
              Хостингов пока нет
            </div>
          )}
          {hostings.map((item) => (
            <div
              key={item.id}
              className="rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-row)] px-4 py-3"
            >
              {editing?.id === item.id ? (
                <div className="grid gap-2 md:grid-cols-[1fr_1.2fr_1fr_auto_auto]">
                  <input value={editName} onChange={(e) => setEditName(e.target.value)} className={inputCls} />
                  <input
                    value={editWebsiteUrl}
                    onChange={(e) => setEditWebsiteUrl(e.target.value)}
                    placeholder="https://"
                    className={inputCls}
                  />
                  <input value={editNotes} onChange={(e) => setEditNotes(e.target.value)} className={inputCls} />
                  <button type="button" disabled={busy} onClick={() => void onSaveEdit()} className={btnPrimary}>
                    Сохранить
                  </button>
                  <button type="button" onClick={() => setEditing(null)} className={btnGhost}>
                    Отмена
                  </button>
                </div>
              ) : (
                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <HostingLogo name={item.name} faviconData={item.favicon_data} size={22} />
                      <div className="truncate font-medium text-[var(--text)]">{item.name}</div>
                      {item.website_url && (
                        <a
                          href={item.website_url}
                          target="_blank"
                          rel="noreferrer"
                          className="truncate text-xs text-[var(--accent)] hover:underline"
                          title={item.website_url}
                        >
                          {item.website_url.replace(/^https?:\/\//, "")}
                        </a>
                      )}
                    </div>
                    {item.notes && <div className="mt-1 truncate text-xs text-[var(--muted)]">{item.notes}</div>}
                  </div>
                  <div className="flex flex-wrap shrink-0 gap-2">
                    {item.website_url && (
                      <>
                        <a
                          href={item.website_url}
                          target="_blank"
                          rel="noreferrer"
                          className={btnGhost}
                        >
                          Открыть сайт
                        </a>
                        <button
                          type="button"
                          className={btnGhost}
                          disabled={busy}
                          onClick={() => void onRefreshFavicon(item)}
                          title="Обновить favicon"
                        >
                          Favicon
                        </button>
                      </>
                    )}
                    <button
                      type="button"
                      className={btnGhost}
                      onClick={() => {
                        setEditing(item);
                        setEditName(item.name);
                        setEditWebsiteUrl(item.website_url ?? "");
                        setEditNotes(item.notes ?? "");
                      }}
                    >
                      Изменить
                    </button>
                    <button
                      type="button"
                      className="rounded-lg border border-[rgba(240,113,120,0.35)] px-3 py-2 text-sm text-[var(--danger)]"
                      onClick={() => setPendingDelete(item)}
                    >
                      Удалить
                    </button>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      </div>

      <ConfirmDialog
        open={Boolean(pendingDelete)}
        title="Удалить хостинг?"
        message={
          pendingDelete
            ? `Хостинг «${pendingDelete.name}» будет удалён. У связанных нод поле хостинга станет пустым.`
            : ""
        }
        busy={deleteBusy}
        onCancel={() => {
          if (!deleteBusy) setPendingDelete(null);
        }}
        onConfirm={() => void confirmDelete()}
      />
    </div>
  );
}

const inputCls =
  "w-full rounded-lg border border-[var(--border)] bg-[var(--bg)] px-3 py-2 text-sm text-[var(--text)] outline-none transition focus:border-[var(--accent)]";

const btnPrimary =
  "rounded-lg bg-[var(--accent)] px-4 py-2 text-sm font-semibold text-[#06221e] transition hover:brightness-110 disabled:opacity-60";

const btnGhost =
  "rounded-lg border border-[var(--border)] px-3 py-2 text-sm text-[var(--muted)] transition hover:text-[var(--text)]";
