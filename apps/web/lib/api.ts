export const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, {
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (res.status === 401) throw new Error("unauthenticated");
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
  return res.json();
}

export type Me = {
  id: number;
  email: string;
  name: string | null;
  gmail_accounts: { id: number; email: string; status: string; last_synced_at: string | null }[];
};

export type SyncStatus = {
  accounts: { email: string; last_synced_at: string | null; history_id: string | null }[];
  emails_total: number;
  emails_embedded: number;
  chunks: number;
};

export type Txn = {
  id: number;
  email_id: number;
  date: string | null;
  amount: number | null;
  currency: string | null;
  merchant: string | null;
  reference_no: string | null;
  type: string | null;
  confidence: number | null;
  email_subject: string | null;
  email_sender: string | null;
};

export type Citation = {
  email_id: number;
  gmail_id: string;
  subject: string | null;
  sender: string | null;
  date: string | null;
  score: number;
};

export type PendingAction = {
  action_id: number;
  action_type: string;
  payload: Record<string, unknown>;
};

// SSE over POST: parse the fetch body stream into typed events
export async function streamChat(
  sessionId: number,
  content: string,
  handlers: {
    onToken: (text: string) => void;
    onTool: (name: string, args: Record<string, unknown>) => void;
    onCitations: (results: Citation[]) => void;
    onActionRequired: (action: PendingAction) => void;
    onError: (message: string) => void;
    onDone: () => void;
  }
) {
  const res = await fetch(`${API}/chat/sessions/${sessionId}/messages`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  if (!res.ok || !res.body) {
    handlers.onError(`request failed (${res.status})`);
    return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buffer.indexOf("\n\n")) !== -1) {
      const raw = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      let event = "message";
      let data = "";
      for (const line of raw.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7).trim();
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      if (!data) continue;
      const parsed = JSON.parse(data);
      if (event === "token") handlers.onToken(parsed.text);
      else if (event === "tool") handlers.onTool(parsed.name, parsed.args);
      else if (event === "citations") handlers.onCitations(parsed.results);
      else if (event === "action_required") handlers.onActionRequired(parsed);
      else if (event === "error") handlers.onError(parsed.message);
      else if (event === "done") handlers.onDone();
    }
  }
}
