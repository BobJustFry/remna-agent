import { useState, type FormEvent } from "react";
import { api } from "../api/client";

type Props = {
  onSuccess: (username: string) => void;
};

export function LoginPage({ onSuccess }: Props) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const user = await api.login(username, password);
      onSuccess(user.username);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Ошибка входа");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <form
        onSubmit={onSubmit}
        className="w-full max-w-sm rounded-[var(--radius)] border border-[var(--border)] bg-[var(--bg-elevated)] p-6 shadow-2xl"
      >
        <div className="mb-6">
          <div className="text-xl font-semibold tracking-tight">Remna Agent</div>
          <div className="mt-1 text-sm text-[var(--muted)]">Вход в панель управления нодами</div>
        </div>

        <label className="mb-3 grid gap-1.5 text-xs text-[var(--muted)]">
          Имя пользователя
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            className="rounded-lg border border-[var(--border)] bg-[var(--bg)] px-3 py-2 text-sm text-[var(--text)] outline-none focus:border-[var(--accent)]"
            autoComplete="username"
            autoCapitalize="none"
            spellCheck={false}
            maxLength={128}
            required
          />
        </label>

        <label className="mb-4 grid gap-1.5 text-xs text-[var(--muted)]">
          Пароль
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="rounded-lg border border-[var(--border)] bg-[var(--bg)] px-3 py-2 text-sm text-[var(--text)] outline-none focus:border-[var(--accent)]"
            autoComplete="current-password"
            maxLength={256}
            required
          />
        </label>

        {error && (
          <div className="mb-3 rounded-md border border-[rgba(240,113,120,0.35)] bg-[rgba(240,113,120,0.08)] px-3 py-2 text-sm text-[var(--danger)]">
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={busy}
          className="w-full rounded-lg bg-[var(--accent)] px-4 py-2.5 text-sm font-semibold text-[#06221e] transition hover:brightness-110 disabled:opacity-60"
        >
          {busy ? "Вход…" : "Войти"}
        </button>
      </form>
    </div>
  );
}
