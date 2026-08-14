import { useState } from "react";
import type { NodeItem } from "../types";

export type InstallIntent = "install" | "reinstall";

type Props = {
  nodes: NodeItem[];
  intent?: InstallIntent;
  onClose: () => void;
  onConfirm: (installDeps: boolean) => void;
};

export function AgentInstallConfirmDialog({
  nodes,
  intent = "install",
  onClose,
  onConfirm,
}: Props) {
  const [installDeps, setInstallDeps] = useState(true);
  if (nodes.length === 0) return null;

  const bulk = nodes.length > 1;
  const reinstall = intent === "reinstall" || (!bulk && nodes[0].agent_configured);
  const reinstallCount = nodes.filter((n) => n.agent_configured).length;

  const title = bulk
    ? reinstall
      ? `Переустановить агент на ${nodes.length} нод?`
      : `Установить агент на ${nodes.length} нод?`
    : reinstall
      ? "Переустановить агент?"
      : "Установить агент?";

  return (
    <div className="fixed inset-0 z-[60] flex items-end justify-center bg-black/60 p-0 backdrop-blur-sm sm:items-center sm:p-4">
      <div
        role="dialog"
        aria-modal="true"
        className="flex max-h-[min(100dvh,720px)] w-full max-w-lg flex-col overflow-hidden rounded-t-[var(--radius)] border border-[var(--border)] bg-[var(--bg-elevated)] shadow-2xl sm:rounded-[var(--radius)]"
      >
        <div className="border-b border-[var(--border)] px-5 py-4">
          <h2 className="text-base font-semibold text-[var(--text)]">{title}</h2>
          {!bulk && (
            <p className="mt-1 font-mono text-xs text-[var(--muted)]">
              {nodes[0].name} · {nodes[0].ssh_user}@{nodes[0].host}:{nodes[0].ssh_port}
            </p>
          )}
        </div>

        <div className="space-y-4 overflow-y-auto px-5 py-4 text-sm text-[var(--muted)]">
          <p>
            На сервер{bulk ? "а" : ""} по SSH будет{" "}
            {reinstall ? "переустановлен" : "установлен"}{" "}
            <span className="text-[var(--text)]">remna-node-agent</span> (systemd, порт 7422).
            Установка идёт в фоне — можно закрыть окно и следить за прогрессом в панели снизу.
          </p>
          {bulk && (
            <div className="max-h-40 overflow-auto rounded-lg border border-[var(--border)] bg-[var(--bg)] px-3 py-2">
              <ul className="space-y-1 text-xs">
                {nodes.map((n) => (
                  <li key={n.id} className="flex justify-between gap-2">
                    <span className="truncate text-[var(--text)]">{n.name}</span>
                    <span className="shrink-0 font-mono text-[var(--muted)]">{n.host}</span>
                  </li>
                ))}
              </ul>
              {reinstallCount > 0 && intent !== "reinstall" && (
                <p className="mt-2 text-[11px] text-[var(--warning)]">
                  У {reinstallCount} нод агент уже есть — будет переустановка.
                </p>
              )}
            </div>
          )}
          <label className="flex cursor-pointer items-start gap-3 rounded-lg border border-[var(--border)] bg-[var(--bg)] px-3 py-3">
            <input
              type="checkbox"
              checked={installDeps}
              onChange={(e) => setInstallDeps(e.target.checked)}
              className="mt-0.5 h-4 w-4 accent-[var(--accent)]"
            />
            <span>
              <span className="block text-[var(--text)]">Установить зависимости, если отсутствуют</span>
              <span className="mt-0.5 block text-xs">
                python3 через apt/dnf/yum/apk. Без галочки установка остановится, если python3 нет.
              </span>
            </span>
          </label>
          {bulk && (
            <p className="text-xs">Одновременно устанавливаем не больше 2 нод, остальные ждут в очереди.</p>
          )}
        </div>

        <div className="flex justify-end gap-2 border-t border-[var(--border)] px-5 py-4">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-[var(--border)] px-4 py-2 text-sm text-[var(--muted)] transition hover:text-[var(--text)]"
          >
            Отмена
          </button>
          <button
            type="button"
            onClick={() => onConfirm(installDeps)}
            className="rounded-lg bg-[var(--accent)] px-4 py-2 text-sm font-semibold text-[#06221e] transition hover:brightness-110"
          >
            {bulk
              ? `${reinstall ? "Переустановить" : "Установить"} (${nodes.length})`
              : reinstall
                ? "Переустановить"
                : "Установить"}
          </button>
        </div>
      </div>
    </div>
  );
}
