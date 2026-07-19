"use client";

import { useEffect, useState } from "react";
import { api, API, type Txn } from "@/lib/api";

export default function TransactionsPage() {
  const [txns, setTxns] = useState<Txn[] | null>(null);
  const [extracting, setExtracting] = useState(false);

  const refresh = () => api<Txn[]>("/transactions").then(setTxns).catch(() => setTxns([]));

  useEffect(() => {
    refresh();
  }, []);

  const extract = async () => {
    setExtracting(true);
    try {
      await api("/transactions/extract", { method: "POST" });
      setTimeout(() => {
        refresh();
        setExtracting(false);
      }, 8000);
    } catch {
      setExtracting(false);
    }
  };

  return (
    <div>
      <div className="flex items-center gap-3 mb-4">
        <h1 className="text-xl font-semibold">Transactions</h1>
        <span className="text-xs text-zinc-500">extracted from your inbox by Gemini</span>
        <div className="ml-auto flex gap-2">
          <button
            onClick={extract}
            disabled={extracting}
            className="text-sm bg-zinc-800 hover:bg-zinc-700 rounded-lg px-3 py-1.5 disabled:opacity-50"
          >
            {extracting ? "Extracting…" : "Extract new"}
          </button>
          <a
            href={`${API}/transactions/export.csv`}
            className="text-sm bg-indigo-600 hover:bg-indigo-500 rounded-lg px-3 py-1.5"
          >
            Export CSV
          </a>
        </div>
      </div>

      {txns === null ? (
        <p className="text-zinc-500">Loading…</p>
      ) : txns.length === 0 ? (
        <p className="text-zinc-500">
          No transactions yet — hit “Extract new” after your first sync completes.
        </p>
      ) : (
        <div className="overflow-x-auto border border-zinc-800 rounded-xl">
          <table className="w-full text-sm">
            <thead className="bg-zinc-900 text-zinc-400 text-left">
              <tr>
                <th className="px-3 py-2 font-medium">Date</th>
                <th className="px-3 py-2 font-medium">Amount</th>
                <th className="px-3 py-2 font-medium">Merchant</th>
                <th className="px-3 py-2 font-medium">Type</th>
                <th className="px-3 py-2 font-medium">Reference</th>
                <th className="px-3 py-2 font-medium">Source email</th>
                <th className="px-3 py-2 font-medium">Conf.</th>
              </tr>
            </thead>
            <tbody>
              {txns.map((t) => (
                <tr key={t.id} className="border-t border-zinc-800/70 hover:bg-zinc-900/50">
                  <td className="px-3 py-2 whitespace-nowrap text-zinc-400">
                    {t.date?.slice(0, 10) ?? "—"}
                  </td>
                  <td className="px-3 py-2 whitespace-nowrap font-medium">
                    {t.amount != null ? `${t.currency ?? ""} ${t.amount.toLocaleString()}` : "—"}
                  </td>
                  <td className="px-3 py-2 max-w-48 truncate">{t.merchant ?? "—"}</td>
                  <td className="px-3 py-2">
                    <span
                      className={
                        t.type === "credit" || t.type === "refund"
                          ? "text-emerald-400"
                          : t.type === "debit"
                            ? "text-rose-400"
                            : "text-zinc-400"
                      }
                    >
                      {t.type ?? "—"}
                    </span>
                  </td>
                  <td className="px-3 py-2 max-w-40 truncate text-zinc-400">
                    {t.reference_no ?? "—"}
                  </td>
                  <td className="px-3 py-2 max-w-64 truncate text-zinc-500">
                    {t.email_subject ?? "—"}
                  </td>
                  <td className="px-3 py-2 text-zinc-500">
                    {t.confidence != null ? `${Math.round(t.confidence * 100)}%` : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
