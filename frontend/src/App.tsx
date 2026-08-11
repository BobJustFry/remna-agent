import { useEffect, useState } from "react";
import { api } from "./api/client";
import { LoginPage } from "./pages/LoginPage";
import { NodesPage } from "./pages/NodesPage";

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
    <NodesPage
      username={username}
      onLogout={() => {
        void api.logout().finally(() => {
          setUsername("");
          setAuth("guest");
        });
      }}
    />
  );
}
