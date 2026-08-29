"use client";
import { useEffect, useState } from "react";
import { format, addDays, isToday, startOfWeek, addWeeks, subWeeks, isSameDay, parseISO } from "date-fns";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
function authHeaders(): Record<string, string> {
  if (typeof window === "undefined") return {};
  const hid = localStorage.getItem("sc_household_id") || "";
  const mid = localStorage.getItem("sc_member_id") || "";
  return hid ? { "x-household-id": hid, "x-member-id": mid } : {};
}

interface CalEvent {
  id: string; title: string; all_day: boolean; start_at?: string; end_at?: string;
  date?: string; location?: string; calendar_name: string;
}

export default function CalendarPage() {
  const [events, setEvents] = useState<CalEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [weekStart, setWeekStart] = useState(() => startOfWeek(new Date(), { weekStartsOn: 1 }));

  const fetchEvents = async () => {
    try {
      const r = await fetch(`${API}/api/v1/connectors/configure/calendar/events/week`, { headers: authHeaders() });
      if (r.ok) {
        const data = await r.json();
        setEvents(data.events || []);
      }
    } catch {} finally { setLoading(false); }
  };

  useEffect(() => { fetchEvents(); }, []);
  useEffect(() => {
    const iv = setInterval(() => { if (!document.hidden) fetchEvents(); }, 30000);
    return () => clearInterval(iv);
  }, []);

  const days = Array.from({ length: 7 }, (_, i) => addDays(weekStart, i));

  const eventsForDay = (day: Date) => events.filter(evt => {
    if (evt.all_day && evt.date) return evt.date === format(day, "yyyy-MM-dd");
    if (evt.start_at) return isSameDay(parseISO(evt.start_at), day);
    return false;
  }).sort((a, b) => {
    if (a.all_day && !b.all_day) return -1;
    if (!a.all_day && b.all_day) return 1;
    return (a.start_at || "") > (b.start_at || "") ? 1 : -1;
  });

  if (loading) return <div className="flex items-center justify-center min-h-[60vh] text-gray-400">Loading calendar...</div>;

  return (
    <div className="max-w-5xl mx-auto p-4 pt-8">
      <div className="flex items-center justify-between mb-4">
        <button onClick={() => setWeekStart(w => subWeeks(w, 1))} className="p-2 rounded-lg hover:bg-gray-100 text-gray-600 text-lg">←</button>
        <h2 className="text-base font-semibold text-gray-700">
          {format(weekStart, "MMM d")} – {format(addDays(weekStart, 6), "MMM d, yyyy")}
        </h2>
        <button onClick={() => setWeekStart(w => addWeeks(w, 1))} className="p-2 rounded-lg hover:bg-gray-100 text-gray-600 text-lg">→</button>
      </div>

      <div className="grid grid-cols-7 gap-1">
        {days.map(day => (
          <div key={day.toISOString()} className="text-center pb-2">
            <p className={`text-xs uppercase tracking-wide ${isToday(day) ? "text-indigo-600 font-bold" : "text-gray-500"}`}>{format(day, "EEE")}</p>
            <p className={`text-lg mt-0.5 leading-none ${isToday(day) ? "w-8 h-8 bg-indigo-600 text-white rounded-full flex items-center justify-center mx-auto" : "text-gray-700"}`}>
              {format(day, "d")}
            </p>
          </div>
        ))}

        {days.map(day => {
          const dayEvents = eventsForDay(day);
          return (
            <div key={day.toISOString()} className={`min-h-24 rounded-lg p-1.5 space-y-1 ${isToday(day) ? "bg-indigo-50" : "bg-gray-50"}`}>
              {dayEvents.map(evt => (
                <div key={evt.id} className="bg-white border border-gray-200 rounded-lg p-1.5 text-xs hover:shadow-sm transition-shadow">
                  {!evt.all_day && evt.start_at && (
                    <p className="text-[10px] text-indigo-600 font-medium">{format(parseISO(evt.start_at), "h:mm a")}</p>
                  )}
                  {evt.all_day && <p className="text-[10px] text-gray-400">All day</p>}
                  <p className="font-medium text-gray-800 truncate leading-tight">{evt.title}</p>
                  {evt.location && <p className="text-[10px] text-gray-400 truncate">{evt.location}</p>}
                  <p className="text-[9px] text-gray-300 mt-0.5">{evt.calendar_name}</p>
                </div>
              ))}
              {dayEvents.length === 0 && <div className="h-full" />}
            </div>
          );
        })}
      </div>

      {events.length === 0 && (
        <div className="mt-8 text-center text-gray-500">
          <p className="text-sm">No events this week.</p>
          <p className="text-xs mt-1">Connect your Google Calendar in <a href="/settings/connected-services" className="text-indigo-600 hover:underline">Settings</a> to see events here.</p>
        </div>
      )}
    </div>
  );
}
