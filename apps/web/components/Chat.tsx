"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  API,
  streamChat,
  type Citation,
  type PendingAction,
} from "@/lib/api";

type Msg = {
  role: "user" | "assistant";
  content: string;
  citations?: Citation[];
  tools?: string[];
  action?: PendingAction & { status?: string };
};

export default function Chat() {
  const [sessionId, setSessionId] = useState<number | null>(null);
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api<{ id: number }>("/chat/sessions", { method: "POST" })
      .then((s) => setSessionId(s.id))
      .catch(() => {});
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const send = useCallback(async () => {
    if (!input.trim() || !sessionId || busy) return;
    const content = input.trim();
    setInput("");
    setBusy(true);
    setMessages((m) => [...m, { role: "user", content }, { role: "assistant", content: "" }]);

    const patch = (fn: (last: Msg) => Msg) =>
      setMessages((m) => {
        const copy = [...m];
        copy[copy.length - 1] = fn(copy[copy.length - 1]);
        return copy;
      });

    await streamChat(sessionId, content, {
      onToken: (text) => patch((l) => ({ ...l, content: l.content + text })),
      onTool: (name) => patch((l) => ({ ...l, tools: [...(l.tools ?? []), name] })),
      onCitations: (results) => patch((l) => ({ ...l, citations: results })),
      onActionRequired: (action) => patch((l) => ({ ...l, action })),
      onError: (message) =>
        patch((l) => ({ ...l, content: l.content + `\n\n⚠️ ${message}` })),
      onDone: () => {},
    });
    setBusy(false);
  }, [input, sessionId, busy]);

  const resolveAction = async (actionId: number, approve: boolean) => {
    setBusy(true);
    try {
      const res = await api<{ status: string; result: string }>(
        `/actions/${actionId}/${approve ? "approve" : "reject"}`,
        { method: "POST", body: approve ? undefined : JSON.stringify({ reason: "rejected in UI" }) }
      );
      setMessages((m) =>
        m.map((msg) =>
          msg.action?.action_id === actionId
            ? {
                ...msg,
                action: { ...msg.action, status: res.status },
                content: msg.content + `\n\n${res.result}`,
              }
            : msg
        )
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col h-[calc(100vh-8.5rem)]">
      <div className="flex-1 overflow-y-auto space-y-4 pb-4">
        {messages.length === 0 && (
          <div className="text-center text-zinc-500 mt-24 space-y-2">
            <p className="text-2xl">Ask anything about your inbox</p>
            <p className="text-sm">
              “When did my last order arrive?” · “List transactions this month” · “Forward my
              latest invoice to…”
            </p>
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={m.role === "user" ? "flex justify-end" : "flex justify-start"}>
            <div
              className={
                m.role === "user"
                  ? "bg-indigo-600 rounded-2xl rounded-br-sm px-4 py-2 max-w-[80%]"
                  : "bg-zinc-900 border border-zinc-800 rounded-2xl rounded-bl-sm px-4 py-3 max-w-[85%] w-fit"
              }
            >
              {m.tools && m.tools.length > 0 && (
                <div className="flex flex-wrap gap-1 mb-2">
                  {m.tools.map((t, j) => (
                    <span
                      key={j}
                      className="text-[10px] uppercase tracking-wide bg-zinc-800 text-zinc-400 rounded px-1.5 py-0.5"
                    >
                      ⚙ {t}
                    </span>
                  ))}
                </div>
              )}
              <div className="whitespace-pre-wrap text-sm leading-relaxed">
                {m.content || (m.role === "assistant" && busy ? "…" : "")}
              </div>
              {m.citations && m.citations.length > 0 && (
                <div className="mt-3 border-t border-zinc-800 pt-2 space-y-1">
                  {m.citations.slice(0, 5).map((c) => (
                    <a
                      key={c.email_id}
                      href={`https://mail.google.com/mail/u/0/#all/${c.gmail_id}`}
                      target="_blank"
                      rel="noreferrer"
                      className="block text-xs text-zinc-400 hover:text-indigo-400 truncate"
                    >
                      [{c.email_id}] {c.subject ?? "(no subject)"} —{" "}
                      {c.sender?.replace(/<.*>/, "").trim()}
                    </a>
                  ))}
                </div>
              )}
              {m.action && (
                <div className="mt-3 border border-amber-700/50 bg-amber-950/30 rounded-xl p-3">
                  <p className="text-amber-400 text-xs font-semibold uppercase tracking-wide mb-1">
                    ✋ Approval required — {m.action.action_type}
                  </p>
                  <pre className="text-xs text-zinc-300 whitespace-pre-wrap max-h-40 overflow-y-auto">
                    {JSON.stringify(m.action.payload, null, 2)}
                  </pre>
                  {!m.action.status ? (
                    <div className="flex gap-2 mt-2">
                      <button
                        onClick={() => resolveAction(m.action!.action_id, true)}
                        disabled={busy}
                        className="bg-emerald-600 hover:bg-emerald-500 text-sm rounded-lg px-3 py-1 disabled:opacity-50"
                      >
                        Approve & send
                      </button>
                      <button
                        onClick={() => resolveAction(m.action!.action_id, false)}
                        disabled={busy}
                        className="bg-zinc-700 hover:bg-zinc-600 text-sm rounded-lg px-3 py-1 disabled:opacity-50"
                      >
                        Reject
                      </button>
                    </div>
                  ) : (
                    <p className="text-xs mt-2 text-zinc-400">→ {m.action.status}</p>
                  )}
                </div>
              )}
            </div>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
      <div className="flex gap-2 pt-3 border-t border-zinc-800">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
          placeholder="Ask about your emails…"
          className="flex-1 bg-zinc-900 border border-zinc-800 rounded-xl px-4 py-2.5 text-sm outline-none focus:border-indigo-600"
        />
        <button
          onClick={send}
          disabled={busy || !sessionId}
          className="bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 rounded-xl px-5 text-sm font-medium"
        >
          {busy ? "…" : "Send"}
        </button>
      </div>
    </div>
  );
}
