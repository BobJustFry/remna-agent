import { useCallback, useEffect, useRef, useState } from "react";
import { api, type AuditCheck, type AuditFinding, type AuditStatus } from "../api/client";
import { NodeWatchCard } from "../components/NodeWatchCard";
import { PanelBackupCard } from "../components/PanelBackupCard";

const SEV_ORDER: Record<string, number> = { crit: 0, warn: 1, info: 2 };

const SEV_STYLE: Record<string, { label: string; cls: string }> = {
  crit: { label: "Критично", cls: "border-[rgba(240,113,120,0.45)] bg-[rgba(240,113,120,0.10)]" },
  warn: { label: "Внимание", cls: "border-[rgba(251,191,36,0.45)] bg-[rgba(251,191,36,0.10)]" },
  info: { label: "К сведению", cls: "border-[var(--border)] bg-[var(--bg-row)]" },
};

function fmtTime(iso: string | null | undefined) {
  if (!iso) return "не запускалась";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString();
}

export function AutomationPage() {
  const [status, setStatus] = useState<AuditStatus | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [domains, setDomains] = useState("");
  const [savingDomains, setSavingDomains] = useState(false);
  const pollRef = useRef<number | null>(null);

  const load = useCallback(async (syncDomains: boolean) => {
    try {
      const s = await api.auditChecks();
      setStatus(s);
      if (syncDomains) setDomains(s.cert_domains.join("\n"));
      return s;
    } catch (err) {
      setMsg(err instanceof Error ? err.message : "Не удалось получить состояние");
      return null;
    }
  }, []);

  useEffect(() => {
    void load(true);
    return () => {
      if (pollRef.current) window.clearInterval(pollRef.current);
    };
  }, [load]);

  // Пока хоть одна проверка идёт — подтягиваем результат, потом останавливаемся.
  useEffect(() => {
    if (!status?.any_running) {
      if (pollRef.current) {
        window.clearInterval(pollRef.current);
        pollRef.current = null;
      }
      return;
    }
    if (pollRef.current) return;
    pollRef.current = window.setInterval(() => void load(false), 1500);
  }, [status?.any_running, load]);

  async function runOne(id: string) {
    setMsg(null);
    try {
      setStatus(await api.auditRun(id));
    } catch (err) {
      setMsg(err instanceof Error ? err.message : "Не удалось запустить проверку");
    }
  }

  async function runAll() {
    if (!status) return;
    setMsg(null);
    for (const c of status.checks) {
      try {
        setStatus(await api.auditRun(c.id));
      } catch {
        /* по одной проверке молчим: ошибку покажет её собственная карточка */
      }
    }
  }

  async function saveDomains() {
    setSavingDomains(true);
    setMsg(null);
    try {
      const list = domains.split("\n").map((d) => d.trim()).filter(Boolean);
      const s = await api.auditUpdateSettings({ cert_domains: list });
      setDomains(s.cert_domains.join("\n"));
      setMsg("Список доменов сохранён");
    } catch (err) {
      setMsg(err instanceof Error ? err.message : "Не удалось сохранить");
    } finally {
      setSavingDomains(false);
    }
  }

  const totals = (status?.checks ?? []).reduce(
    (acc, c) => {
      acc.crit += c.result?.crit ?? 0;
      acc.warn += c.result?.warn ?? 0;
      acc.ran += c.result?.ran_at ? 1 : 0;
      return acc;
    },
    { crit: 0, warn: 0, ran: 0 },
  );

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden">
      <header className="border-b border-[var(--border)] px-4 py-3 sm:px-6 sm:py-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="hidden text-xl font-semibold tracking-tight lg:block">Автоматизация</h1>
            <p className="text-sm text-[var(--muted)] lg:mt-0.5">
              Проверки состояния парка. Только читают панель и сеть — ничего не меняют.
            </p>
          </div>
          <button
            type="button"
            disabled={!status || status.any_running}
            onClick={() => void runAll()}
            className="rounded-lg bg-[var(--accent)] px-4 py-2 text-sm font-semibold text-[#06221e] disabled:opacity-50"
          >
            {status?.any_running ? "Идёт проверка…" : "Проверить всё"}
          </button>
        </div>
      </header>

      <div className="flex-1 overflow-auto px-3 py-3 sm:px-6 sm:py-4">
        <div className="mx-auto max-w-3xl space-y-4">
          <section className="rounded-[var(--radius)] border border-[rgba(251,191,36,0.35)] bg-[rgba(251,191,36,0.07)] p-4">
            <h2 className="text-sm font-semibold">Фоновый запуск выключен</h2>
            <p className="mt-1 text-xs text-[var(--muted)]">
              Расписания нет: проверки выполняются только по кнопке. Так и задумано, пока
              вы не решите, что им можно ходить самим. Когда решите — добавляется интервал,
              сам код проверок уже готов и меняться не будет.
            </p>
          </section>

          {status && totals.ran > 0 && (
            <div className="flex flex-wrap gap-3 text-sm">
              <Badge tone={totals.crit ? "crit" : "ok"}>Критичных: {totals.crit}</Badge>
              <Badge tone={totals.warn ? "warn" : "ok"}>Требует внимания: {totals.warn}</Badge>
              <Badge tone="muted">
                Проверок выполнено: {totals.ran} из {status.checks.length}
              </Badge>
            </div>
          )}

          <NodeWatchCard />

          <PanelBackupCard />

          {(status?.checks ?? []).map((c) => (
            <CheckCard key={c.id} check={c} onRun={() => void runOne(c.id)} />
          ))}

          <section className="rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-elevated)] p-4">
            <h2 className="text-sm font-semibold">Домены для проверки сертификата</h2>
            <p className="mt-1 text-xs text-[var(--muted)]">
              Хосты панели с настоящим TLS проверяются сами. Сюда добавьте то, чего в панели
              нет, — например домен подписки: его сертификат на CDN продлевается руками, и
              пропущенный срок кладёт выдачу подписок всем сразу. По одному домену в строке.
            </p>
            <textarea
              value={domains}
              onChange={(e) => setDomains(e.target.value)}
              rows={3}
              spellCheck={false}
              placeholder="sub.example.com"
              className="mt-3 w-full rounded-lg border border-[var(--border)] bg-[var(--bg)] px-3 py-2 font-mono text-sm text-[var(--text)] outline-none focus:border-[var(--accent)]"
            />
            <button
              type="button"
              disabled={savingDomains}
              onClick={() => void saveDomains()}
              className="mt-3 rounded-lg border border-[var(--border)] px-3 py-1.5 text-sm text-[var(--text)] hover:bg-[var(--bg-row)] disabled:opacity-50"
            >
              {savingDomains ? "Сохранение…" : "Сохранить"}
            </button>
          </section>

          {msg && <p className="text-xs text-[var(--muted)]">{msg}</p>}
        </div>
      </div>
    </div>
  );
}

function CheckCard({ check, onRun }: { check: AuditCheck; onRun: () => void }) {
  const [open, setOpen] = useState(false);
  const r = check.result;
  const findings = [...(r?.findings ?? [])].sort(
    (a, b) => (SEV_ORDER[a.severity] ?? 9) - (SEV_ORDER[b.severity] ?? 9),
  );
  const clean = r && !r.error && findings.length === 0;

  return (
    <section className="rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-elevated)] p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold">{check.title}</h2>
          <p className="mt-1 text-xs text-[var(--muted)]">{check.what}</p>
        </div>
        <button
          type="button"
          disabled={check.running}
          onClick={onRun}
          className="shrink-0 rounded-lg border border-[var(--border)] px-3 py-1.5 text-sm text-[var(--text)] hover:bg-[var(--bg-row)] disabled:opacity-50"
        >
          {check.running ? "Идёт…" : "Проверить"}
        </button>
      </div>

      <details className="mt-2" open={open} onToggle={(e) => setOpen(e.currentTarget.open)}>
        <summary className="cursor-pointer text-xs text-[var(--muted)] hover:text-[var(--text)]">
          Откуда взялась проверка
        </summary>
        <p className="mt-1 text-xs leading-relaxed text-[var(--muted)]">{check.why}</p>
      </details>

      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-[var(--muted)]">
        <span>Запуск: {fmtTime(r?.ran_at)}</span>
        {r?.ran_at && <span>проверено объектов: {r.checked}</span>}
        {r?.duration_ms ? <span>за {(r.duration_ms / 1000).toFixed(1)} с</span> : null}
      </div>

      {r?.error && (
        <p className="mt-3 rounded-lg border border-[rgba(240,113,120,0.45)] bg-[rgba(240,113,120,0.10)] px-3 py-2 text-xs text-[var(--danger)]">
          {r.error}
        </p>
      )}

      {clean && (
        <p className="mt-3 text-sm text-[var(--success)]">Ничего не найдено — всё в порядке.</p>
      )}

      {findings.length > 0 && (
        <ul className="mt-3 space-y-2">
          {findings.map((f, i) => (
            <FindingRow key={`${f.target}-${i}`} finding={f} />
          ))}
        </ul>
      )}
    </section>
  );
}

function FindingRow({ finding }: { finding: AuditFinding }) {
  const style = SEV_STYLE[finding.severity] ?? SEV_STYLE.info;
  return (
    <li className={`rounded-lg border px-3 py-2 ${style.cls}`}>
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <span className="text-[10px] uppercase tracking-wide text-[var(--muted)]">
          {style.label}
        </span>
        <span className="text-sm font-medium">{finding.title}</span>
      </div>
      <div className="mt-0.5 break-words text-xs text-[var(--muted)]">{finding.target}</div>
      {finding.detail && (
        <p className="mt-1 text-xs leading-relaxed text-[var(--muted)]">{finding.detail}</p>
      )}
    </li>
  );
}

function Badge({ tone, children }: { tone: "crit" | "warn" | "ok" | "muted"; children: React.ReactNode }) {
  const cls =
    tone === "crit"
      ? "border-[rgba(240,113,120,0.45)] text-[var(--danger)]"
      : tone === "warn"
        ? "border-[rgba(251,191,36,0.45)] text-[var(--warning)]"
        : tone === "ok"
          ? "border-[rgba(52,211,153,0.45)] text-[var(--success)]"
          : "border-[var(--border)] text-[var(--muted)]";
  return <span className={`rounded-full border px-3 py-1 text-xs ${cls}`}>{children}</span>;
}
