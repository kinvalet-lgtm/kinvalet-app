"use client";
/**
 * Notification Preferences settings page (§11.19).
 *
 * Controls per-member, per-category notification preferences.
 * Delegation requests deliberately absent — they cannot be disabled.
 * A request needing your answer is not a notification you can mute
 * without breaking household coordination (PRD §11.19).
 */
import { useEffect, useState } from "react";
import { api, NotificationPreference } from "@/lib/api";

const CATEGORY_INFO: Record<string, { label: string; description: string; canDisable: boolean }> = {
  daily_briefing: {
    label: "Daily Briefing",
    description: "Your 8:00 AM summary of today's schedule and action items",
    canDisable: true,
  },
  leave_by: {
    label: "Leave-By Reminders",
    description: "Traffic-aware alerts for when to leave for time-bound items",
    canDisable: true,
  },
  task_reminder: {
    label: "Task Reminders",
    description: "Reminders before items with a set time",
    canDisable: true,
  },
  conflict_alert: {
    label: "Conflict Alerts",
    description: "When a scheduling conflict is detected on your calendar",
    canDisable: true,
  },
  financial_alert: {
    label: "Financial Alerts",
    description: "Anomaly alerts from connected financial accounts",
    canDisable: true,
  },
  feedback_prompt: {
    label: "Feedback Prompts",
    description: "Occasional satisfaction check-ins (NPS, thumbs up/down)",
    canDisable: true,
  },
};

export default function PreferencesPage() {
  const [prefs, setPrefs] = useState<NotificationPreference[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState<string | null>(null);
  const [pauseMode, setPauseMode] = useState(false);

  useEffect(() => {
    api.notification.getPreferences()
      .then(data => {
        setPrefs(data);
        // Check if opted out globally
        const any = data.find(p => p.whatsapp_opted_out);
        if (any) setPauseMode(true);
      })
      .finally(() => setLoading(false));
  }, []);

  const getPref = (category: string): NotificationPreference => {
    return prefs.find(p => p.category === category) || {
      category,
      enabled: true,
      delivery_mode: "immediate",
      quiet_hours_start: "21:00",
      quiet_hours_end: "07:00",
      daily_notification_ceiling: 12,
      whatsapp_opted_out: false,
    };
  };

  const update = async (category: string, patch: Partial<NotificationPreference>) => {
    setSaving(category);
    try {
      await api.notification.updatePreference({ category, ...patch });
      setPrefs(prev => {
        const existing = prev.find(p => p.category === category);
        if (existing) {
          return prev.map(p => p.category === category ? { ...p, ...patch } : p);
        }
        return [...prev, { ...getPref(category), ...patch }];
      });
    } catch (e: any) {
      alert(e.message);
    } finally {
      setSaving(null);
    }
  };

  if (loading) return <p className="p-6 text-gray-500">Loading...</p>;

  return (
    <div className="max-w-xl mx-auto p-6 pt-10 space-y-6">
      <h1 className="text-2xl font-semibold text-gray-900">Notification Preferences</h1>

      {/* WhatsApp opt-out status */}
      {pauseMode && (
        <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-4">
          <p className="text-sm text-yellow-800 font-medium">WhatsApp notifications are paused</p>
          <p className="text-sm text-yellow-700 mt-1">
            You replied STOP. The dashboard still works. Reply START to resume.
          </p>
        </div>
      )}

      {/* Quiet Hours */}
      <div className="border border-gray-200 rounded-lg bg-white p-4">
        <h2 className="text-sm font-semibold text-gray-900 mb-1">Quiet Hours</h2>
        <p className="text-xs text-gray-500 mb-3">
          Non-urgent messages are held during this window. Urgent items (critical priority) always break through.
        </p>
        <div className="flex items-center gap-3">
          <div>
            <label className="text-xs text-gray-500 block mb-1">Start</label>
            <input
              type="time"
              defaultValue={getPref("task_reminder").quiet_hours_start}
              onChange={e => {
                // Update all categories at once
                Object.keys(CATEGORY_INFO).forEach(cat =>
                  update(cat, { quiet_hours_start: e.target.value })
                );
              }}
              className="border border-gray-300 rounded px-2 py-1 text-sm"
            />
          </div>
          <span className="text-gray-400 mt-4">to</span>
          <div>
            <label className="text-xs text-gray-500 block mb-1">End</label>
            <input
              type="time"
              defaultValue={getPref("task_reminder").quiet_hours_end}
              onChange={e => {
                Object.keys(CATEGORY_INFO).forEach(cat =>
                  update(cat, { quiet_hours_end: e.target.value })
                );
              }}
              className="border border-gray-300 rounded px-2 py-1 text-sm"
            />
          </div>
        </div>
      </div>

      {/* Daily ceiling */}
      <div className="border border-gray-200 rounded-lg bg-white p-4">
        <h2 className="text-sm font-semibold text-gray-900 mb-1">Daily Message Limit</h2>
        <p className="text-xs text-gray-500 mb-3">
          Non-urgent messages beyond this limit are batched into a single digest rather than sent individually.
          This is a safety valve against the product becoming the very overwhelm it exists to remove.
        </p>
        <div className="flex items-center gap-3">
          <input
            type="number"
            min={1}
            max={50}
            defaultValue={getPref("daily_briefing").daily_notification_ceiling}
            onChange={e => {
              const val = parseInt(e.target.value);
              if (val >= 1) {
                Object.keys(CATEGORY_INFO).forEach(cat =>
                  update(cat, { daily_notification_ceiling: val })
                );
              }
            }}
            className="w-20 border border-gray-300 rounded px-2 py-1 text-sm"
          />
          <span className="text-sm text-gray-600">messages per day</span>
        </div>
      </div>

      {/* Per-category toggles */}
      <div className="border border-gray-200 rounded-lg bg-white divide-y divide-gray-100">
        {Object.entries(CATEGORY_INFO).map(([category, info]) => {
          const pref = getPref(category);
          const isSaving = saving === category;
          return (
            <div key={category} className="flex items-center justify-between p-4">
              <div className="flex-1 mr-4">
                <p className="text-sm font-medium text-gray-900">{info.label}</p>
                <p className="text-xs text-gray-500 mt-0.5">{info.description}</p>
              </div>
              {info.canDisable && (
                <button
                  onClick={() => update(category, { enabled: !pref.enabled })}
                  disabled={isSaving}
                  className={`relative inline-flex h-5 w-9 shrink-0 rounded-full transition-colors ${
                    pref.enabled ? "bg-gray-900" : "bg-gray-200"
                  } disabled:opacity-50`}
                >
                  <span
                    className={`inline-block h-4 w-4 mt-0.5 rounded-full bg-white shadow transition-transform ${
                      pref.enabled ? "translate-x-4" : "translate-x-0.5"
                    }`}
                  />
                </button>
              )}
            </div>
          );
        })}

        {/* Delegation requests — cannot be disabled */}
        <div className="flex items-center justify-between p-4 bg-gray-50">
          <div className="flex-1 mr-4">
            <p className="text-sm font-medium text-gray-500">Delegation Requests</p>
            <p className="text-xs text-gray-400 mt-0.5">
              Always on — a request needing your answer can't be muted without breaking household coordination
            </p>
          </div>
          <div className="text-xs text-gray-400">Always on</div>
        </div>
      </div>
    </div>
  );
}
