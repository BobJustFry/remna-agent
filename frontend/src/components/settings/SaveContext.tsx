import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
  type ReactNode,
} from "react";

/** Блок настроек, зарегистрированный в общей кнопке «Сохранить». */
export type BlockEntry = {
  label: string;
  /** Вкладка, на которой лежит блок (для пометки несохранённого на вкладке). */
  tab: string;
  /** Сколько полей изменено и не сохранено. */
  dirty: number;
  /** Сохранить изменённое. При ошибке должна бросить исключение. */
  save: () => Promise<void>;
  /** Откатить правки к сохранённому. */
  reset: () => void;
};

export type SaveResult = { label: string; ok: boolean; error?: string };

type Ctx = {
  upsert: (id: string, entry: BlockEntry) => void;
  remove: (id: string) => void;
  totalDirty: number;
  dirtyByTab: Record<string, number>;
  saving: boolean;
  results: SaveResult[] | null;
  saveAll: () => Promise<void>;
  resetAll: () => void;
};

const SaveCtx = createContext<Ctx | null>(null);

export function SettingsSaveProvider({ children }: { children: ReactNode }) {
  const entries = useRef(new Map<string, BlockEntry>());
  const [, bump] = useReducer((x: number) => x + 1, 0);
  const [saving, setSaving] = useState(false);
  const [results, setResults] = useState<SaveResult[] | null>(null);

  const upsert = useCallback((id: string, entry: BlockEntry) => {
    const prev = entries.current.get(id);
    entries.current.set(id, entry);
    if (!prev || prev.dirty !== entry.dirty || prev.label !== entry.label || prev.tab !== entry.tab) bump();
  }, []);

  const remove = useCallback((id: string) => {
    if (entries.current.delete(id)) bump();
  }, []);

  let totalDirty = 0;
  const dirtyByTab: Record<string, number> = {};
  for (const e of entries.current.values()) {
    totalDirty += e.dirty;
    if (e.dirty > 0) dirtyByTab[e.tab] = (dirtyByTab[e.tab] ?? 0) + e.dirty;
  }

  const saveAll = useCallback(async () => {
    setSaving(true);
    setResults(null);
    const out: SaveResult[] = [];
    try {
      // Последовательно; упавший блок не мешает остальным, сохранённое не откатываем.
      for (const id of [...entries.current.keys()]) {
        const e = entries.current.get(id);
        if (!e || e.dirty <= 0) continue;
        try {
          await e.save();
          out.push({ label: e.label, ok: true });
        } catch (err) {
          out.push({
            label: e.label,
            ok: false,
            error: err instanceof Error ? err.message : "Не удалось сохранить",
          });
        }
      }
    } finally {
      setResults(out);
      setSaving(false);
    }
  }, []);

  const resetAll = useCallback(() => {
    for (const e of entries.current.values()) if (e.dirty > 0) e.reset();
    setResults(null);
  }, []);

  // Закрытие/перезагрузка вкладки с несохранённым.
  useEffect(() => {
    if (totalDirty <= 0) return;
    const onBeforeUnload = (ev: BeforeUnloadEvent) => {
      ev.preventDefault();
      ev.returnValue = "";
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [totalDirty > 0]); // eslint-disable-line react-hooks/exhaustive-deps

  const value = useMemo<Ctx>(
    () => ({ upsert, remove, totalDirty, dirtyByTab, saving, results, saveAll, resetAll }),
    // dirtyByTab пересоздаётся на каждый рендер; значение пересобираем по его содержимому.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [upsert, remove, totalDirty, JSON.stringify(dirtyByTab), saving, results, saveAll, resetAll],
  );

  return <SaveCtx.Provider value={value}>{children}</SaveCtx.Provider>;
}

export function useSettingsSave(): Ctx {
  const c = useContext(SaveCtx);
  if (!c) throw new Error("useSettingsSave вне SettingsSaveProvider");
  return c;
}

/** Регистрирует блок в общем сохранении; вызывать в каждом рендере с актуальными замыканиями. */
export function useSettingsBlock(id: string, entry: BlockEntry) {
  const { upsert, remove } = useSettingsSave();
  useEffect(() => {
    upsert(id, entry);
  });
  useEffect(() => () => remove(id), [id, remove]);
}
