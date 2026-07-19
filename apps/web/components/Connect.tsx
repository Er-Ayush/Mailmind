"use client";

import { useEffect, useState } from "react";
import { api, API, type Me, type SyncStatus } from "@/lib/api";

export default function Connect({ me }: { me: Me }) {
  const [status, setStatus] = useState<SyncStatus | null>(null);
  const [syncing, setSyncing] = useState(false);

  const refresh = () => api<SyncStatus>("/sync/status").then(setStatus).catch(() => {});

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, []);

  const trigger = async () => {
    setSyncing(true);
    try {
      await api("/sync/trigger", { method: "POST" });
    } finally {
      setTimeout(() => setSyncing(false), 2000);
    }
  };

  return (
    <div className="flex items-center gap-3 text-xs text-zinc-400 bg-zinc-900/60 border border-zinc-800 rounded-xl px-4 py-2 mb-4">
      <span className="text-emerald-400">●</span>
      <span>{me.gmail_accounts[0]?.email}</span>
      {status && (
        <span>
          {status.emails_total} emails · {status.emails_embedded} embedded · {status.chunks}{" "}
          chunks
        </span>
      )}
      <button
        onClick={trigger}
        disabled={syncing}
        className="ml-auto bg-zinc-800 hover:bg-zinc-700 rounded-lg px-3 py-1 disabled:opacity-50"
      >
        {syncing ? "Queued…" : "Sync now"}
      </button>
    </div>
  );
}
