import { memo, useState } from "react";
import { api, type RemnaScriptAction } from "../api/client";
import type { InstallJob } from "../hooks/useAgentInstallQueue";
import { agentNeedsUpdate, remnanodeNeedsUpdate } from "../hooks/useRemnawaveVersions";
import type { AgentStatus, NodeItem, OnlineStatus, SshCheckResult } from "../types";
import { CopyButton } from "./CopyButton";
import { CountryFlag } from "./CountryFlag";
import { HostingLogo } from "./HostingLogo";
import { OnlineBadge } from "./OnlineBadge";

export type NodeListDensity = "comfortable" | "compact";

type Props = {
  node: NodeItem;
  status?: OnlineStatus;
  agent?: AgentStatus;
  selected: boolean;
  density?: NodeListDensity;
  onSelectChange: (nodeId: string, selected: boolean) => void;
  installJob?: InstallJob;
  onEdit: (node: NodeItem) => void;
  onDelete: (node: NodeItem) => void;
  onInstallAgent: (node: NodeItem) => void;
  onReboot: (node: NodeItem) => void;
  onRemnaScript?: (node: NodeItem, action: RemnaScriptAction) => void;
  latestRemnanodeVersion?: string | null;
  latestAgentVersion?: string | null;
  onOpenInstallLog: (nodeId: string) => void;
  rebooting?: boolean;
};

export const NodeRow = memo(function NodeRow({
  node,
  status,
  agent,
  selected,
  density = "comfortable",
  onSelectChange,
  installJob,
  onEdit,
  onDelete,
  onInstallAgent,
  onReboot,
  onRemnaScript,
  latestRemnanodeVersion,
  latestAgentVersion,
  onOpenInstallLog,
  rebooting,
}: Props) {
  const compact = density === "compact";
  const needsRemnaUpdate = remnanodeNeedsUpdate(agent?.remnanode_version, latestRemnanodeVersion);
  const needsAgentUpdate =
    !!agent?.present && agentNeedsUpdate(agent.version, latestAgentVersion);
  const [menuOpen, setMenuOpen] = useState(false);
  const [copyingSecret, setCopyingSecret] = useState(false);
  const [sshChecking, setSshChecking] = useState(false);
  const [sshResult, setSshResult] = useState<SshCheckResult | null>(null);

  async function copySecret() {
    setCopyingSecret(true);
    try {
      const data = await api.getSecret(node.id);
      await navigator.clipboard.writeText(data.secret);
    } catch {
      // ignore
    } finally {
      setCopyingSecret(false);
      setMenuOpen(false);
    }
  }

  async function checkSsh() {
    setSshChecking(true);
    try {
      const result = await api.sshCheck(node.id);
      setSshResult(result);
    } catch (err) {
      setSshResult({
        ok: false,
        message: err instanceof Error ? err.message : "Ошибка проверки SSH",
      });
    } finally {
      setSshChecking(false);
    }
  }

  const checkbox = (
    <input
      type="checkbox"
      checked={selected}
      onChange={(e) => onSelectChange(node.id, e.target.checked)}
      className={`accent-[var(--accent)] ${compact ? "h-3.5 w-3.5" : "h-4 w-4"}`}
      aria-label={`Выбрать ${node.name}`}
    />
  );

  const menu = (
    <div className="relative shrink-0">
      <button
        type="button"
        onClick={() => setMenuOpen((v) => !v)}
        className={`inline-flex items-center justify-center rounded-md text-[var(--muted)] hover:bg-[var(--accent-dim)] hover:text-[var(--text)] ${
          compact ? "h-6 w-6 text-sm" : "h-8 w-8"
        }`}
        aria-label="Меню"
      >
        ⋮
      </button>
      {menuOpen && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setMenuOpen(false)} />
          <div className="absolute right-0 z-20 mt-1 w-44 overflow-hidden rounded-lg border border-[var(--border)] bg-[var(--bg-elevated)] py-1 shadow-xl">
            <button
              type="button"
              className="block w-full px-3 py-2 text-left text-sm hover:bg-[var(--accent-dim)]"
              onClick={() => {
                setMenuOpen(false);
                onEdit(node);
              }}
            >
              Редактировать
            </button>
            <button
              type="button"
              className={[
                "block w-full px-3 py-2 text-left text-sm hover:bg-[var(--accent-dim)]",
                needsAgentUpdate ? "font-semibold text-[var(--accent)]" : "",
              ].join(" ")}
              onClick={() => {
                setMenuOpen(false);
                onInstallAgent(node);
              }}
              disabled={installJob?.phase === "queued" || installJob?.phase === "running"}
            >
              {needsAgentUpdate
                ? `Обновить агент${latestAgentVersion ? ` →${latestAgentVersion}` : ""}`
                : node.agent_configured
                  ? "Переустановить агент"
                  : "Установить агент"}
            </button>
            <button
              type="button"
              className="block w-full px-3 py-2 text-left text-sm hover:bg-[var(--accent-dim)] disabled:opacity-50"
              onClick={() => {
                setMenuOpen(false);
                onReboot(node);
              }}
              disabled={rebooting}
            >
              {rebooting ? "Перезагрузка…" : "Перезагрузить"}
            </button>
            {onRemnaScript && (
              <>
                <div className="my-1 border-t border-[var(--border)]" />
                <button
                  type="button"
                  className="block w-full px-3 py-2 text-left text-sm hover:bg-[var(--accent-dim)]"
                  onClick={() => {
                    setMenuOpen(false);
                    onRemnaScript(node, "install");
                  }}
                >
                  RemnaNode: установить
                </button>
                <button
                  type="button"
                  className="block w-full px-3 py-2 text-left text-sm hover:bg-[var(--accent-dim)]"
                  onClick={() => {
                    setMenuOpen(false);
                    onRemnaScript(node, "reinstall");
                  }}
                >
                  RemnaNode: переустановить
                </button>
                <button
                  type="button"
                  className="block w-full px-3 py-2 text-left text-sm hover:bg-[var(--accent-dim)]"
                  onClick={() => {
                    setMenuOpen(false);
                    onRemnaScript(node, "tune");
                  }}
                >
                  RemnaNode: параметры
                </button>
                <button
                  type="button"
                  className={[
                    "block w-full px-3 py-2 text-left text-sm hover:bg-[var(--accent-dim)]",
                    needsRemnaUpdate ? "font-semibold text-[var(--accent)]" : "",
                  ].join(" ")}
                  onClick={() => {
                    setMenuOpen(false);
                    onRemnaScript(node, "update");
                  }}
                >
                  RemnaNode: обновить
                  {needsRemnaUpdate && agent?.remnanode_version && latestRemnanodeVersion
                    ? ` (${agent.remnanode_version}→${latestRemnanodeVersion})`
                    : ""}
                </button>
              </>
            )}
            <button
              type="button"
              className="block w-full px-3 py-2 text-left text-sm text-[var(--danger)] hover:bg-[rgba(240,113,120,0.08)]"
              onClick={() => {
                setMenuOpen(false);
                onDelete(node);
              }}
            >
              Удалить
            </button>
          </div>
        </>
      )}
    </div>
  );

  const authBlock = (
    <div className={`text-[var(--muted)] ${compact ? "text-[10px]" : "text-xs"}`}>
      {compact ? (node.auth_type === "password" ? "pwd" : "key") : node.auth_type === "password" ? "Пароль" : "SSH ключ"}
      {(node.has_password || node.has_private_key) && (
        <button
          type="button"
          onClick={() => void copySecret()}
          disabled={copyingSecret}
          className="ml-1.5 inline-flex align-middle text-[var(--accent)] hover:underline disabled:opacity-50"
          title="Копировать секрет"
        >
          <CopyIcon />
        </button>
      )}
    </div>
  );

  const sshBlock = (
    <div className="flex min-w-0 items-center gap-1.5">
      <button
        type="button"
        onClick={() => void checkSsh()}
        disabled={sshChecking}
        className={`shrink-0 rounded-md border border-[var(--border)] text-[var(--muted)] transition hover:border-[var(--accent)] hover:text-[var(--accent)] disabled:opacity-50 ${
          compact ? "px-1.5 py-0.5 text-[10px]" : "px-2 py-1 text-[11px]"
        }`}
      >
        {sshChecking ? "…" : "SSH"}
      </button>
      {sshResult && (
        <span
          className={`truncate font-semibold ${compact ? "text-[10px]" : "text-[11px]"} ${
            sshResult.ok ? "text-[var(--success)]" : "text-[var(--danger)]"
          }`}
          title={sshResult.message}
        >
          {sshResult.ok ? "OK" : "fail"}
        </span>
      )}
    </div>
  );

  const versionsBlock = (
    <VersionsCell
      agent={agent}
      compact={compact}
      needsRemnaUpdate={needsRemnaUpdate}
      latestRemnanodeVersion={latestRemnanodeVersion}
      onUpdateRemna={onRemnaScript ? () => onRemnaScript(node, "update") : undefined}
      needsAgentUpdate={needsAgentUpdate}
      latestAgentVersion={latestAgentVersion}
      onUpdateAgent={() => onInstallAgent(node)}
    />
  );

  const agentBlock = (
    <AgentCell
      node={node}
      agent={agent}
      installJob={installJob}
      compact={compact}
      onInstall={() => onInstallAgent(node)}
      onOpenLog={() => onOpenInstallLog(node.id)}
    />
  );

  const desktopGrid = compact
    ? "lg:grid lg:grid-cols-[28px_56px_minmax(100px,1fr)_minmax(100px,1fr)_48px_minmax(92px,auto)_minmax(110px,1fr)_minmax(64px,auto)_36px] lg:gap-2"
    : "lg:grid lg:grid-cols-[32px_120px_minmax(140px,1fr)_minmax(130px,1fr)_90px_minmax(110px,auto)_minmax(140px,1.1fr)_minmax(100px,auto)_44px] lg:gap-3";

  return (
    <>
      <div
        className={[
          "hidden items-center rounded-[var(--radius)] border bg-[var(--bg-row)] transition hover:bg-[var(--bg-row-hover)]",
          desktopGrid,
          compact ? "px-2.5 py-1.5" : "px-4 py-3",
          selected ? "border-[var(--accent)]" : "border-[var(--border)]",
        ].join(" ")}
      >
        {checkbox}
        <OnlineBadge status={status} compact={compact} />

        <div className="min-w-0">
          <div className="flex items-center gap-1.5">
            {node.country_code && <CountryFlag code={node.country_code} size={compact ? 12 : 14} />}
            <div className={`truncate text-[var(--text)] ${compact ? "text-xs font-medium" : "font-medium"}`}>
              {node.name}
            </div>
          </div>
          {!compact && (
            <div className="mt-0.5 truncate text-xs text-[var(--muted)]">
              {node.ssh_user}@{node.host}:{node.ssh_port}
            </div>
          )}
        </div>

        <div className="flex min-w-0 items-center gap-1.5">
          {node.hosting_name &&
            (compact ? (
              <span title={node.hosting_name} className="shrink-0">
                <HostingLogo name={node.hosting_name} faviconData={node.hosting_favicon_data} size={14} />
              </span>
            ) : (
              <span className="inline-flex min-w-0 max-w-[140px] shrink-0 items-center gap-1.5 rounded-full border border-[rgba(34,211,187,0.35)] bg-[var(--accent-dim)] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-[var(--accent)]">
                <HostingLogo name={node.hosting_name} faviconData={node.hosting_favicon_data} size={14} />
                {node.hosting_website_url ? (
                  <a
                    href={node.hosting_website_url}
                    target="_blank"
                    rel="noreferrer"
                    className="truncate hover:underline"
                    title={node.hosting_website_url}
                    onClick={(e) => e.stopPropagation()}
                  >
                    {node.hosting_name}
                  </a>
                ) : (
                  <span className="truncate">{node.hosting_name}</span>
                )}
              </span>
            ))}
          <span className={`truncate font-mono text-[var(--text)] ${compact ? "text-xs" : "text-sm"}`}>
            {node.host}
          </span>
          {!compact && <CopyButton value={node.host} title="Копировать IP / host" />}
        </div>

        {authBlock}
        {versionsBlock}
        {agentBlock}
        {sshBlock}
        <div className="justify-self-end">{menu}</div>
      </div>

      {/* Mobile */}
      {compact ? (
        <div
          className={[
            "flex items-center gap-2 rounded-md border bg-[var(--bg-row)] px-2 py-1.5 lg:hidden",
            selected ? "border-[var(--accent)]" : "border-[var(--border)]",
          ].join(" ")}
        >
          {checkbox}
          <OnlineBadge status={status} compact />
          {node.country_code && <CountryFlag code={node.country_code} size={12} />}
          <div className="min-w-0 flex-1">
            <div className="truncate text-xs font-medium text-[var(--text)]">{node.name}</div>
            <div className="truncate font-mono text-[10px] text-[var(--muted)]">{node.host}</div>
          </div>
          <div className="hidden min-w-0 max-w-[40%] sm:block">{agentBlock}</div>
          {menu}
        </div>
      ) : (
        <div
          className={[
            "rounded-[var(--radius)] border bg-[var(--bg-row)] p-3 lg:hidden",
            selected ? "border-[var(--accent)]" : "border-[var(--border)]",
          ].join(" ")}
        >
          <div className="flex items-start justify-between gap-2">
            <div className="flex min-w-0 flex-1 items-start gap-2">
              <div className="pt-1">{checkbox}</div>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <OnlineBadge status={status} />
                  {node.country_code && <CountryFlag code={node.country_code} size={14} />}
                  <div className="truncate font-medium text-[var(--text)]">{node.name}</div>
                </div>
                <div className="mt-1 truncate text-xs text-[var(--muted)]">
                  {node.ssh_user}@{node.host}:{node.ssh_port}
                </div>
              </div>
            </div>
            {menu}
          </div>

          <div className="mt-3 flex flex-wrap items-center gap-2">
            {node.hosting_name && (
              <span className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-[rgba(34,211,187,0.35)] bg-[var(--accent-dim)] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-[var(--accent)]">
                <HostingLogo name={node.hosting_name} faviconData={node.hosting_favicon_data} size={14} />
                <span className="truncate">{node.hosting_name}</span>
              </span>
            )}
            <span className="inline-flex items-center gap-1 font-mono text-sm text-[var(--text)]">
              {node.host}
              <CopyButton value={node.host} title="Копировать IP / host" />
            </span>
            {authBlock}
          </div>

          <div className="mt-3 grid grid-cols-2 gap-3 border-t border-[var(--border)] pt-3">
            <div>{versionsBlock}</div>
            <div>{agentBlock}</div>
          </div>

          <div className="mt-3 flex items-center justify-between gap-2">{sshBlock}</div>
        </div>
      )}
    </>
  );
});

function VersionsCell({
  agent,
  compact,
  needsRemnaUpdate,
  latestRemnanodeVersion,
  onUpdateRemna,
  needsAgentUpdate,
  latestAgentVersion,
  onUpdateAgent,
}: {
  agent?: AgentStatus;
  compact: boolean;
  needsRemnaUpdate?: boolean;
  latestRemnanodeVersion?: string | null;
  onUpdateRemna?: () => void;
  needsAgentUpdate?: boolean;
  latestAgentVersion?: string | null;
  onUpdateAgent?: () => void;
}) {
  const text = compact ? "text-[10px]" : "text-[11px]";
  const btnCls =
    "shrink-0 rounded border border-[var(--accent)] bg-[var(--accent-dim)] px-1 py-0.5 text-[9px] font-semibold text-[var(--accent)] transition hover:brightness-110";

  if (!agent?.present) {
    return <span className={`text-[var(--muted)] ${text}`}>—</span>;
  }

  const rn = agent.remnanode_version;
  const ag = agent.version;
  const rnUnknown = !rn || rn === "0.0.0" || rn === "0.0.1" || rn === "latest";
  const rnLabel = rnUnknown
    ? agent.remnanode_running === false
      ? "off"
      : agent.remnanode_running
        ? "?"
        : "—"
    : `v${rn}`;

  return (
    <div className={`min-w-0 ${text}`}>
      <div className="flex items-center gap-1">
        <span className="shrink-0 text-[var(--muted)]">{compact ? "N" : "Нода"}</span>
        <span
          className={`truncate tabular-nums ${needsRemnaUpdate ? "text-[var(--warning)]" : "text-[var(--text)]"}`}
          title={
            !rnUnknown
              ? needsRemnaUpdate && latestRemnanodeVersion
                ? `RemnaNode ${rn} → ${latestRemnanodeVersion}`
                : `RemnaNode v${rn}`
              : agent.remnanode_running === false
                ? "RemnaNode stopped"
                : agent.remnanode_running
                  ? "RemnaNode запущен; версия читается из баннера «Remnawave Node v…» — обновите агент до 0.1.4+"
                  : "RemnaNode: нет данных"
          }
        >
          {rnLabel}
        </span>
        {needsRemnaUpdate && onUpdateRemna && (
          <button type="button" onClick={onUpdateRemna} className={btnCls} title="Обновить RemnaNode">
            ↑
          </button>
        )}
      </div>
      <div className="mt-0.5 flex items-center gap-1">
        <span className="shrink-0 text-[var(--muted)]">{compact ? "A" : "Агент"}</span>
        <span
          className={`truncate tabular-nums ${needsAgentUpdate ? "text-[var(--warning)]" : "text-[var(--text)]"}`}
          title={
            ag
              ? needsAgentUpdate && latestAgentVersion
                ? `Агент ${ag} → ${latestAgentVersion}`
                : `Агент v${ag}`
              : "Агент: нет версии"
          }
        >
          {ag ? `v${ag}` : "—"}
        </span>
        {needsAgentUpdate && onUpdateAgent && (
          <button type="button" onClick={onUpdateAgent} className={btnCls} title="Обновить агент">
            ↑
          </button>
        )}
      </div>
    </div>
  );
}

function AgentCell({
  node,
  agent,
  installJob,
  compact,
  onInstall,
  onOpenLog,
}: {
  node: NodeItem;
  agent?: AgentStatus;
  installJob?: InstallJob;
  compact: boolean;
  onInstall: () => void;
  onOpenLog: () => void;
}) {
  const text = compact ? "text-[10px]" : "text-[11px]";

  if (installJob && (installJob.phase === "queued" || installJob.phase === "running")) {
    return (
      <div className="min-w-0">
        <button
          type="button"
          onClick={onOpenLog}
          className={`font-semibold text-[var(--accent)] hover:underline ${text}`}
        >
          {installJob.phase === "queued" ? "Очередь…" : "Установка…"}
        </button>
      </div>
    );
  }

  if (installJob?.phase === "error") {
    return (
      <button
        type="button"
        onClick={onOpenLog}
        className={`font-semibold text-[var(--danger)] hover:underline ${text}`}
        title={installJob.statusMessage ?? undefined}
      >
        Ошибка
      </button>
    );
  }

  if (!node.agent_configured) {
    return (
      <button
        type="button"
        onClick={onInstall}
        className={`justify-self-start rounded-md border border-[var(--accent)] bg-[var(--accent-dim)] font-semibold text-[var(--accent)] transition hover:brightness-110 ${
          compact ? "px-1.5 py-0.5 text-[10px]" : "px-2 py-1 text-[11px]"
        }`}
      >
        {compact ? "Агент +" : "Установить агент"}
      </button>
    );
  }

  if (!agent) {
    return <span className={`text-[var(--muted)] ${text}`}>Агент…</span>;
  }

  if (!agent.present) {
    const err = agent.error ?? "Нет ответа";
    const low = err.toLowerCase();
    const tokenMismatch = low.includes("токен") || low.includes("авторизац");
    const portClosed =
      low.includes("порт") || low.includes("security") || low.includes("firewall");

    const headline = tokenMismatch
      ? "Токен"
      : portClosed
        ? "Порт"
        : compact
          ? "Offline"
          : "Агент offline";

    if (compact) {
      return (
        <button
          type="button"
          onClick={onInstall}
          className={`truncate font-semibold hover:underline ${text} ${
            tokenMismatch ? "text-[var(--warning)]" : "text-[var(--danger)]"
          }`}
          title={err}
        >
          {headline}
        </button>
      );
    }

    const detail = tokenMismatch
      ? err.includes("автосинхронизация") || err.includes("прочитан") || err.includes("перезапущен")
        ? err
        : "Секрет устарел — идёт автосинхронизация по SSH или переустановите"
      : portClosed
        ? `TCP ${node.agent_port || 7422} недоступен с панели (SG/firewall)`
        : err;

    return (
      <div className="min-w-0">
        <div
          className={`text-[11px] font-semibold ${
            tokenMismatch ? "text-[var(--warning)]" : "text-[var(--danger)]"
          }`}
          title={err}
        >
          {tokenMismatch ? "Токен не совпадает" : portClosed ? "Порт закрыт" : "Агент offline"}
        </div>
        <div className="mt-0.5 text-[10px] leading-snug text-[var(--muted)] [overflow-wrap:anywhere]" title={err}>
          {detail}
        </div>
        <button type="button" onClick={onInstall} className="mt-0.5 text-[10px] text-[var(--accent)] hover:underline">
          Переустановить
        </button>
      </div>
    );
  }

  const title = [
    agent.version ? `agent v${agent.version}` : null,
    agent.remnanode_version ? `RemnaNode v${agent.remnanode_version}` : null,
    `CPU ${fmt(agent.cpu_percent)}%`,
    `RAM ${fmt(agent.mem_percent)}%`,
    `Disk ${fmt(agent.disk_percent)}%`,
    agent.loadavg?.length ? `load ${agent.loadavg.map((x) => x.toFixed(2)).join(" · ")}` : null,
    agent.error,
  ]
    .filter(Boolean)
    .join(" · ");

  if (compact) {
    return (
      <div className={`min-w-0 truncate tabular-nums text-[var(--text)] ${text}`} title={title}>
        {fmt(agent.cpu_percent)}/{fmt(agent.mem_percent)}/{fmt(agent.disk_percent)}
      </div>
    );
  }

  return (
    <div className="min-w-0 text-[11px]" title={title || "Агент online"}>
      <div className="font-medium text-[var(--text)] [overflow-wrap:anywhere]">
        CPU {fmt(agent.cpu_percent)}% · RAM {fmt(agent.mem_percent)}% · Disk {fmt(agent.disk_percent)}%
      </div>
      {agent.loadavg && agent.loadavg.length > 0 && (
        <div className="mt-0.5 text-[10px] text-[var(--muted)]">
          load {agent.loadavg.map((x) => x.toFixed(2)).join(" · ")}
        </div>
      )}
    </div>
  );
}

function fmt(v: number | null | undefined): string {
  return v == null || Number.isNaN(v) ? "—" : String(Math.round(v));
}

function CopyIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="inline">
      <rect x="9" y="9" width="13" height="13" rx="2" />
      <path d="M5 15V5a2 2 0 0 1 2-2h10" />
    </svg>
  );
}
