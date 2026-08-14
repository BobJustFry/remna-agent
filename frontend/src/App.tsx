import { useEffect, useState } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { api } from "./api/client";
import { AppShell } from "./components/AppShell";
import { DashboardPage } from "./pages/DashboardPage";
import { HostingsPage } from "./pages/HostingsPage";
import { LoginPage } from "./pages/LoginPage";
import { NodesPage } from "./pages/NodesPage";
import { ScriptsPage } from "./pages/ScriptsPage";
import { SettingsPage } from "./pages/SettingsPage";

type AuthState = "loading" | "guest" | "user";

export default function App() {
  const [auth, setAuth] = useState<AuthState>("loading");
  const [username, setUsername] = useState("");

  useEffect(() => {
    api
      .me()
      .then((u) => {
        setUsername(u.username);
        setAuth("user");
      })
      .catch(() => setAuth("guest"));
  }, []);

  if (auth === "loading") {
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-[var(--muted)]">
        Загрузка…
      </div>
    );
  }

  if (auth === "guest") {
    return (
      <LoginPage
        onSuccess={(name) => {
          setUsername(name);
          setAuth("user");
        }}
      />
    );
  }

  return (
    <BrowserRouter>
      <Routes>
        <Route
          element={
            <AppShell
              username={username}
              onLogout={() => {
                void api.logout().finally(() => {
                  setUsername("");
                  setAuth("guest");
                });
              }}
            />
          }
        >
          <Route index element={<DashboardPage />} />
          <Route path="nodes" element={<NodesPage />} />
          <Route path="scripts" element={<ScriptsPage />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="hostings" element={<HostingsPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
