import { useSettingsSave } from "./SaveContext";

/** Закреплённая внизу панель: одна кнопка «Сохранить» на всю страницу. */
export function SaveBar() {
  const { totalDirty, saving, results, saveAll, resetAll } = useSettingsSave();
  const failed = results?.filter((r) => !r.ok) ?? [];
  const okCount = results?.filter((r) => r.ok).length ?? 0;

  return (
    <div className="sticky bottom-0 z-20 shrink-0 border-t border-[var(--border)] bg-[var(--bg-elevated)] px-3 py-2 sm:px-6">
      <div className="mx-auto flex max-w-3xl flex-wrap items-center gap-x-3 gap-y-2">
        <span
          className={`text-xs ${totalDirty > 0 ? "text-[var(--warning)]" : "text-[var(--muted)]"}`}
          role="status"
        >
          {totalDirty > 0 ? `Есть несохранённые изменения (${totalDirty})` : "Нет изменений"}
        </span>
        <div className="ml-auto flex gap-2">
          <button
            type="button"
            disabled={saving || totalDirty === 0}
            onClick={resetAll}
            className="rounded-lg border border-[var(--border)] px-3 py-1.5 text-sm text-[var(--text)] hover:bg-[var(--bg-row)] disabled:opacity-50"
          >
            Отменить
          </button>
          <button
            type="button"
            disabled={saving || totalDirty === 0}
            onClick={() => void saveAll()}
            className="rounded-lg bg-[var(--accent)] px-4 py-1.5 text-sm font-semibold text-[#06221e] disabled:opacity-50"
          >
            {saving ? "Сохранение…" : "Сохранить"}
          </button>
        </div>
        {results && (
          <div className="basis-full text-xs">
            {failed.length === 0 ? (
              okCount > 0 && <p className="text-[var(--success)]">Сохранено: {okCount}</p>
            ) : (
              <>
                {okCount > 0 && <p className="text-[var(--success)]">Сохранено блоков: {okCount}</p>}
                <ul className="mt-0.5 space-y-0.5 text-[var(--danger)]">
                  {failed.map((r) => (
                    <li key={r.label}>
                      Не сохранён «{r.label}»: {r.error}
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
