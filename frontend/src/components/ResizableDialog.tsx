import { useCallback, useEffect, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";

type Size = { w: number; h: number };

type Props = {
  children: ReactNode;
  storageKey: string;
  defaultWidth: number;
  defaultHeight: number;
  minWidth?: number;
  minHeight?: number;
  zClass?: string;
};

const STORE_PREFIX = "remna.dialog.size.";

function readStored(key: string): Size | null {
  try {
    const raw = localStorage.getItem(STORE_PREFIX + key);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Size;
    if (typeof parsed.w === "number" && typeof parsed.h === "number") return parsed;
  } catch {
    // ignore
  }
  return null;
}

function clampSize(size: Size, minW: number, minH: number): Size {
  const maxW = Math.max(240, window.innerWidth - 24);
  const maxH = Math.max(240, window.innerHeight - 24);
  const loW = Math.min(minW, maxW);
  const loH = Math.min(minH, maxH);
  return {
    w: Math.min(maxW, Math.max(loW, size.w)),
    h: Math.min(maxH, Math.max(loH, size.h)),
  };
}

export function ResizableDialog({
  children,
  storageKey,
  defaultWidth,
  defaultHeight,
  minWidth = 440,
  minHeight = 380,
  zClass = "z-[60]",
}: Props) {
  const [desktop, setDesktop] = useState(() =>
    typeof window !== "undefined" ? window.matchMedia("(min-width: 640px)").matches : true,
  );
  const [size, setSize] = useState<Size>(() => {
    const stored = typeof window !== "undefined" ? readStored(storageKey) : null;
    return stored ?? { w: defaultWidth, h: defaultHeight };
  });

  useEffect(() => {
    const mq = window.matchMedia("(min-width: 640px)");
    const onChange = () => setDesktop(mq.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  useEffect(() => {
    function onResize() {
      setSize((prev) => clampSize(prev, minWidth, minHeight));
    }
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [minHeight, minWidth]);

  useEffect(() => {
    if (!desktop) return;
    try {
      localStorage.setItem(STORE_PREFIX + storageKey, JSON.stringify(size));
    } catch {
      // ignore
    }
  }, [desktop, size, storageKey]);

  const startResize = useCallback(
    (axis: "x" | "y" | "both") => (event: ReactPointerEvent<HTMLButtonElement>) => {
      event.preventDefault();
      event.stopPropagation();
      const startX = event.clientX;
      const startY = event.clientY;
      const start = size;
      const pointerId = event.pointerId;
      const target = event.currentTarget;
      target.setPointerCapture(pointerId);

      const onMove = (ev: PointerEvent) => {
        const next = { ...start };
        if (axis === "x" || axis === "both") next.w = start.w + ev.clientX - startX;
        if (axis === "y" || axis === "both") next.h = start.h + ev.clientY - startY;
        setSize(clampSize(next, minWidth, minHeight));
      };
      const onUp = () => {
        target.releasePointerCapture(pointerId);
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    },
    [minHeight, minWidth, size],
  );

  const panelStyle = desktop
    ? {
        width: clampSize(size, minWidth, minHeight).w,
        height: clampSize(size, minWidth, minHeight).h,
        maxWidth: "calc(100vw - 1.5rem)",
        maxHeight: "calc(100dvh - 1.5rem)",
      }
    : undefined;

  return (
    <div
      className={`fixed inset-0 ${zClass} flex items-end justify-center bg-black/60 p-0 backdrop-blur-sm sm:items-center sm:p-3`}
    >
      <div
        className="relative flex h-[100dvh] w-full flex-col overflow-hidden rounded-t-[var(--radius)] border border-[var(--border)] bg-[var(--bg-elevated)] shadow-2xl sm:h-auto sm:rounded-[var(--radius)]"
        style={panelStyle}
      >
        <div className="flex h-full min-h-0 flex-col overflow-hidden">{children}</div>
        {desktop && (
          <>
            <button
              type="button"
              aria-label="Ширина окна"
              onPointerDown={startResize("x")}
              className="absolute inset-y-6 right-0 z-10 w-2 cursor-ew-resize touch-none"
            />
            <button
              type="button"
              aria-label="Высота окна"
              onPointerDown={startResize("y")}
              className="absolute inset-x-6 bottom-0 z-10 h-2 cursor-ns-resize touch-none"
            />
            <button
              type="button"
              aria-label="Размер окна"
              onPointerDown={startResize("both")}
              className="absolute bottom-0 right-0 z-20 flex h-5 w-5 cursor-nwse-resize touch-none items-end justify-end p-0.5 text-[var(--muted)] hover:text-[var(--accent)]"
            >
              <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden>
                <path d="M11 4v7H4" fill="none" stroke="currentColor" strokeWidth="1.5" />
                <path d="M11 8v3H8" fill="none" stroke="currentColor" strokeWidth="1.5" />
              </svg>
            </button>
          </>
        )}
      </div>
    </div>
  );
}
