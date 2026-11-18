import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

export const INPUT =
  "w-full rounded-lg border border-[var(--border)] bg-[var(--bg)] px-3 py-2 text-sm text-[var(--text)] outline-none focus:border-[var(--accent)]";
export const BTN =
  "rounded-lg border border-[var(--border)] px-3 py-1.5 text-sm text-[var(--text)] hover:bg-[var(--bg-row)] disabled:opacity-50";

export function Card({
  title,
  right,
  children,
}: {
  title: string;
  right?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-elevated)] p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-sm font-semibold">{title}</h2>
        {right}
      </div>
      <div className="mt-3 space-y-3">{children}</div>
    </section>
  );
}

/** Подсказка «?»: длинное пояснение не занимает место на странице. */
export function Hint({ children }: { children: ReactNode }) {
  return (
    <span className="group relative inline-flex align-middle">
      <span
        tabIndex={0}
        aria-label="Пояснение"
        className="ml-1 inline-flex h-4 w-4 cursor-help items-center justify-center rounded-full border border-[var(--border)] text-[10px] text-[var(--muted)] outline-none focus:border-[var(--accent)]"
      >
        ?
      </span>
      <span className="pointer-events-none absolute left-0 top-5 z-30 hidden w-64 max-w-[80vw] rounded-lg border border-[var(--border)] bg-[var(--bg-elevated)] p-2 text-xs font-normal normal-case leading-relaxed text-[var(--text)] shadow-lg group-focus-within:block group-hover:block">
        {children}
      </span>
    </span>
  );
}

/** Подпись поля с необязательной подсказкой. */
export function Label({ text, hint }: { text: string; hint?: ReactNode }) {
  return (
    <span className="text-xs text-[var(--muted)]">
      {text}
      {hint ? <Hint>{hint}</Hint> : null}
    </span>
  );
}

/** Сворачиваемый блок «Дополнительно» (свёрнут по умолчанию). */
export function Advanced({
  title = "Дополнительно",
  badge,
  children,
}: {
  title?: string;
  badge?: ReactNode;
  children: ReactNode;
}) {
  return (
    <details className="group rounded-lg border border-[var(--border)]">
      <summary className="cursor-pointer select-none px-3 py-2 text-xs text-[var(--muted)] hover:text-[var(--text)]">
        {title}
        {badge ? <span className="ml-2 text-[var(--accent)]">{badge}</span> : null}
      </summary>
      <div className="space-y-3 border-t border-[var(--border)] p-3">{children}</div>
    </details>
  );
}

export function Toggle({
  checked,
  onChange,
  label,
  hint,
  disabled,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
  hint?: ReactNode;
  disabled?: boolean;
}) {
  return (
    <label className="flex items-center gap-2 text-sm">
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
        className="h-4 w-4 accent-[var(--accent)]"
      />
      <span>
        {label}
        {hint ? <Hint>{hint}</Hint> : null}
      </span>
    </label>
  );
}

export function StatusToggle({
  checked,
  onChange,
  disabled,
  on = "включена",
  off = "выключена",
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
  on?: string;
  off?: string;
}) {
  return (
    <label className="flex shrink-0 items-center gap-2 text-sm">
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
        className="h-4 w-4 accent-[var(--accent)]"
      />
      <span className={checked ? "text-[var(--success)]" : "text-[var(--muted)]"}>
        {checked ? on : off}
      </span>
    </label>
  );
}

export function Grid({ children }: { children: ReactNode }) {
  return <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">{children}</div>;
}

export type Scalar = string | number | boolean;

/**
 * Черновик формы поверх сохранённого значения. Когда `saved` меняется (после сохранения
 * или перезагрузки) — черновик сбрасывается к нему. `saved` должен быть мемоизирован.
 */
export function useDraft<T extends Record<string, Scalar>>(saved: T | null) {
  const [draft, setDraft] = useState<T | null>(saved);
  const prev = useRef<T | null>(saved);
  useEffect(() => {
    // Подтягиваем только те поля, у которых изменилось сохранённое значение: правки в полях,
    // которые не удалось сохранить, при этом не теряются.
    const before = prev.current;
    prev.current = saved;
    if (!saved) return;
    setDraft((d) => {
      if (!d || !before) return saved;
      const next = { ...d };
      for (const k of Object.keys(saved) as (keyof T)[]) {
        if (saved[k] !== before[k]) next[k] = saved[k];
      }
      return next;
    });
  }, [saved]);

  const set = useCallback(<K extends keyof T>(key: K, value: T[K]) => {
    setDraft((d) => (d ? { ...d, [key]: value } : d));
  }, []);

  const changed = useMemo(() => {
    const out: Partial<T> = {};
    if (!saved || !draft) return out;
    for (const k of Object.keys(saved) as (keyof T)[]) {
      if (draft[k] !== saved[k]) out[k] = draft[k];
    }
    return out;
  }, [saved, draft]);

  const reset = useCallback(() => setDraft(saved), [saved]);
  return { draft, set, changed, dirty: Object.keys(changed).length, reset };
}

/** Числовое поле: хранит число в черновике, как и раньше (`Number(value)`). */
export function NumField({
  label,
  hint,
  value,
  onChange,
  min,
  max,
  step,
  disabled,
}: {
  label: string;
  hint?: ReactNode;
  disabled?: boolean;
  value: number;
  onChange: (v: number) => void;
  min?: number;
  max?: number;
  step?: number | "any";
}) {
  return (
    <label className="block">
      <Label text={label} hint={hint} />
      <input
        type="number"
        min={min}
        max={max}
        step={step}
        disabled={disabled}
        value={Number.isNaN(value) ? "" : value}
        onChange={(e) => onChange(Number(e.target.value))}
        className={`mt-1 ${INPUT}`}
      />
    </label>
  );
}

export function TextField({
  label,
  hint,
  value,
  onChange,
  placeholder,
  mono = true,
  type = "text",
  disabled,
}: {
  disabled?: boolean;
  label: string;
  hint?: ReactNode;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  mono?: boolean;
  type?: string;
}) {
  return (
    <label className="block">
      <Label text={label} hint={hint} />
      <input
        value={value}
        type={type}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        spellCheck={false}
        autoComplete="off"
        placeholder={placeholder}
        className={`mt-1 ${mono ? "font-mono " : ""}${INPUT}`}
      />
    </label>
  );
}
