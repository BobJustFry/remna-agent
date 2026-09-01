import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { SharingStatus } from "../types";

export function useSharingStatus(): SharingStatus | null {
  const [data, setData] = useState<SharingStatus | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      api
        .sharingStatus()
        .then((s) => {
          if (!cancelled) setData(s);
        })
        .catch(() => {
          /* keep last */
        });
    };
    load();
    const id = window.setInterval(load, 25000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  return data;
}
