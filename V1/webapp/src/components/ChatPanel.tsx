import { useRef, useState } from "react";
import { api, type BacktestResult, type ChatMessage } from "../api";

const SUGGESTIONS = [
  "Why did the worst-losing trade fail?",
  "What's different about winning vs. losing trades on this run?",
  "Is one symbol responsible for most of the losses?",
];

function Bubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className="max-w-[85%] rounded-lg px-3 py-2 text-sm whitespace-pre-wrap"
        style={{
          background: isUser ? "var(--series-1)" : "var(--surface-1)",
          color: isUser ? "#ffffff" : "var(--text-primary)",
          border: isUser ? "none" : "1px solid var(--border)",
        }}
      >
        {message.content}
      </div>
    </div>
  );
}

/** Chat scoped to exactly the result currently on screen -- see
 * engine/chat_assistant.py. The frontend is the source of truth for
 * conversation history; every message resends the full `result` payload
 * (no backend session state, no re-running the backtest). */
export function ChatPanel({ result }: { result: BacktestResult }) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const send = async (text: string) => {
    const question = text.trim();
    if (!question || sending) return;
    const next: ChatMessage[] = [...messages, { role: "user", content: question }];
    setMessages(next);
    setInput("");
    setSending(true);
    setError(null);
    try {
      const res = await api.chat(result, next);
      setMessages([...next, { role: "assistant", content: res.reply }]);
    } catch (e) {
      setError(String(e));
    } finally {
      setSending(false);
      requestAnimationFrame(() => bottomRef.current?.scrollIntoView({ behavior: "smooth" }));
    }
  };

  return (
    <div className="flex h-[32rem] flex-col gap-3">
      <div
        className="flex-1 space-y-3 overflow-y-auto rounded-lg border p-4"
        style={{ borderColor: "var(--border)", background: "var(--page)" }}
      >
        {messages.length === 0 && (
          <div className="space-y-3">
            <p className="text-sm" style={{ color: "var(--text-muted)" }}>
              Ask about this run's trades -- the assistant only sees this result (trades, MFE/MAE,
              exit efficiency, per-symbol stats), grounded in the same numbers shown in the other
              tabs. It won't tell you what to trade next.
            </p>
            <div className="flex flex-wrap gap-2">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => send(s)}
                  className="rounded-full border px-3 py-1.5 text-xs"
                  style={{ borderColor: "var(--border)", color: "var(--text-secondary)" }}
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((m, i) => (
          <Bubble key={i} message={m} />
        ))}
        {sending && (
          <div className="text-xs" style={{ color: "var(--text-muted)" }}>
            Thinking…
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {error && (
        <div
          className="rounded-lg border px-3 py-2 text-xs"
          style={{ borderColor: "var(--status-critical)", color: "var(--status-critical)" }}
        >
          {error}
        </div>
      )}

      <form
        onSubmit={(e) => {
          e.preventDefault();
          send(input);
        }}
        className="flex gap-2"
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask about this run's trades…"
          disabled={sending}
          className="flex-1 rounded-md border px-3 py-2 text-sm disabled:opacity-50"
          style={{ borderColor: "var(--border)", background: "var(--surface-1)", color: "var(--text-primary)" }}
        />
        <button
          type="submit"
          disabled={sending || !input.trim()}
          className="rounded-md px-4 py-2 text-sm font-medium text-white transition-opacity disabled:opacity-50"
          style={{ background: "var(--series-1)" }}
        >
          {sending ? "…" : "Send"}
        </button>
      </form>
    </div>
  );
}
