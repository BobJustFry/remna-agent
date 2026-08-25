import { useEffect, useRef, useState } from "react";
import {
  api,
  type DestCandidate,
  type DestLoopResult,
  type DestScanEvent,
} from "../api/client";
import type { NodeItem } from "../types";
import { ResizableDialog } from "./ResizableDialog";

type Step = "form" | "scanning" | "picked" | "testing" | "done";

type Props = {
  node: NodeItem;
  onClose: () => void;
  onBusyChange?: (busy: boolean) => void;
};

function parseExtra(raw: string): string[] {
  return raw
    .split(/[\s,;]+/)
    .map((s) => s.trim().toLowerCase())
    .filter(Boolean);
}

function nodeCc(node: NodeItem): string | null {
  const raw = (node.country_code || "").trim().toUpperCase();
  if (raw.length >= 2 && /^[A-Z]{2}/.test(raw)) return raw.slice(0, 2);
  const tok = node.name.split("-")[0]?.toUpperCase() ?? "";
  return /^[A-Z]{2}$/.test(tok) ? tok : null;
}

export function DestPickDialog({ node, onClose, onBusyChange }: Props) {
  const cc = nodeCc(node);
  const [ruOnly, setRuOnly] = useState(() => nodeCc(node) === "RU");
  const [extra, setExtra] = useState("");
  const [step, setStep] = useState<Step>("form");
  const [lines, setLines] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [checked, setChecked] = useState<DestCandidate[]>([]);
  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const [loopResults, setLoopResults] = useState<DestLoopResult[]>([]);
  const [best, setBest] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const logRef = useRef<HTMLPreElement>(null);
  const lastRemoteAt = useRef<number | null>(null);
  const onBusyChangeRef = useRef(onBusyChange);
  onBusyChangeRef.current = onBusyChange;
  const [elapsed, setElapsed] = useState(0);
  const [quietSec, setQuietSec] = useState(0);
  const busy = step === "scanning" || step === "testing";
  const finished = step === "done";
  const winners = loopResults.filter((row) => row.ok);
  const progress = parseProgress(lines);
  const lastLine = lastUsefulLine(lines);

  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lines]);

  useEffect(() => {
    if (!busy) {
      setElapsed(0);
      setQuietSec(0);
      lastRemoteAt.current = null;
      return;
    }
    const started = Date.now();
    let lastTick = 0;
    const t = window.setInterval(() => {
      const now = Date.now();
      const sec = Math.floor((now - started) / 1000);
      setElapsed(sec);
      setQuietSec(lastRemoteAt.current ? Math.floor((now - lastRemoteAt.current) / 1000) : sec);
      if (!lastRemoteAt.current && sec > 0 && sec % 5 === 0 && sec !== lastTick) {
        lastTick = sec;
        setLines((prev) => [...prev, `… всё ещё жду байты потока, ${sec}с`]);
      }
    }, 250);
    return () => window.clearInterval(t);
  }, [busy]);

  useEffect(() => {
    onBusyChangeRef.current?.(busy);
  }, [busy]);

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
      onBusyChangeRef.current?.(false);
    };
  }, []);

  function pushLog(line: string, fromServer = false) {
    if (!line.trim()) return;
    if (fromServer) lastRemoteAt.current = Date.now();
    setLines((prev) => [...prev, line]);
  }

  async function runScan() {
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    setError(null);
    setLines([]);
    setChecked([]);
    setSelected(new Set());
    setLoopResults([]);
    setBest(null);
    setCopied(false);
    setStep("scanning");
    pushLog(`→ подключаюсь к ${node.name} (${node.host}) по SSH…`);
    pushLog(
      ruOnly
        ? "режим: только российские ресурсы, без /24"
        : `режим: доноры страны ${cc || "?"} (+ общие) и скан /24`,
    );
    try {
      await api.destScanStream(node.id, {
        ru_only: ruOnly,
        extra: parseExtra(extra),
        signal: ac.signal,
        onEvent: (ev: DestScanEvent) => {
          if (ev.type === "log") pushLog(ev.line, true);
          if (ev.type === "error") {
            setError(ev.message);
            setStep("form");
          }
          if (ev.type === "done") {
            setChecked(ev.checked ?? []);
            const good = (ev.good ?? []).map((d) => d.host);
            setSelected(new Set(good.slice(0, 12)));
            setBest(ev.best ?? null);
            setStep("picked");
            pushLog(
              ev.best
                ? `лучший формальный: ${ev.best} (${(ev.good ?? []).length} годных)`
                : "годных нет — снимите галку RU или добавьте свои домены",
            );
          }
        },
      });
    } catch (err: unknown) {
      if (ac.signal.aborted) return;
      setError(err instanceof Error ? err.message : "Скан не удался");
      setStep("form");
    }
  }

  async function runLoopback() {
    const dests = checked.filter((d) => selected.has(d.host)).map((d) => d.host).slice(0, 12);
    if (dests.length === 0) {
      setError("отметьте хотя бы один dest");
      return;
    }
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    setError(null);
    setStep("testing");
    pushLog(`→ петля REALITY на :18443, ${dests.length} шт.: ${dests.join(", ")}`);
    try {
      await api.destLoopbackStream(node.id, {
        dests,
        signal: ac.signal,
        onEvent: (ev) => {
          if (ev.type === "log") pushLog(ev.line, true);
          if (ev.type === "error") {
            setError(ev.message);
            setStep("picked");
          }
          if (ev.type === "done") {
            setLoopResults(ev.results ?? []);
            setBest(ev.best ?? null);
            setStep("done");
          }
        },
      });
    } catch (err: unknown) {
      if (ac.signal.aborted) return;
      setError(err instanceof Error ? err.message : "Петля не удалась");
      setStep("picked");
    }
  }

  function toggle(host: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(host)) next.delete(host);
      else if (next.size < 12) next.add(host);
      return next;
    });
  }

  async function copyBest() {
    if (!best) return;
    try {
      await navigator.clipboard.writeText(best);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  return (
    <ResizableDialog
      storageKey="dest-pick"
      defaultWidth={820}
      defaultHeight={780}
      minWidth={560}
      minHeight={480}
      zClass="z-[70]"
    >
        <div className="shrink-0 border-b border-[var(--border)] px-5 py-4">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h2 className="text-base font-semibold">Прикрытие REALITY</h2>
              <p className="mt-1 font-mono text-xs text-[var(--muted)]">
                {node.name} · {node.host}
                {cc ? ` · ${cc}` : ""}
              </p>
            </div>
            {busy && (
              <span className="inline-flex items-center gap-1.5 rounded-md border border-[rgba(34,211,187,0.4)] bg-[var(--accent-dim)] px-2 py-1 text-[11px] font-semibold text-[var(--accent)]">
                <ScanSpinner />
                {step === "scanning" ? "скан" : "петля"} · {formatElapsed(elapsed)}
              </span>
            )}
          </div>
        </div>

        <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-hidden px-5 py-4 text-sm">
          {!finished && (
          <>
          <label className="flex shrink-0 items-start gap-2 rounded-lg border border-[var(--border)] bg-[var(--bg)] px-3 py-2">
            <input
              type="checkbox"
              className="mt-0.5 h-4 w-4 accent-[var(--accent)]"
              checked={ruOnly}
              onChange={(e) => setRuOnly(e.target.checked)}
              disabled={busy}
            />
            <span>
              <span className="block text-sm text-[var(--text)]">RU — только российские ресурсы</span>
              <span className="mt-0.5 block text-[11px] text-[var(--muted)]">
                {ruOnly
                  ? "Список .ru / .рф / vk.com, банки и маркеты. Соседей по /24 не трогаем."
                  : cc
                    ? `Доноры под ${cc} из sni-choose плюс общие (*). Замер TLS/HTTP/2/задержки — с этой ноды.`
                    : "Страна ноды пустая — общие доноры (*) и скан /24. Пропиши country у ноды."}
              </span>
            </span>
          </label>

          <label className="grid shrink-0 gap-1 text-xs text-[var(--muted)]">
            <span>Свои домены (необязательно)</span>
            <textarea
              value={extra}
              onChange={(e) => setExtra(e.target.value)}
              disabled={busy}
              rows={2}
              placeholder="www.avito.ru, www.rbc.ru"
              className="w-full resize-y rounded-lg border border-[var(--border)] bg-[var(--bg)] px-3 py-2 font-mono text-[12px] text-[var(--text)] outline-none focus:border-[var(--accent)]"
            />
          </label>
          </>
          )}

          {finished && (
            <div
              className={`shrink-0 rounded-lg border px-3 py-3 ${
                winners.length
                  ? "border-[rgba(34,211,187,0.35)] bg-[var(--accent-dim)]"
                  : "border-[rgba(240,113,120,0.35)] bg-[rgba(240,113,120,0.08)]"
              }`}
            >
              <p className="text-sm font-semibold text-[var(--text)]">
                {winners.length
                  ? `Итог: петля встала на ${winners.length} из ${loopResults.length}`
                  : `Итог: петля не встала (${loopResults.length || 0} проверено)`}
              </p>
              {best ? (
                <p className="mt-1 text-sm text-[var(--text)]">
                  брать: <span className="font-mono font-semibold">{best}</span>
                  <button
                    type="button"
                    onClick={() => void copyBest()}
                    className="ml-2 text-xs font-semibold text-[var(--accent)] hover:underline"
                  >
                    {copied ? "скопировано" : "копировать"}
                  </button>
                </p>
              ) : null}
              <p className="mt-1 text-[11px] text-[var(--muted)]">в Remnawave само не пишется</p>
            </div>
          )}

          {busy && (
            <div className="shrink-0 rounded-lg border border-[rgba(34,211,187,0.35)] bg-[var(--accent-dim)] px-3 py-2">
              <div className="flex items-center justify-between gap-2 text-sm">
                <span className="font-medium text-[var(--text)]">
                  {lastRemoteAt.current
                    ? step === "scanning"
                      ? "Скан идёт на ноде"
                      : "Кручу петлю REALITY"
                    : "Жду первый байт с панели"}
                </span>
                <span className="tabular-nums text-[11px] text-[var(--muted)]">
                  {progress ? `${progress.done} / ${progress.total}` : formatElapsed(elapsed)}
                </span>
              </div>
              <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-[var(--bg)]">
                <div
                  className={`h-full rounded-full bg-[var(--accent)] transition-[width] duration-300 ${
                    progress ? "" : "animate-pulse"
                  }`}
                  style={{
                    width: progress
                      ? `${Math.max(4, Math.round((progress.done / progress.total) * 100))}%`
                      : "22%",
                  }}
                />
              </div>
              <p className="mt-1.5 font-mono text-[11px] text-[var(--muted)]">
                {lastRemoteAt.current
                  ? lastLine
                  : `тишина ${quietSec}с — поток ещё не пришёл (SSH или буфер nginx)`}
              </p>
            </div>
          )}

          {error && (
            <div className="rounded-lg border border-[rgba(240,113,120,0.35)] bg-[rgba(240,113,120,0.08)] px-3 py-2 text-xs text-[var(--danger)]">
              {error}
            </div>
          )}

          {checked.length > 0 && (
            <div className="min-h-0 flex-1 basis-48 overflow-auto rounded-lg border border-[var(--border)]">
              <table className="w-full text-left text-[12px]">
                <thead className="sticky top-0 z-10 bg-[var(--bg-elevated)] text-[10px] uppercase tracking-wide text-[var(--muted)]">
                  <tr>
                    <th className="px-2 py-1.5 font-semibold"> </th>
                    <th className="px-2 py-1.5 font-semibold">dest</th>
                    <th className="px-2 py-1.5 font-semibold">TLS13</th>
                    <th className="px-2 py-1.5 font-semibold">H2</th>
                    <th className="px-2 py-1.5 font-semibold">код</th>
                    <th className="px-2 py-1.5 font-semibold">мс</th>
                    <th className="px-2 py-1.5 font-semibold">вердикт</th>
                  </tr>
                </thead>
                <tbody>
                  {checked.map((row) => (
                    <tr key={row.host} className="border-t border-[var(--border)]">
                      <td className="px-2 py-1.5">
                        <input
                          type="checkbox"
                          className="h-3.5 w-3.5 accent-[var(--accent)]"
                          checked={selected.has(row.host)}
                          onChange={() => toggle(row.host)}
                          disabled={busy || finished}
                        />
                      </td>
                      <td className="px-2 py-1.5 font-mono text-[var(--text)]">{row.host}</td>
                      <td className="px-2 py-1.5 text-[var(--muted)]">
                        {row.tls13 || (row.kex || "").includes("X25519") ? "да" : "нет"}
                      </td>
                      <td className="px-2 py-1.5 text-[var(--muted)]">
                        {row.http_version === "2" ? "да" : "нет"}
                      </td>
                      <td className="px-2 py-1.5 tabular-nums text-[var(--muted)]">{row.code || "—"}</td>
                      <td className="px-2 py-1.5 tabular-nums text-[var(--muted)]">
                        {row.connect_med != null ? Math.round(row.connect_med) : "—"}
                      </td>
                      <td className={`px-2 py-1.5 ${row.ok ? "text-[var(--success)]" : "text-[var(--danger)]"}`}>
                        {row.verdict || (row.ok ? "годится" : row.why || "пропустить")}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {loopResults.length > 0 && (
            <div className="space-y-1 rounded-lg border border-[var(--border)] bg-[var(--bg)] px-3 py-2 text-[12px]">
              {loopResults.map((row) => (
                <div key={row.host} className="flex gap-2">
                  <span className={row.ok ? "text-[var(--success)]" : "text-[var(--danger)]"}>
                    {row.ok ? "✓" : "✗"}
                  </span>
                  <span className="font-mono">{row.host}</span>
                  <span className="text-[var(--muted)]">{row.note}</span>
                </div>
              ))}
            </div>
          )}

          <pre
            ref={logRef}
            className="min-h-40 flex-1 overflow-auto rounded-lg border border-[#1a2a32] bg-[#070c0f] px-3 py-3 font-mono text-[12px] leading-relaxed text-[#c8d4dc]"
          >
            {lines.length === 0 ? (
              <span className="text-[#5a6a75]">Лог появится здесь</span>
            ) : (
              lines.map((line, i) => (
                <div
                  key={`${i}-${line.slice(0, 24)}`}
                  className={
                    line.startsWith("→")
                      ? "text-[var(--accent)]"
                      : line.startsWith("[")
                        ? "text-[#e8f4f1]"
                        : line.startsWith("✗") || line.includes("ошибка")
                          ? "text-[var(--danger)]"
                          : undefined
                  }
                >
                  {line}
                </div>
              ))
            )}
          </pre>
        </div>

        <div className="flex shrink-0 flex-wrap justify-end gap-2 border-t border-[var(--border)] px-5 py-4 pr-7">
          {finished ? (
            <button
              type="button"
              onClick={() => {
                abortRef.current?.abort();
                onClose();
              }}
              className="rounded-lg bg-[var(--accent)] px-4 py-2 text-sm font-semibold text-[#06221e] hover:brightness-110"
            >
              Закрыть
            </button>
          ) : (
            <>
          <button
            type="button"
            onClick={() => {
              abortRef.current?.abort();
              onClose();
            }}
            className="rounded-lg border border-[var(--border)] px-4 py-2 text-sm text-[var(--muted)] transition hover:text-[var(--text)]"
          >
            Закрыть
          </button>
          <button
            type="button"
            onClick={() => void runScan()}
            disabled={busy}
            className="rounded-lg border border-[var(--border)] px-4 py-2 text-sm text-[var(--text)] transition hover:border-[var(--accent)] disabled:opacity-50"
          >
            {step === "scanning" ? "Сканирую…" : checked.length ? "Сканировать снова" : "Сканировать"}
          </button>
          <button
            type="button"
            onClick={() => void runLoopback()}
            disabled={busy || selected.size === 0}
            className="rounded-lg bg-[var(--accent)] px-4 py-2 text-sm font-semibold text-[#06221e] hover:brightness-110 disabled:opacity-50"
          >
            {step === "testing" ? "Петля…" : "Далее — петля"}
          </button>
            </>
          )}
        </div>
    </ResizableDialog>
  );
}

function parseProgress(lines: string[]): { done: number; total: number } | null {
  for (let i = lines.length - 1; i >= 0; i -= 1) {
    const m = lines[i].match(/\[(\d+)\s*\/\s*(\d+)\]/);
    if (!m) continue;
    const done = Number(m[1]);
    const total = Number(m[2]);
    if (total > 0) return { done, total };
  }
  return null;
}

function lastUsefulLine(lines: string[]): string {
  for (let i = lines.length - 1; i >= 0; i -= 1) {
    const line = lines[i].trim();
    if (line) return line;
  }
  return "";
}

function formatElapsed(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return m > 0 ? `${m}:${s.toString().padStart(2, "0")}` : `${s}с`;
}

function ScanSpinner() {
  return (
    <svg className="h-3.5 w-3.5 animate-spin" viewBox="0 0 24 24" fill="none" aria-hidden>
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeOpacity="0.25" strokeWidth="3" />
      <path d="M12 2a10 10 0 0 1 10 10" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}
