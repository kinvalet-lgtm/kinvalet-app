"use client";
import { useEffect, useState } from "react";
import { getDisplayName, getHouseholdName } from "@/lib/session";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

function authHeaders(): Record<string, string> {
  if (typeof window === "undefined") return {};
  const hid = localStorage.getItem("sc_household_id") || "";
  const mid = localStorage.getItem("sc_member_id") || "";
  return hid ? { "x-household-id": hid, "x-member-id": mid } : {};
}

interface CalEvent {
  id: string; title: string; all_day: boolean; start_at?: string; end_at?: string;
  date?: string; location?: string; calendar_name: string; source: string;
}

export default function BriefingPage() {
  const [todayEvents, setTodayEvents] = useState<CalEvent[]>([]);
  const [tomorrowEvents, setTomorrowEvents] = useState<CalEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const displayName = typeof window !== "undefined" ? getDisplayName() : "";
  const householdName = typeof window !== "undefined" ? getHouseholdName() : "";
  const fwdEmail = typeof window !== "undefined" ? localStorage.getItem("sc_forwarding_email") : null;

  useEffect(() => {
    Promise.all([
      fetch(`${API}/api/v1/connectors/configure/calendar/events/today`, { headers: authHeaders() }).then(r => r.ok ? r.json() : { events: [] }).catch(() => ({ events: [] })),
      fetch(`${API}/api/v1/connectors/configure/calendar/events/tomorrow`, { headers: authHeaders() }).then(r => r.ok ? r.json() : { events: [] }).catch(() => ({ events: [] })),
    ]).then(([today, tomorrow]) => {
      setTodayEvents(today.events || []);
      setTomorrowEvents(tomorrow.events || []);
    }).finally(() => setLoading(false));

    // Poll every 30 seconds
    const iv = setInterval(() => {
      if (document.hidden) return;
      fetch(`${API}/api/v1/connectors/configure/calendar/events/today`, { headers: authHeaders() })
        .then(r => r.ok ? r.json() : { events: [] }).then(d => setTodayEvents(d.events || [])).catch(() => {});
    }, 30000);
    return () => clearInterval(iv);
  }, []);

  const greeting = () => { const h = new Date().getHours(); return h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening"; };
  const fmt = (iso?: string) => iso ? new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : "";
  const dateStr = new Date().toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" });
  const tmrwStr = new Date(Date.now() + 86400000).toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" });

  if (loading) return <div className="flex items-center justify-center min-h-[60vh] text-gray-400">Loading your briefing...</div>;

  return (
    <div className="max-w-2xl mx-auto p-6 pt-8 space-y-6">
      <div>
        <p className="text-sm text-gray-400">{dateStr}</p>
        <h1 className="text-2xl font-semibold text-gray-900 mt-0.5">{greeting()}{displayName ? `, ${displayName}` : ""}</h1>
        {householdName && <p className="text-sm text-gray-500">{householdName}</p>}
      </div>

      {/* Today's schedule */}
      {todayEvents.length > 0 ? (
        <Section title="Today's schedule">
          {todayEvents.map(evt => (
            <EventRow key={evt.id} event={evt} />
          ))}
        </Section>
      ) : (
        <div className="bg-green-50 border border-green-200 rounded-xl p-5 flex items-center gap-3">
          <span className="text-2xl">✅</span>
          <div>
            <p className="font-semibold text-green-800">All clear today</p>
            <p className="text-sm text-green-700">No events on your calendar.</p>
          </div>
        </div>
      )}

      {/* Tomorrow's preview */}
      {tomorrowEvents.length > 0 && (
        <Section title={`Tomorrow — ${tmrwStr}`} subtle>
          {tomorrowEvents.map(evt => (
            <EventRow key={evt.id} event={evt} />
          ))}
        </Section>
      )}

      {/* Quick links */}
      <div className="grid grid-cols-2 gap-3">
        <QuickLink href="/assistant" icon="✦" label="Send a message" />
        <QuickLink href="/tasks" icon="✅" label="View tasks" />
        <QuickLink href="/calendar" icon="📅" label="Week view" />
        <QuickLink href="/settings/connected-services" icon="⚙️" label="Connected services" />
      </div>

      {/* Forwarding email */}
      {fwdEmail && (
        <div className="bg-white border border-gray-200 rounded-xl p-4">
          <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Your forwarding email</p>
          <div className="flex items-center gap-2">
            <code className="flex-1 bg-gray-50 border border-gray-200 rounded-lg px-3 py-2 text-sm font-mono text-gray-900 select-all truncate">{fwdEmail}</code>
            <button onClick={() => navigator.clipboard.writeText(fwdEmail)}
              className="shrink-0 px-3 py-2 bg-indigo-600 text-white text-xs font-semibold rounded-lg hover:bg-indigo-700">
              Copy
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function Section({ title, children, subtle }: { title: string; children: React.ReactNode; subtle?: boolean }) {
  return (
    <div className={`border rounded-xl p-4 ${subtle ? "border-gray-100 bg-gray-50" : "border-gray-200 bg-white"}`}>
      <h2 className={`text-xs font-semibold uppercase tracking-wide mb-3 ${subtle ? "text-gray-400" : "text-gray-500"}`}>{title}</h2>
      <div className="space-y-1">{children}</div>
    </div>
  );
}

function EventRow({ event }: { event: CalEvent }) {
  const time = event.all_day ? "All day" : fmt(event.start_at);
  return (
    <div className="flex items-start gap-3 py-2 border-b border-gray-100 last:border-0">
      <span className="text-xs text-gray-400 w-16 shrink-0 pt-0.5 text-right">{time}</span>
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-gray-900 truncate">{event.title}</p>
        <div className="flex items-center gap-2 mt-0.5">
          {event.location && <p className="text-xs text-gray-500 truncate">{event.location}</p>}
          <span className="text-[10px] text-gray-400 bg-gray-100 px-1.5 py-0.5 rounded">{event.calendar_name}</span>
        </div>
      </div>
    </div>
  );
}

function fmt(iso?: string) { return iso ? new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : ""; }

function QuickLink({ href, icon, label }: { href: string; icon: string; label: string }) {
  return (
    <a href={href} className="flex items-center gap-2 border border-gray-200 rounded-xl p-3 bg-white hover:bg-gray-50 text-sm font-medium text-gray-700">
      <span>{icon}</span> {label}
    </a>
  );
}
