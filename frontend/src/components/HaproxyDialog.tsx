import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  type HaproxyAction,
  type HaproxyLiveStats,
  type HaproxyRoute,
  type HaproxyStatus,
  type HaproxyTemplate,
  type ScriptStreamEvent,
} from "../api/client";
import type { NodeItem } from "../types";
import { CapacityDialog } from "./CapacityDialog";
import { ConfirmDialog } from "./ConfirmDialog";
import { ResizableDialog } from "./ResizableDialog";
import { HaproxyStatsView } from "./HaproxyStatsView";

type DialogTab = "config" | "stats" | "sessions";

type Props = {
  node: NodeItem;
  onClose: () => void;
  onBusyChange?: (busy: boolean) => void;
};

const TEMPLATES: { id: HaproxyTemplate; label: string; hint: string }[] = [
  { id: "minimal", label: "minimal", hint: "только stats на 127.0.0.1:8404" },
  { id: "front-xhttp", label: "front-xhttp", hint: "HTTP → Xray xHTTP, как nginx-origin" },
  { id: "tcp", label: "tcp", hint: "TCP passthrough: один listen-порт = одна нода" },
];

export function HaproxyDialog({ node, onClose, onBusyChange }: Props) {
  const [status, setStatus] = useState<HaproxyStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [template, setTemplate] = useState<HaproxyTemplate>("front-xhttp");
  const [bindPort, setBindPort] = useState(80);
  const [backend, setBackend] = useState("127.0.0.1:10087");
  const [pathPrefix, setPathPrefix] = useState("/api/generate/");
  const [proxyProtocol, setProxyProtocol] = useState(true);
  const [routes, setRoutes] = useState<HaproxyRoute[]>([{ listen: 443, backend: "127.0.0.1:443" }]);
  const [config, setConfig] = useState("");
  const [lines, setLines] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [checkOpen, setCheckOpen] = useState(false);
  const [confirmUninstall, setConfirmUninstall] = useState(false);
  const [tab, setTab] = useState<DialogTab>("config");
  const [live, setLive] = useState<HaproxyLiveStats | null>(null);
  const [liveLoading, setLiveLoading] = useState(false);
  const [liveError, setLiveError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const logRef = useRef<HTMLPreElement>(null);
  const previewLive = useRef(false);
  const previewGen = useRef(0);

  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lines]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    void api
      .getHaproxy(node.id)
      .then((st) => {
        if (cancelled) return;
        applyStatus(st);
        if (!st.config) {
          void loadPreview({
            template,
            bind_port: bindPort,
            backend,
            path_prefix: pathPrefix,
            proxy_protocol: proxyProtocol,
          });
        }
        if (st.error && !st.installed) setError(st.error);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Не удалось прочитать HAProxy");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
      abortRef.current?.abort();
    };
  }, [node.id]);

  function setBusyState(next: boolean) {
    setBusy(next);
    onBusyChange?.(next);
  }

  function applyStatus(st: HaproxyStatus) {
    setStatus(st);
    if (!st.config) return;
    setConfig(st.config);
    const p = st.parsed;
    if (p?.template) setTemplate(p.template);
    if (p?.bind_port) setBindPort(p.bind_port);
    if (p?.backend) setBackend(p.backend);
    if (p?.path_prefix) setPathPrefix(p.path_prefix);
    if (p?.routes?.length) {
      setRoutes(p.routes);
      setBindPort(p.routes[0].listen);
      setBackend(p.routes[0].backend);
    } else if (p?.bind_port && p.backend) {
      setRoutes([{ listen: p.bind_port, backend: p.backend }]);
    }
    if (p) setProxyProtocol(!!p.proxy_protocol);
    else setProxyProtocol(/\bsend-proxy(?:-v2)?\b/.test(st.config));
  }

  async function refreshStatus() {
    const st = await api.getHaproxy(node.id);
    applyStatus(st);
    return st;
  }

  const loadLive = useCallback(async () => {
    setLiveLoading(true);
    setLiveError(null);
    try {
      const data = await api.getHaproxyStats(node.id);
      setLive(data);
      if (data.error) setLiveError(data.error);
    } catch (err) {
      setLiveError(err instanceof Error ? err.message : "Не удалось снять статистику");
    } finally {
      setLiveLoading(false);
    }
  }, [node.id]);

  useEffect(() => {
    if (tab === "config") return;
    void loadLive();
    const timer = window.setInterval(() => {
      void loadLive();
    }, 15000);
    return () => window.clearInterval(timer);
  }, [tab, loadLive]);

  async function runDiag() {
    if (busy) return;
    setError(null);
    setLines(["→ диагностика…"]);
    setBusyState(true);
    try {
      const diag = await api.getHaproxyDiag(node.id);
      setLines(diag.lines.length ? diag.lines : ["(пусто)"]);
      if (diag.error) setError(diag.error);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось собрать диагностику");
    } finally {
      setBusyState(false);
    }
  }

  function previewBody(over: Partial<{
    template: HaproxyTemplate;
    bind_port: number;
    backend: string;
    path_prefix: string;
    proxy_protocol: boolean;
    routes: HaproxyRoute[];
  }> = {}) {
    const nextRoutes = over.routes ?? routes;
    return {
      template: over.template ?? template,
      bind_port: nextRoutes[0]?.listen ?? over.bind_port ?? bindPort,
      backend: nextRoutes[0]?.backend ?? over.backend ?? backend,
      path_prefix: over.path_prefix ?? pathPrefix,
      proxy_protocol: over.proxy_protocol ?? proxyProtocol,
      routes: (over.template ?? template) === "tcp" ? nextRoutes : [],
    };
  }

  async function loadPreview(params: {
    template: HaproxyTemplate;
    bind_port: number;
    backend: string;
    path_prefix: string;
    proxy_protocol: boolean;
    routes?: HaproxyRoute[];
  }) {
    const gen = ++previewGen.current;
    setError(null);
    try {
      const preview = await api.haproxyPreview(params);
      if (gen !== previewGen.current) return;
      setConfig(preview.config);
    } catch (err) {
      if (gen !== previewGen.current) return;
      setError(err instanceof Error ? err.message : "Не удалось собрать шаблон");
    }
  }

  function pickTemplate(next: HaproxyTemplate) {
    setTemplate(next);
    previewLive.current = true;
    void loadPreview(previewBody({ template: next }));
  }

  function toggleProxyProtocol(next: boolean) {
    setProxyProtocol(next);
    previewLive.current = true;
    void loadPreview(previewBody({ proxy_protocol: next }));
  }

  function changeRoutes(next: HaproxyRoute[]) {
    setRoutes(next);
    if (next[0]) {
      setBindPort(next[0].listen);
      setBackend(next[0].backend);
    }
    previewLive.current = true;
    void loadPreview(previewBody({ routes: next }));
  }

  useEffect(() => {
    if (!previewLive.current) return;
    const timer = window.setTimeout(() => {
      void loadPreview(previewBody());
    }, 250);
    return () => window.clearTimeout(timer);
  }, [bindPort, backend, pathPrefix, routes]);

  async function run(action: HaproxyAction) {
    if (busy) return;
    setError(null);
    setLines([]);
    const ac = new AbortController();
    abortRef.current = ac;
    setBusyState(true);
    try {
      const onEvent = (ev: ScriptStreamEvent) => {
        if (ac.signal.aborted) return;
        if (ev.type === "log") setLines((prev) => [...prev, ev.line]);
        else if (ev.type === "done") setLines((prev) => [...prev, "", `✓ ${ev.message}`]);
        else setLines((prev) => [...prev, "", `✗ ${ev.message}`]);
      };
      await api.runHaproxyStream(node.id, {
        action,
        force: action === "install" && status?.installed === true,
        template,
        bind_port: bindPort,
        backend,
        path_prefix: pathPrefix,
        proxy_protocol: proxyProtocol,
        routes: template === "tcp" ? routes : [],
        config: action === "install" || action === "apply" ? config : undefined,
        signal: ac.signal,
        onEvent,
      });
      const st = await refreshStatus();
      if (action === "uninstall" && !st.installed) setConfig("");
    } catch (err) {
      const aborted =
        ac.signal.aborted ||
        (err instanceof DOMException && err.name === "AbortError") ||
        (err instanceof Error && err.name === "AbortError");
      if (aborted) setLines((prev) => [...prev, "", "✗ Выполнение отменено"]);
      else setError(err instanceof Error ? err.message : "Ошибка HAProxy");
    } finally {
      abortRef.current = null;
      setBusyState(false);
    }
  }

  const listen = status?.listen?.length ? status.listen.join(", ") : "—";

  return (
    <>
    <ResizableDialog
      storageKey="haproxy"
      defaultWidth={1120}
      defaultHeight={860}
      minWidth={480}
      minHeight={520}
    >
        <div className="flex shrink-0 flex-wrap items-start justify-between gap-3 border-b border-[var(--border)] px-4 py-3 sm:px-5 sm:py-4">
          <div className="min-w-0">
            <h2 className="text-base font-semibold">HAProxy</h2>
            <p className="mt-1 truncate font-mono text-xs text-[var(--muted)]">
              {node.name} · {node.host}
            </p>
          </div>
          <div className="flex shrink-0 gap-2">
            <button
              type="button"
              onClick={() => void runDiag()}
              disabled={busy || loading}
              className="rounded-lg border border-[var(--border)] px-3 py-1.5 text-xs font-semibold uppercase tracking-wide text-[var(--text)] hover:border-[var(--accent)] disabled:opacity-50"
            >
              Анализ
            </button>
            <button
              type="button"
              onClick={() => setCheckOpen(true)}
              className="rounded-lg border border-[var(--accent)] bg-[var(--accent-dim)] px-3 py-1.5 text-xs font-semibold uppercase tracking-wide text-[var(--accent)] hover:brightness-110"
            >
              Проверка
            </button>
          </div>
        </div>

        <div className="no-scrollbar flex shrink-0 gap-1 overflow-x-auto border-b border-[var(--border)] px-4 sm:px-5">
          {(
            [
              ["config", "Конфиг"],
              ["stats", "Статистика"],
              ["sessions", "Сессии"],
            ] as const
          ).map(([id, label]) => (
            <button
              key={id}
              type="button"
              onClick={() => setTab(id)}
              className={[
                "relative px-3 py-2 text-sm",
                tab === id
                  ? "font-semibold text-[var(--accent)]"
                  : "text-[var(--muted)] hover:text-[var(--text)]",
              ].join(" ")}
            >
              {label}
              {tab === id && (
                <span className="absolute inset-x-2 bottom-0 h-0.5 rounded-full bg-[var(--accent)]" />
              )}
            </button>
          ))}
        </div>

        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-4 text-sm sm:px-5">
          <div className="grid gap-2 rounded-lg border border-[var(--border)] bg-[var(--bg)] px-3 py-2 text-xs sm:grid-cols-4">
            <Stat
              label="пакет"
              value={loading ? "…" : status?.installed ? "стоит" : "нет"}
              ok={status?.installed}
            />
            <Stat
              label="сервис"
              value={loading ? "…" : status?.running ? "active" : "down"}
              ok={status?.running}
            />
            <Stat label="версия" value={loading ? "…" : status?.version ?? "—"} />
            <Stat label="listen" value={loading ? "…" : listen} />
          </div>

          {tab !== "config" && (
            <HaproxyStatsView
              stats={live}
              loading={liveLoading}
              error={liveError}
              onRefresh={() => void loadLive()}
              showSessions={tab === "sessions"}
            />
          )}

          {tab === "config" && status?.valid === false && (
            <div className="rounded-lg border border-[rgba(240,113,120,0.35)] bg-[rgba(240,113,120,0.08)] px-3 py-2 text-xs text-[var(--danger)]">
              Текущий конфиг не проходит `haproxy -c`
            </div>
          )}

          {tab === "config" && (
          <>
          <div className="grid gap-2 sm:grid-cols-[1fr_90px_1fr_1fr]">
            <label className="block">
              <span className="mb-1 block text-[11px] text-[var(--muted)]">Шаблон</span>
              <select
                value={template}
                onChange={(e) => pickTemplate(e.target.value as HaproxyTemplate)}
                className={inputCls}
                disabled={busy}
              >
                {TEMPLATES.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.label}
                  </option>
                ))}
              </select>
            </label>
            {template !== "tcp" && (
              <>
                <label className="block">
                  <span className="mb-1 block text-[11px] text-[var(--muted)]">Порт</span>
                  <input
                    type="number"
                    min={1}
                    max={65535}
                    value={bindPort}
                    onChange={(e) => setBindPort(Number(e.target.value) || 80)}
                    className={inputCls}
                    disabled={busy || template === "minimal"}
                  />
                </label>
                <label className="block">
                  <span className="mb-1 block text-[11px] text-[var(--muted)]">Backend</span>
                  <input
                    value={backend}
                    onChange={(e) => setBackend(e.target.value)}
                    className={inputCls}
                    disabled={busy || template === "minimal"}
                  />
                </label>
                <label className="block">
                  <span className="mb-1 block text-[11px] text-[var(--muted)]">Path</span>
                  <input
                    value={pathPrefix}
                    onChange={(e) => setPathPrefix(e.target.value)}
                    className={inputCls}
                    disabled={busy || template !== "front-xhttp"}
                  />
                </label>
              </>
            )}
          </div>
          <p className="text-[11px] text-[var(--muted)]">
            {TEMPLATES.find((t) => t.id === template)?.hint}
          </p>

          {template === "tcp" && (
            <div className="space-y-2 rounded-lg border border-[var(--border)] bg-[var(--bg)] px-3 py-2">
              <div className="grid grid-cols-[88px_1fr_28px] gap-2 text-[11px] text-[var(--muted)]">
                <span>Listen</span>
                <span>Нода host:port</span>
                <span />
              </div>
              {routes.map((row, idx) => (
                <div key={idx} className="grid grid-cols-[88px_1fr_28px] gap-2">
                  <input
                    type="number"
                    min={1}
                    max={65535}
                    value={row.listen}
                    onChange={(e) => {
                      const next = routes.slice();
                      next[idx] = { ...row, listen: Number(e.target.value) || 1 };
                      changeRoutes(next);
                    }}
                    className={inputCls}
                    disabled={busy}
                  />
                  <input
                    value={row.backend}
                    onChange={(e) => {
                      const next = routes.slice();
                      next[idx] = { ...row, backend: e.target.value };
                      changeRoutes(next);
                    }}
                    className={inputCls}
                    disabled={busy}
                    placeholder="144.31.214.43:443"
                  />
                  <button
                    type="button"
                    className="text-[var(--muted)] hover:text-[var(--danger)] disabled:opacity-40"
                    disabled={busy || routes.length <= 1}
                    onClick={() => changeRoutes(routes.filter((_, i) => i !== idx))}
                    title="Убрать порт"
                  >
                    ×
                  </button>
                </div>
              ))}
              <button
                type="button"
                className="text-xs text-[var(--accent)] hover:underline disabled:opacity-50"
                disabled={busy || routes.length >= 32}
                onClick={() =>
                  changeRoutes([...routes, { listen: nextFreeListen(routes), backend: "" }])
                }
              >
                + порт
              </button>
              <p className="text-[11px] text-[var(--muted)]">
                В панели Remnawave у хоста адрес = IP этого HAProxy, порт = Listen. На inbound
                ноды нужен <code>acceptProxyProtocol: true</code>. Два хоста на один Listen нельзя —
                оба попадут в одну ноду.
              </p>
            </div>
          )}

          {template !== "minimal" && (
            <label className="flex items-start gap-2 rounded-lg border border-[var(--border)] bg-[var(--bg)] px-3 py-2">
              <input
                type="checkbox"
                className="mt-0.5"
                checked={proxyProtocol}
                onChange={(e) => toggleProxyProtocol(e.target.checked)}
                disabled={busy}
              />
              <span>
                <span className="block text-sm text-[var(--text)]">
                  PROXY protocol — Remna видит IP клиентов
                </span>
                <span className="mt-0.5 block text-[11px] text-[var(--muted)]">
                  В конфиг пишется <code>send-proxy-v2</code>. На inbound Xray нужно{" "}
                  <code>streamSettings.sockopt.acceptProxyProtocol: true</code>, иначе туннель не
                  поднимется. Без этой галки Remna видит всех как 127.0.0.1.
                </span>
              </span>
            </label>
          )}

          <label className="block">
            <span className="mb-1 block text-[11px] text-[var(--muted)]">/etc/haproxy/haproxy.cfg</span>
            <textarea
              value={config}
              onChange={(e) => setConfig(e.target.value)}
              spellCheck={false}
              disabled={busy}
              className="min-h-[20rem] w-full resize-y rounded-lg border border-[var(--border)] bg-[#070c0f] px-3 py-2 font-mono text-[12px] leading-relaxed text-[#c8d4dc] outline-none focus:border-[var(--accent)]"
            />
          </label>

          {error && (
            <div className="rounded-lg border border-[rgba(240,113,120,0.35)] bg-[rgba(240,113,120,0.08)] px-3 py-2 text-xs text-[var(--danger)]">
              {error}
            </div>
          )}

          <pre
            ref={logRef}
            className="min-h-[10rem] overflow-auto rounded-lg border border-[#1a2a32] bg-[#070c0f] px-3 py-3 font-mono text-[12px] leading-relaxed text-[#c8d4dc]"
          >
            {lines.length === 0 ? (
              <span className="text-[#5a6a75]">{busy ? "Ожидание…" : "Лог появится здесь"}</span>
            ) : (
              lines.map((line, i) => (
                <div
                  key={i}
                  className={
                    line.startsWith("✗")
                      ? "text-[var(--danger)]"
                      : line.startsWith("✓")
                        ? "text-[var(--success)]"
                        : line.startsWith("$") || line.startsWith("→")
                          ? "text-[var(--accent)]"
                          : undefined
                  }
                >
                  {line || "\u00a0"}
                </div>
              ))
            )}
          </pre>
          </>
          )}
        </div>

        <div className="flex shrink-0 flex-wrap justify-end gap-2 border-t border-[var(--border)] px-5 py-4 pr-7">
          <button type="button" onClick={onClose} className={btnGhost}>
            {busy ? "В фоне" : "Закрыть"}
          </button>
          <button
            type="button"
            onClick={() => setConfirmUninstall(true)}
            className={btnDanger}
            disabled={busy || loading}
          >
            Удалить
          </button>
          {status?.installed && (
            <>
              <button type="button" onClick={() => void run("stop")} className={btnDanger} disabled={busy}>
                Stop
              </button>
              <button type="button" onClick={() => void run("start")} className={btnGhost} disabled={busy}>
                Start
              </button>
              <button type="button" onClick={() => void run("reload")} className={btnGhost} disabled={busy}>
                Reload
              </button>
              {tab === "config" && (
              <button type="button" onClick={() => void run("apply")} className={btnGhost} disabled={busy || !config.trim()}>
                Применить
              </button>
              )}
            </>
          )}
          {tab === "config" && (
          <button
            type="button"
            onClick={() => void run("install")}
            className={btnPrimary}
            disabled={busy}
          >
            {status?.installed ? "Переустановить" : "Установить"}
          </button>
          )}
        </div>
    </ResizableDialog>
    {checkOpen && <CapacityDialog node={node} onClose={() => setCheckOpen(false)} />}
    <ConfirmDialog
      open={confirmUninstall}
      zClass="z-[70]"
      title="Удалить HAProxy?"
      message={`С ноды «${node.name}» (${node.host}) снимем сервис, пакет и /etc/haproxy. BBR не трогаем.`}
      confirmLabel="Удалить начисто"
      busy={busy}
      busyLabel="Удаляю…"
      onCancel={() => {
        if (!busy) setConfirmUninstall(false);
      }}
      onConfirm={() => {
        setConfirmUninstall(false);
        void run("uninstall");
      }}
    />
    </>
  );
}

function nextFreeListen(rows: HaproxyRoute[]): number {
  const used = new Set(rows.map((r) => r.listen));
  for (const candidate of [443, 8443, 2096, 2053, 2083, 2087, 2095, 8080, 8880]) {
    if (!used.has(candidate)) return candidate;
  }
  let port = 10443;
  while (used.has(port) && port < 65535) port += 1;
  return port;
}

function Stat({ label, value, ok }: { label: string; value: string; ok?: boolean }) {
  const color =
    ok === true ? "text-[var(--success)]" : ok === false ? "text-[var(--danger)]" : "text-[var(--text)]";
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wide text-[var(--muted)]">{label}</div>
      <div className={`truncate font-mono ${color}`} title={value}>
        {value}
      </div>
    </div>
  );
}

const inputCls =
  "w-full rounded-lg border border-[var(--border)] bg-[var(--bg)] px-2 py-1.5 text-sm text-[var(--text)] outline-none focus:border-[var(--accent)]";
const btnPrimary =
  "rounded-lg bg-[var(--accent)] px-4 py-2 text-sm font-semibold text-[#06221e] hover:brightness-110 disabled:opacity-50";
const btnGhost =
  "rounded-lg border border-[var(--border)] px-4 py-2 text-sm text-[var(--muted)] hover:text-[var(--text)] disabled:opacity-50";
const btnDanger =
  "rounded-lg border border-[rgba(240,113,120,0.45)] bg-[rgba(240,113,120,0.1)] px-4 py-2 text-sm font-semibold text-[var(--danger)] disabled:opacity-50";
