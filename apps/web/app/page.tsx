"use client";

import { useEffect, useState } from "react";
import Chat from "@/components/Chat";
import Connect from "@/components/Connect";
import { api, API, type Me } from "@/lib/api";

export default function Home() {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api<Me>("/auth/me")
      .then(setMe)
      .catch(() => setMe(null))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <p className="text-zinc-500 mt-20 text-center">Loading…</p>;

  if (!me)
    return (
      <div className="text-center mt-28 space-y-6">
        <h1 className="text-4xl font-semibold tracking-tight">
          Chat with your <span className="text-indigo-400">Gmail inbox</span>
        </h1>
        <p className="text-zinc-400 max-w-md mx-auto">
          Search emails in natural language, extract transactions, and let the agent draft &
          forward — every send needs your approval.
        </p>
        <a
          href={`${API}/auth/google/login`}
          className="inline-block bg-white text-zinc-900 font-medium rounded-xl px-6 py-3 hover:bg-zinc-200"
        >
          Connect Google account
        </a>
        <p className="text-xs text-zinc-600">
          Read + send scopes · tokens encrypted at rest · unverified-app warning is expected (own
          OAuth app in testing mode)
        </p>
      </div>
    );

  return (
    <>
      <Connect me={me} />
      <Chat />
    </>
  );
}
