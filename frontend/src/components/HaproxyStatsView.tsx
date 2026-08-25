import type { ReactNode } from "react";
import type { HaproxyLiveStats, HaproxySession, HaproxyStatRow } from "../api/client";
import { formatBytes, HaproxyCharts, MiniSpark, useHaproxyHistory } from "./HaproxyCharts";

export { formatBytes };

type Props = {
  stats: HaproxyLiveStats | null;
  loading?: boolean;
  error?: string | null;
  onRefresh?: () => void;
  showSessions?: boolean;
  title?: string;
};

function formatLast(sec: number | null | undefined): string {
  if (sec == null || sec < 0) return "—";
  if (sec < 60) return `${sec}с`;
  if (sec < 3600) return `${Math.round(sec / 60)}м`;
  return `${Math.round(sec / 3600)}ч`;
}

function num(n: number | null | undefined): string {
  return n == null ? "—" : String(n);
}

export function HaproxyStatsView({
  stats,
  loading,
  error,
  onRefresh,
  showSessions = false,
  title,
}: Props) {
  const history = useHaproxyHistory(stats);
  return (
    <div className="space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          {title && <h3 className="text-sm font-semibold text-[var(--text)]">{title}</h3>}
          {stats?.uptime && (
            <p className="text-[11px] text-[var(--muted)]">uptime {stats.uptime}</p>
          )}
        </div>
        {onRefresh && (
          <button
            type="button"
            onClick={onRefresh}
            disabled={loading}
            className="shrink-0 rounded-lg border border-[var(--border)] px-2.5 py-1 text-[11px] text-[var(--muted)] hover:text-[var(--text)] disabled:opacity-50"
          >
            {loading ? "Снимаю…" : "Обновить"}
          </button>
        )}
      </div>

      {error && (
        <div className="rounded-lg border border-[rgba(240,113,120,0.35)] bg-[rgba(240,113,120,0.08)] px-3 py-2 text-xs text-[var(--danger)]">
          {error}
        </div>
      )}

      {loading && !stats && (
        <div className="text-sm text-[var(--muted)]">Читаю admin.sock…</div>
      )}

      {stats && (
        <>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
            <Card
              label="сейчас"
              value={num(stats.curr_conns)}
              spark={<MiniSpark values={history.map((p) => p.curr_conns)} />}
            />
            <Card label="всего сессий" value={num(stats.cum_conns)} />
            <Card
              label="conn/s"
              value={num(stats.conn_rate)}
              spark={<MiniSpark values={history.map((p) => p.conn_rate)} color="#e6a23c" />}
            />
            <Card label="вход" value={formatBytes(stats.bin)} />
            <Card label="выход" value={formatBytes(stats.bout)} />
          </div>

          <HaproxyCharts stats={stats} history={history} />

          <div className="overflow-x-auto rounded-lg border border-[var(--border)]">
            <table className="w-full min-w-[720px] text-left text-[12px]">
              <thead className="text-[10px] uppercase tracking-wide text-[var(--muted)]">
                <tr className="border-b border-[var(--border)]">
                  <th className="px-2 py-1.5 font-medium">proxy</th>
                  <th className="px-2 py-1.5 font-medium">role</th>
                  <th className="px-2 py-1.5 font-medium">status</th>
                  <th className="px-2 py-1.5 font-medium text-right">сейчас</th>
                  <th className="px-2 py-1.5 font-medium text-right">пик</th>
                  <th className="px-2 py-1.5 font-medium text-right">всего</th>
                  <th className="px-2 py-1.5 font-medium text-right">вход</th>
                  <th className="px-2 py-1.5 font-medium text-right">выход</th>
                  <th className="px-2 py-1.5 font-medium text-right">rate</th>
                  <th className="px-2 py-1.5 font-medium text-right">err</th>
                  <th className="px-2 py-1.5 font-medium text-right">тишина</th>
                </tr>
              </thead>
              <tbody>
                {stats.rows.length === 0 ? (
                  <tr>
                    <td colSpan={11} className="px-2 py-4 text-center text-[var(--muted)]">
                      Нет строк FRONTEND/BACKEND
                    </td>
                  </tr>
                ) : (
                  stats.rows.map((row, i) => (
                    <StatTr key={`${row.pxname}-${row.svname}-${i}`} row={row} />
                  ))
                )}
              </tbody>
            </table>
          </div>

          {stats.errors ? (
            <pre className="max-h-40 overflow-auto rounded-lg border border-[var(--border)] bg-[#070c0f] px-3 py-2 font-mono text-[11px] text-[#c8d4dc]">
              {stats.errors}
            </pre>
          ) : null}

          {showSessions && <SessionTable sessions={stats.sessions} />}
        </>
      )}
    </div>
  );
}

function Card({
  label,
  value,
  spark,
}: {
  label: string;
  value: string;
  spark?: ReactNode;
}) {
  return (
    <div className="rounded-lg border border-[var(--border)] bg-[var(--bg)] px-2.5 py-2">
      <div className="text-[10px] uppercase tracking-wide text-[var(--muted)]">{label}</div>
      <div className="mt-0.5 flex items-end justify-between gap-2">
        <div className="font-mono text-sm tabular-nums text-[var(--text)]">{value}</div>
        {spark}
      </div>
    </div>
  );
}

function StatTr({ row }: { row: HaproxyStatRow }) {
  const down = /down|nolb|maint/i.test(row.status);
  const err = (row.ereq ?? 0) + (row.econ ?? 0) + (row.eresp ?? 0);
  return (
    <tr className="border-b border-[var(--border)] last:border-0">
      <td className="px-2 py-1.5 font-mono text-[var(--text)]">{row.pxname}</td>
      <td className="px-2 py-1.5 text-[var(--muted)]">{row.svname}</td>
      <td className={`px-2 py-1.5 ${down ? "text-[var(--danger)]" : "text-[var(--success)]"}`}>
        {row.status || "—"}
      </td>
      <td className="px-2 py-1.5 text-right tabular-nums">{num(row.scur)}</td>
      <td className="px-2 py-1.5 text-right tabular-nums">{num(row.smax)}</td>
      <td className="px-2 py-1.5 text-right tabular-nums">{num(row.stot)}</td>
      <td className="px-2 py-1.5 text-right tabular-nums">{formatBytes(row.bin)}</td>
      <td className="px-2 py-1.5 text-right tabular-nums">{formatBytes(row.bout)}</td>
      <td className="px-2 py-1.5 text-right tabular-nums">
        {row.rate == null ? "—" : `${row.rate}${row.rate_max != null ? `/${row.rate_max}` : ""}`}
      </td>
      <td className={`px-2 py-1.5 text-right tabular-nums ${err ? "text-[var(--danger)]" : ""}`}>
        {err || "—"}
      </td>
      <td className="px-2 py-1.5 text-right tabular-nums text-[var(--muted)]">
        {formatLast(row.lastsess)}
      </td>
    </tr>
  );
}

function SessionTable({ sessions }: { sessions: HaproxySession[] }) {
  if (sessions.length === 0) {
    return <p className="text-xs text-[var(--muted)]">Живых сессий нет (`show sess` пуст).</p>;
  }
  return (
    <div className="overflow-x-auto rounded-lg border border-[var(--border)]">
      <table className="w-full min-w-[560px] text-left text-[12px]">
        <thead className="text-[10px] uppercase tracking-wide text-[var(--muted)]">
          <tr className="border-b border-[var(--border)]">
            <th className="px-2 py-1.5 font-medium">src</th>
            <th className="px-2 py-1.5 font-medium">frontend</th>
            <th className="px-2 py-1.5 font-medium">backend</th>
            <th className="px-2 py-1.5 font-medium">age</th>
          </tr>
        </thead>
        <tbody>
          {sessions.map((s, i) => (
            <tr key={i} className="border-b border-[var(--border)] last:border-0" title={s.raw}>
              <td className="px-2 py-1.5 font-mono">{s.src ?? "—"}</td>
              <td className="px-2 py-1.5 font-mono text-[var(--muted)]">{s.frontend ?? "—"}</td>
              <td className="px-2 py-1.5 font-mono text-[var(--muted)]">{s.backend ?? "—"}</td>
              <td className="px-2 py-1.5 tabular-nums">{s.age ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
