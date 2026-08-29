"use client";
/**
 * KinValet Assistant — dashboard input channel.
 * Same AI brain as WhatsApp, but in the browser.
 *
 * Type natural language → AI extracts tasks, FYIs, delegations.
 * Quick actions: delegate items, push FYIs, view responses.
 */
import { useEffect, useRef, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

function authHeaders(): Record<string, string> {
  if (typeof window === "undefined") return {};
  const hid = localStorage.getItem("sc_household_id") || "";
  const mid = localStorage.getItem("sc_member_id") || "";
  return hid ? { "x-household-id": hid, "x-member-id": mid } : {};
}

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  status?: "sending" | "processing" | "done" | "error";
  items?: Array<{ title: string; category: string; start_at?: string; cost_cents?: number }>;
  timestamp: Date;
}

export default function AssistantPage() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    // Load chat history
    fetch(`${API}/api/v1/assistant/history?limit=20`, { headers: authHeaders() })
      .then(r => r.ok ? r.json() : [])
      .then(history => {
        const msgs: ChatMessage[] = [];
        for (const h of (history as any[]).reverse()) {
          msgs.push({
            id: h.id,
            role: "user",
            text: h.text,
            status: "done",
            timestamp: new Date(h.sent_at),
          });
          if (h.has_result) {
            msgs.push({
              id: h.id + "-reply",
              role: "assistant",
              text: h.status === "extracted" ? "Processed" : "Processing...",
              status: h.status === "extracted" ? "done" : "processing",
              timestamp: new Date(h.sent_at),
            });
          }
        }
        setMessages(msgs);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  const send = async () => {
    const text = input.trim();
    if (!text || sending) return;

    const userMsg: ChatMessage = {
      id: Date.now().toString(),
      role: "user",
      text,
      status: "sending",
      timestamp: new Date(),
    };
    setMessages(prev => [...prev, userMsg]);
    setInput("");
    setSending(true);
    inputRef.current?.focus();

    try {
      const res = await fetch(`${API}/api/v1/assistant/message`, {
        method: "POST",
        headers: { ...authHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      const data = await res.json();

      if (!res.ok) {
        setMessages(prev => [...prev, {
          id: Date.now().toString(), role: "assistant",
          text: data.detail || "Something went wrong", status: "error", timestamp: new Date(),
        }]);
        setSending(false);
        return;
      }

      // Show processing
      const assistantMsgId = Date.now().toString();
      setMessages(prev => [
        ...prev.map(m => m.id === userMsg.id ? { ...m, status: "done" as const } : m),
        { id: assistantMsgId, role: "assistant", text: "Thinking...", status: "processing", timestamp: new Date() },
      ]);

      // Poll for result
      const messageId = data.message_id;
      let attempts = 0;
      const poll = setInterval(async () => {
        attempts++;
        if (attempts > 30) { clearInterval(poll); setSending(false); return; }
        try {
          const r = await fetch(`${API}/api/v1/assistant/message/${messageId}/result`, { headers: authHeaders() });
          const result = await r.json();
          if (result.status === "extracted" || result.status === "error") {
            clearInterval(poll);
            setMessages(prev => prev.map(m =>
              m.id === assistantMsgId ? { ...m, text: result.reply, status: "done", items: result.items_created } : m
            ));
            setSending(false);
          }
        } catch { /* keep polling */ }
      }, 2000);

    } catch (e: any) {
      setMessages(prev => [...prev, {
        id: Date.now().toString(), role: "assistant",
        text: "Failed to send. Check your connection.", status: "error", timestamp: new Date(),
      }]);
      setSending(false);
    }
  };

  const quickActions = [
    { label: "Add a task", prompt: "" , placeholder: "Leo has soccer practice Tuesday at 6pm at Field B"},
    { label: "FYI / Note", prompt: "FYI: ", placeholder: "School is closed next Friday for teacher training" },
    { label: "What's tomorrow?", prompt: "What's on my calendar tomorrow?", placeholder: "" },
    { label: "Show my tasks", prompt: "What's on my plate?", placeholder: "" },
  ];

  return (
    <div className="max-w-2xl mx-auto flex flex-col h-[calc(100vh-3rem)]">
      {/* Header */}
      <div className="px-5 py-4 border-b border-gray-200 bg-white">
        <h1 className="text-lg font-semibold text-gray-900">KinValet Assistant</h1>
        <p className="text-xs text-gray-500">Type anything — same AI as WhatsApp. Tasks, FYIs, questions.</p>
      </div>

      {/* Messages */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto p-4 space-y-3 bg-gray-50">
        {messages.length === 0 && (
          <div className="text-center py-12">
            <div className="text-4xl mb-3">✦</div>
            <p className="text-gray-500 text-sm">Send your first message to get started.</p>
            <p className="text-gray-400 text-xs mt-1">Try: "Leo has soccer practice Tuesday at 6pm"</p>
          </div>
        )}

        {messages.map(msg => (
          <div key={msg.id} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
            <div className={`max-w-[80%] rounded-2xl px-4 py-2.5 ${
              msg.role === "user"
                ? "bg-indigo-600 text-white rounded-br-md"
                : "bg-white border border-gray-200 text-gray-800 rounded-bl-md shadow-sm"
            }`}>
              <p className="text-sm whitespace-pre-wrap">{msg.text}</p>
              {msg.items && msg.items.length > 0 && (
                <div className="mt-2 space-y-1">
                  {msg.items.map((item, i) => (
                    <div key={i} className="bg-gray-50 rounded-lg px-3 py-1.5 text-xs text-gray-700 border border-gray-100">
                      <span className="font-medium">{item.title}</span>
                      {item.start_at && <span className="text-gray-400 ml-1">· {new Date(item.start_at).toLocaleString()}</span>}
                      {item.cost_cents && <span className="text-gray-400 ml-1">· ${(item.cost_cents/100).toFixed(2)}</span>}
                    </div>
                  ))}
                </div>
              )}
              {msg.status === "processing" && (
                <div className="flex items-center gap-1.5 mt-1">
                  <div className="w-1.5 h-1.5 bg-indigo-400 rounded-full animate-pulse" />
                  <div className="w-1.5 h-1.5 bg-indigo-400 rounded-full animate-pulse" style={{ animationDelay: "0.2s" }} />
                  <div className="w-1.5 h-1.5 bg-indigo-400 rounded-full animate-pulse" style={{ animationDelay: "0.4s" }} />
                </div>
              )}
              <p className={`text-[10px] mt-1 ${msg.role === "user" ? "text-indigo-200" : "text-gray-400"}`}>
                {msg.timestamp.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}
              </p>
            </div>
          </div>
        ))}
      </div>

      {/* Quick actions */}
      <div className="px-4 py-2 bg-white border-t border-gray-100 flex gap-2 overflow-x-auto">
        {quickActions.map(qa => (
          <button
            key={qa.label}
            onClick={() => {
              if (qa.prompt && !qa.placeholder) {
                setInput(qa.prompt);
                setTimeout(() => send(), 100);
              } else {
                setInput(qa.prompt);
                inputRef.current?.focus();
              }
            }}
            className="shrink-0 px-3 py-1.5 bg-gray-100 text-gray-600 text-xs font-medium rounded-full hover:bg-gray-200 transition-colors"
          >
            {qa.label}
          </button>
        ))}
      </div>

      {/* Input */}
      <div className="px-4 py-3 bg-white border-t border-gray-200">
        <form
          onSubmit={(e) => { e.preventDefault(); send(); }}
          className="flex items-center gap-2"
        >
          <input
            ref={inputRef}
            value={input}
            onChange={e => setInput(e.target.value)}
            placeholder="Type a message — tasks, FYIs, questions..."
            className="flex-1 border border-gray-300 rounded-xl px-4 py-3 text-[15px] font-medium text-gray-900 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
            disabled={sending}
            autoFocus
          />
          <button
            type="submit"
            disabled={sending || !input.trim()}
            className="shrink-0 w-11 h-11 bg-indigo-600 text-white rounded-xl flex items-center justify-center hover:bg-indigo-700 disabled:opacity-40 transition-colors"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <line x1="22" y1="2" x2="11" y2="13" /><polygon points="22 2 15 22 11 13 2 9 22 2" />
            </svg>
          </button>
        </form>
      </div>
    </div>
  );
}
