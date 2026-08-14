type TrayJob = {
  nodeId: string;
  nodeName: string;
  host: string;
  phase: "queued" | "running" | "done" | "error";
};

type Props = {
  jobs: TrayJob[];
  activeCount: number;
  doneCount: number;
  errorCount: number;
  title?: string;
  onOpen: (nodeId: string) => void;
  onCancel: (nodeId: string) => void;
  onDismissFinished: () => void;
};

export function AgentInstallTray({
  jobs,
  activeCount,
  doneCount,
  errorCount,
  title = "Установка агентов",
  onOpen,
  onCancel,
  onDismissFinished,
}: Props) {
  if (jobs.length === 0) return null;

  return (
    <div className="shrink-0 border-t border-[var(--border)] bg-[var(--sidebar)] px-3 py-2 sm:px-4">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div className="text-xs text-[var(--muted)]">
          {title}:{" "}
          {activeCount > 0 && (
            <span className="text-[var(--accent)]">{activeCount} в работе</span>
          )}
          {activeCount > 0 && (doneCount > 0 || errorCount > 0) && " · "}
          {doneCount > 0 && <span className="text-[var(--success)]">{doneCount} OK</span>}
          {doneCount > 0 && errorCount > 0 && " · "}
          {errorCount > 0 && <span className="text-[var(--danger)]">{errorCount} ошибок</span>}
        </div>
        {(doneCount > 0 || errorCount > 0) && activeCount === 0 && (
          <button
            type="button"
            onClick={onDismissFinished}
            className="text-xs text-[var(--muted)] hover:text-[var(--text)]"
          >
            Очистить
          </button>
        )}
      </div>
      <div className="flex max-h-28 flex-col gap-1 overflow-auto">
        {jobs.map((job) => (
          <div
            key={job.nodeId}
            className="flex items-center gap-2 rounded-md border border-[var(--border)] bg-[var(--bg-row)] px-2 py-1.5 text-xs"
          >
            <PhaseDot phase={job.phase} />
            <button
              type="button"
              onClick={() => onOpen(job.nodeId)}
              className="min-w-0 flex-1 truncate text-left text-[var(--text)] hover:text-[var(--accent)]"
              title="Открыть лог"
            >
              {job.nodeName}
              <span className="ml-2 font-mono text-[var(--muted)]">{job.host}</span>
            </button>
            <span className="shrink-0 text-[var(--muted)]">
              {job.phase === "queued"
                ? "очередь"
                : job.phase === "running"
                  ? "SSH…"
                  : job.phase === "done"
                    ? "OK"
                    : "ошибка"}
            </span>
            {(job.phase === "queued" || job.phase === "running") && (
              <button
                type="button"
                onClick={() => onCancel(job.nodeId)}
                className="shrink-0 text-[var(--danger)] hover:underline"
              >
                стоп
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function PhaseDot({ phase }: { phase: TrayJob["phase"] }) {
  const cls =
    phase === "running"
      ? "bg-[var(--accent)] animate-pulse"
      : phase === "queued"
        ? "bg-[var(--muted)]"
        : phase === "done"
          ? "bg-[var(--success)]"
          : "bg-[var(--danger)]";
  return <span className={`h-2 w-2 shrink-0 rounded-full ${cls}`} />;
}
