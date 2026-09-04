"use client";
import { useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

function authHeaders(): Record<string, string> {
  if (typeof window === "undefined") return {};
  const hid = localStorage.getItem("sc_household_id") || "";
  const mid = localStorage.getItem("sc_member_id") || "";
  return hid ? { "x-household-id": hid, "x-member-id": mid } : {};
}

interface Cal { id: string; name: string; color?: string; primary: boolean; can_write: boolean; access_role: string; }
type CalMode = "read" | "readwrite" | "restricted";

export default function ConnectedServicesPage() {
  const [calStatus, setCalStatus] = useState<"loading"|"connected"|"disconnected"|"not_connected">("loading");
  const [calAccount, setCalAccount] = useState("");
  const [calendars, setCalendars] = useState<Cal[]>([]);
  const [calModes, setCalModes] = useState<Record<string, CalMode>>({});
  const [showPicker, setShowPicker] = useState(false);
  const [savingCal, setSavingCal] = useState(false);
  const [calMsg, setCalMsg] = useState("");
  const [emailAddr, setEmailAddr] = useState("");
  const [emailCount, setEmailCount] = useState(0);
  const [emailLog, setEmailLog] = useState<any[]>([]);
  const [guide, setGuide] = useState<any>(null);
  const [showGuide, setShowGuide] = useState<"gmail"|"outlook"|null>(null);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [verificationCode, setVerificationCode] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${API}/api/v1/connectors/available`, { headers: authHeaders() })
      .then(r => r.ok ? r.json() : []).then(conns => {
        const cal = (conns as any[]).find((c: any) => c.id === "google_calendar");
        if (cal?.connected) { setCalStatus("connected"); setCalAccount(cal.external_account_ref || ""); loadCalendars(); }
        else if (cal?.status === "disconnected_by_user") setCalStatus("disconnected");
        else setCalStatus("not_connected");
      }).catch(() => setCalStatus("not_connected"));

    fetch(`${API}/api/v1/email/addresses`, { headers: authHeaders() }).then(r => r.ok ? r.json() : [])
      .then(a => { if (Array.isArray(a) && a.length) { setEmailAddr(a[0].address); setEmailCount(a[0].emails_received||0); }}).catch(()=>{});
    fetch(`${API}/api/v1/email/setup-guide`, { headers: authHeaders() }).then(r => r.ok ? r.json() : null).then(setGuide).catch(()=>{});
    fetch(`${API}/api/v1/email/history?limit=5`, { headers: authHeaders() }).then(r => r.ok ? r.json() : []).then(l => setEmailLog(Array.isArray(l)?l:[])).catch(()=>{});
    // Check for Gmail verification code
    fetch(`${API}/api/v1/email/confirmation-code`, { headers: authHeaders() }).then(r => r.ok ? r.json() : null)
      .then(d => { if (d?.has_code) setVerificationCode(d.code); }).catch(()=>{});
  }, []);

  const loadCalendars = async () => {
    try {
      const r = await fetch(`${API}/api/v1/connectors/configure/google/calendars`, { headers: authHeaders() });
      if (!r.ok) return;
      const data = await r.json();
      const cals = data.calendars || [];
      setCalendars(cals);
      // Initialize modes from saved config or defaults
      const modes: Record<string, CalMode> = {};
      const savedReads = new Set(data.current_config?.sync_calendar_ids || []);
      const savedWrite = data.current_config?.write_calendar_id || "";
      for (const c of cals) {
        if (savedWrite && c.id === savedWrite) modes[c.id] = "readwrite";
        else if (savedReads.has(c.id)) modes[c.id] = "read";
        else if (!data.current_config && c.primary) modes[c.id] = "readwrite"; // default primary to read+write
        else modes[c.id] = "restricted";
      }
      setCalModes(modes);
    } catch {}
  };

  const setMode = (calId: string, mode: CalMode) => {
    setCalModes(prev => {
      const next = { ...prev };
      // If setting readwrite, clear any other readwrite (only one allowed)
      if (mode === "readwrite") {
        for (const k of Object.keys(next)) { if (next[k] === "readwrite") next[k] = "read"; }
      }
      next[calId] = mode;
      return next;
    });
  };

  const hasWrite = Object.values(calModes).includes("readwrite");
  const readCount = Object.values(calModes).filter(m => m === "read" || m === "readwrite").length;

  const saveCalendarConfig = async () => {
    if (!hasWrite) return;
    setSavingCal(true);
    const syncIds = Object.entries(calModes).filter(([,m]) => m !== "restricted").map(([id]) => id);
    const syncNames = syncIds.map(id => calendars.find(c => c.id === id)?.name || id);
    const writeId = Object.entries(calModes).find(([,m]) => m === "readwrite")?.[0] || "";
    const writeName = calendars.find(c => c.id === writeId)?.name || writeId;
    try {
      const r = await fetch(`${API}/api/v1/connectors/configure/calendars/select`, {
        method: "POST", headers: { ...authHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify({ connector_type_id: "google_calendar", sync_calendar_ids: syncIds, sync_calendar_names: syncNames, write_calendar_id: writeId, write_calendar_name: writeName }),
      });
      if (r.ok) { setCalMsg("Saved!"); setTimeout(() => { setCalMsg(""); setShowPicker(false); }, 1500); }
    } catch {} finally { setSavingCal(false); }
  };

  const connectCalendar = async () => {
    setBusy(true);
    try {
      const r = await fetch(`${API}/api/v1/connectors/google_calendar/connect`, { method: "POST", headers: { ...authHeaders(), "Content-Type": "application/json" } });
      if (r.ok) { const { auth_url } = await r.json(); window.location.href = auth_url; }
      else { const e = await r.json(); alert(e.detail || "Failed"); }
    } catch (e: any) { alert(e.message); } finally { setBusy(false); }
  };

  const disconnectCalendar = async () => {
    if (!confirm("Disconnecting will pause conflict detection and stop syncing. Events already in your calendar stay.")) return;
    setBusy(true);
    await fetch(`${API}/api/v1/connectors/google_calendar/disconnect`, { method: "DELETE", headers: authHeaders() });
    setCalStatus("disconnected"); setCalAccount(""); setCalendars([]); setShowPicker(false); setBusy(false);
  };

  const modeLabel = (m: CalMode) => ({ read: "Read Only", readwrite: "Read & Write", restricted: "Restricted" }[m]);
  const modeColor = (m: CalMode) => ({ read: "text-blue-700 bg-blue-50 border-blue-200", readwrite: "text-green-700 bg-green-50 border-green-200", restricted: "text-gray-400 bg-gray-50 border-gray-200" }[m]);

  return (
    <div className="max-w-2xl mx-auto p-6 pt-10 space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-gray-900">Connected Services</h1>
        <p className="text-sm text-gray-500 mt-1">Connect your calendar and set up email forwarding.</p>
      </div>

      {/* ── Google Calendar ─────────────────────────────────────── */}
      <div className="border border-gray-200 rounded-2xl bg-white overflow-hidden">
        <div className="px-5 py-4 flex items-start justify-between">
          <div className="flex items-start gap-3">
            <span className="text-2xl mt-0.5">📅</span>
            <div>
              <p className="font-semibold text-gray-900">Google Calendar</p>
              {calStatus === "connected" && <p className="text-xs text-green-600 mt-1">✓ Connected — {calAccount || "Google Account"}</p>}
              {calStatus === "loading" && <p className="text-xs text-gray-400 mt-1">Checking...</p>}
              {calStatus === "disconnected" && <p className="text-xs text-gray-400 mt-1">Disconnected. Reconnect anytime.</p>}
              {calStatus === "not_connected" && <p className="text-xs text-gray-500 mt-1">Sync events, detect conflicts, and get smart reminders.</p>}
            </div>
          </div>
          <div className="flex flex-col gap-2 shrink-0">
            {calStatus === "connected" ? (
              <>
                <button onClick={() => { loadCalendars(); setShowPicker(!showPicker); }}
                  className="text-sm px-4 py-2 rounded-xl border border-indigo-300 text-indigo-700 bg-indigo-50 hover:bg-indigo-100 font-semibold">
                  {showPicker ? "Close" : "Configure Calendars"}
                </button>
                <button onClick={disconnectCalendar} disabled={busy}
                  className="text-sm px-4 py-2 rounded-xl border border-red-200 text-red-600 hover:bg-red-50 font-medium disabled:opacity-50">
                  Disconnect
                </button>
              </>
            ) : (
              <button onClick={connectCalendar} disabled={busy}
                className="text-sm px-4 py-2 rounded-xl bg-indigo-600 text-white hover:bg-indigo-700 font-semibold disabled:opacity-50">
                {busy ? "Connecting..." : "Connect"}
              </button>
            )}
          </div>
        </div>

        {/* Calendar Picker */}
        {showPicker && calStatus === "connected" && (
          <div className="px-5 py-4 border-t border-gray-100 bg-gray-50 space-y-3">
            <div className="flex items-center justify-between">
              <p className="text-sm font-semibold text-gray-700">Configure access per calendar:</p>
              <div className="flex gap-2 text-[10px]">
                <span className="px-2 py-0.5 rounded-full border text-green-700 bg-green-50 border-green-200">Read & Write</span>
                <span className="px-2 py-0.5 rounded-full border text-blue-700 bg-blue-50 border-blue-200">Read Only</span>
                <span className="px-2 py-0.5 rounded-full border text-gray-400 bg-gray-50 border-gray-200">Restricted</span>
              </div>
            </div>

            {calendars.length === 0 ? (
              <p className="text-sm text-gray-400 py-4 text-center">Loading calendars from Google...</p>
            ) : (
              <div className="space-y-2">
                {calendars.map(cal => {
                  const mode = calModes[cal.id] || "restricted";
                  return (
                    <div key={cal.id} className={`border rounded-xl p-3 transition-all ${mode === "restricted" ? "border-gray-200 bg-white opacity-60" : "border-gray-300 bg-white"}`}>
                      <div className="flex items-center gap-3">
                        <div className="w-3 h-3 rounded-full shrink-0" style={{ backgroundColor: cal.color || "#4285F4" }} />
                        <div className="flex-1 min-w-0">
                          <p className="text-sm font-medium text-gray-900 truncate">
                            {cal.name}
                            {cal.primary && <span className="ml-2 text-[10px] text-indigo-600 bg-indigo-50 px-1.5 py-0.5 rounded">Primary</span>}
                          </p>
                          <p className="text-[11px] text-gray-400">
                            {cal.access_role === "owner" ? "Owner" : cal.access_role === "writer" ? "Editor" : "Viewer"}
                            {!cal.can_write && " · shared calendar"}
                          </p>
                        </div>
                        <div className="flex gap-1 shrink-0">
                          {cal.can_write && (
                            <button onClick={() => setMode(cal.id, "readwrite")}
                              className={`text-[11px] px-2.5 py-1 rounded-lg border font-medium transition-all ${mode === "readwrite" ? "text-green-700 bg-green-50 border-green-300 ring-1 ring-green-300" : "text-gray-400 border-gray-200 hover:border-green-200 hover:text-green-600"}`}>
                              Read & Write
                            </button>
                          )}
                          <button onClick={() => setMode(cal.id, "read")}
                            className={`text-[11px] px-2.5 py-1 rounded-lg border font-medium transition-all ${mode === "read" ? "text-blue-700 bg-blue-50 border-blue-300 ring-1 ring-blue-300" : "text-gray-400 border-gray-200 hover:border-blue-200 hover:text-blue-600"}`}>
                            Read Only
                          </button>
                          <button onClick={() => setMode(cal.id, "restricted")}
                            className={`text-[11px] px-2.5 py-1 rounded-lg border font-medium transition-all ${mode === "restricted" ? "text-gray-500 bg-gray-100 border-gray-300 ring-1 ring-gray-300" : "text-gray-400 border-gray-200 hover:border-gray-300"}`}>
                            Restricted
                          </button>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}

            {/* Summary */}
            {readCount > 0 && (
              <div className={`rounded-xl p-3 text-xs border ${hasWrite ? "bg-green-50 border-green-200 text-green-800" : "bg-amber-50 border-amber-200 text-amber-800"}`}>
                {hasWrite ? (
                  <>
                    <strong>New events</strong> will be created in "{calendars.find(c => calModes[c.id] === "readwrite")?.name}".
                    {readCount > 1 && <> Other {readCount - 1} calendar{readCount > 2 ? "s" : ""}: read-only (conflict detection + briefing).</>}
                  </>
                ) : (
                  <>Select one calendar as <strong>Read & Write</strong> to enable event creation.</>
                )}
              </div>
            )}
            {calMsg && <p className="text-sm text-green-600 font-semibold">{calMsg}</p>}
            <div className="flex gap-3 pt-1">
              <button onClick={() => setShowPicker(false)} className="flex-1 py-2.5 border border-gray-300 rounded-xl text-sm text-gray-600 hover:bg-gray-100">Cancel</button>
              <button onClick={saveCalendarConfig} disabled={savingCal || !hasWrite}
                className="flex-1 py-2.5 bg-indigo-600 text-white rounded-xl text-sm font-semibold hover:bg-indigo-700 disabled:opacity-40">
                {savingCal ? "Saving..." : "Save Configuration"}
              </button>
            </div>
          </div>
        )}

        {calStatus === "connected" && !showPicker && (
          <div className="px-5 py-3 bg-gray-50 border-t border-gray-100 text-xs text-gray-600">
            Events sync every hour. Click <strong>Configure Calendars</strong> to choose access per calendar.
          </div>
        )}
      </div>

      {/* ── Email Forwarding ──────────────────────────────────────── */}
      <div className="border border-gray-200 rounded-2xl bg-white overflow-hidden">
        <div className="px-5 py-4 bg-gradient-to-r from-indigo-50 to-purple-50 border-b border-gray-100 flex items-center gap-3">
          <span className="text-2xl">✉️</span>
          <div>
            <h2 className="font-semibold text-gray-900">Email Forwarding</h2>
            <p className="text-xs text-gray-500">Forward specific emails to KinValet — no inbox access needed</p>
          </div>
        </div>
        <div className="p-5 space-y-4">
          {emailAddr ? (
            <>
              <div>
                {/* Gmail verification code banner */}
                {verificationCode && (
                  <div className="bg-amber-50 border border-amber-200 rounded-xl p-4 mb-3">
                    <p className="text-xs font-semibold text-amber-700 uppercase tracking-wide mb-1">Gmail Verification Code</p>
                    <p className="text-2xl font-bold text-amber-900 tracking-widest">{verificationCode}</p>
                    <p className="text-xs text-amber-600 mt-1">Enter this code in Gmail to confirm forwarding. You can also ask the Assistant: "what's my verification code?"</p>
                  </div>
                )}
                <label className="text-xs font-semibold text-gray-500 uppercase tracking-wide">Your KinValet email address</label>
                <div className="mt-1.5 flex items-center gap-2">
                  <code className="flex-1 bg-gray-50 border border-gray-200 rounded-xl px-4 py-3 text-sm font-mono text-gray-900 select-all">{emailAddr}</code>
                  <button onClick={() => { navigator.clipboard.writeText(emailAddr); setCopied(true); setTimeout(() => setCopied(false), 2000); }}
                    className={`shrink-0 px-4 py-3 rounded-xl text-sm font-semibold transition-all ${copied ? "bg-green-100 text-green-700" : "bg-indigo-600 text-white hover:bg-indigo-700"}`}>
                    {copied ? "Copied!" : "Copy"}
                  </button>
                </div>
                {emailCount > 0 && <p className="text-xs text-green-600 mt-2">✓ {emailCount} email{emailCount !== 1 ? "s" : ""} received</p>}
              </div>
              <div className="bg-blue-50 border border-blue-100 rounded-xl p-4 text-xs text-blue-800 space-y-1">
                <p><strong>What we read:</strong> only emails you forward to this address</p>
                <p><strong>What we write:</strong> nothing — we never send, reply to, or modify your email</p>
              </div>
              <div>
                <p className="text-sm font-semibold text-gray-700 mb-2">Set up forwarding:</p>
                <div className="flex gap-2">
                  <button onClick={() => setShowGuide(showGuide === "gmail" ? null : "gmail")}
                    className={`flex-1 py-2.5 rounded-xl text-sm font-medium border transition-colors ${showGuide === "gmail" ? "bg-indigo-50 border-indigo-300 text-indigo-700" : "border-gray-200 text-gray-700 hover:bg-gray-50"}`}>
                    Gmail setup →</button>
                  <button onClick={() => setShowGuide(showGuide === "outlook" ? null : "outlook")}
                    className={`flex-1 py-2.5 rounded-xl text-sm font-medium border transition-colors ${showGuide === "outlook" ? "bg-indigo-50 border-indigo-300 text-indigo-700" : "border-gray-200 text-gray-700 hover:bg-gray-50"}`}>
                    Outlook setup →</button>
                </div>
              </div>
              {showGuide && guide?.instructions?.[showGuide] && (
                <div className="bg-gray-50 border border-gray-200 rounded-xl p-4 space-y-2">
                  <h3 className="text-sm font-semibold text-gray-900">{guide.instructions[showGuide].title}</h3>
                  <ol className="text-sm text-gray-700 space-y-1.5 list-none">
                    {guide.instructions[showGuide].steps.filter((s: string) => s.trim()).map((step: string, i: number) => (
                      <li key={i} className={step.startsWith("   ") ? "ml-4 text-gray-500 text-xs" : ""}>{step}</li>
                    ))}
                  </ol>
                </div>
              )}
              {emailLog.length > 0 && (
                <div className="pt-2 border-t border-gray-100">
                  <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">Recent emails received</p>
                  {emailLog.map((e: any) => (
                    <div key={e.id} className="flex items-start gap-2 text-xs bg-gray-50 rounded-lg px-3 py-2 border border-gray-100 mb-1.5">
                      <span className={`shrink-0 mt-0.5 ${e.status === "extracted" ? "text-green-500" : "text-gray-400"}`}>{e.status === "extracted" ? "✓" : "⟳"}</span>
                      <div className="min-w-0 flex-1">
                        <p className="font-medium text-gray-800 truncate">{e.subject || "(no subject)"}</p>
                        <p className="text-gray-400 truncate">from {e.from} · {new Date(e.received_at).toLocaleString()}</p>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </>
          ) : (
            <div className="text-center py-4">
              <p className="text-sm text-gray-600 mb-3">Get your unique email address to start forwarding.</p>
              <button onClick={() => { fetch(`${API}/api/v1/email/addresses`, { method: "POST", headers: { ...authHeaders(), "Content-Type": "application/json" }, body: "{}" }).then(r => r.ok ? r.json() : null).then(d => { if (d) setEmailAddr(d.address); }); }}
                className="bg-indigo-600 text-white text-sm font-semibold px-6 py-2.5 rounded-xl hover:bg-indigo-700">
                Generate my address
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
