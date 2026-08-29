"use client";
/**
 * Security settings — phone, email, active sessions (§10.6 / §10.7).
 *
 * Phone change is self-serve with re-auth required first (§10.7).
 * Active sessions are listable and individually revocable.
 * Enumeration resistance: all auth flows return ambiguous responses.
 */
import { useEffect, useState } from "react";

interface Session {
  id: string;
  device_label: string;
  last_active_at: string;
  ip_created_from?: string;
}

export default function SecurityPage() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [member, setMember] = useState<{ phone_e164?: string; email?: string } | null>(null);
  const [loading, setLoading] = useState(true);
  const [changingPhone, setChangingPhone] = useState(false);
  const [newPhone, setNewPhone] = useState("");
  const [phoneOtp, setPhoneOtp] = useState("");
  const [phoneStep, setPhoneStep] = useState<"idle" | "otp" | "done">("idle");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  useEffect(() => {
    Promise.all([
      fetch("/api/v1/identity/me", { headers: authHeader() }).then(r => r.json()),
      fetch("/api/v1/identity/sessions", { headers: authHeader() }).then(r => r.json()).catch(() => []),
    ]).then(([me, sess]) => {
      setMember(me);
      setSessions(Array.isArray(sess) ? sess : []);
    }).finally(() => setLoading(false));
  }, []);

  const handleRequestPhoneChange = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    try {
      // Re-auth required before phone change (§10.7)
      const res = await fetch("/api/v1/identity/phone-change/request", {
        method: "POST",
        headers: { ...authHeader(), "Content-Type": "application/json" },
        body: JSON.stringify({ new_phone_e164: newPhone }),
      });
      if (!res.ok) throw new Error((await res.json()).detail);
      setPhoneStep("otp");
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleConfirmPhoneChange = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    try {
      const res = await fetch("/api/v1/identity/phone-change/confirm", {
        method: "POST",
        headers: { ...authHeader(), "Content-Type": "application/json" },
        body: JSON.stringify({ new_phone_e164: newPhone, otp_code: phoneOtp }),
      });
      if (!res.ok) throw new Error((await res.json()).detail);
      setPhoneStep("done");
      setSuccess("Done — text the KinValet from your new number and it'll recognize you. Your old number no longer works.");
      setChangingPhone(false);
    } catch (e: any) {
      setError(e.message);
    }
  };

  const handleRevokeSession = async (sessionId: string) => {
    await fetch(`/api/v1/identity/sessions/${sessionId}`, {
      method: "DELETE",
      headers: authHeader(),
    });
    setSessions(prev => prev.filter(s => s.id !== sessionId));
  };

  const handleRevokeAll = async () => {
    if (!confirm("Sign out of all devices?")) return;
    await fetch("/api/v1/identity/sessions", { method: "DELETE", headers: authHeader() });
    setSessions([]);
  };

  if (loading) return <p className="p-6 text-gray-500">Loading...</p>;

  return (
    <div className="max-w-xl mx-auto p-6 pt-10 space-y-6">
      <h1 className="text-2xl font-semibold text-gray-900">Security</h1>

      {success && (
        <div className="bg-green-50 border border-green-200 rounded-lg p-3 text-sm text-green-800">
          {success}
        </div>
      )}

      {/* Phone + Email */}
      <Section title="Login & Contact">
        <Row
          label="Phone (WhatsApp + login)"
          value={member?.phone_e164 ? maskPhone(member.phone_e164) : "Not set"}
          action={
            !changingPhone ? (
              <button onClick={() => setChangingPhone(true)} className="text-sm text-blue-600 hover:underline">
                Change
              </button>
            ) : null
          }
        />
        <Row
          label="Email (recovery)"
          value={member?.email ? maskEmail(member.email) : "Not set"}
        />

        {changingPhone && (
          <div className="mt-4 border-t pt-4 space-y-3">
            <p className="text-sm text-gray-600">
              Enter your new phone number. We'll send a verification code.
            </p>
            {phoneStep === "idle" && (
              <form onSubmit={handleRequestPhoneChange} className="flex gap-2">
                <input
                  type="tel"
                  value={newPhone}
                  onChange={e => setNewPhone(e.target.value)}
                  placeholder="+1 555 000 0000"
                  className="flex-1 border border-gray-300 rounded px-3 py-1.5 text-sm"
                  required
                />
                <button className="bg-gray-900 text-white text-sm px-3 py-1.5 rounded hover:bg-gray-700">
                  Send code
                </button>
                <button
                  type="button"
                  onClick={() => setChangingPhone(false)}
                  className="text-sm text-gray-500 hover:text-gray-700"
                >
                  Cancel
                </button>
              </form>
            )}
            {phoneStep === "otp" && (
              <form onSubmit={handleConfirmPhoneChange} className="flex gap-2">
                <input
                  type="text"
                  value={phoneOtp}
                  onChange={e => setPhoneOtp(e.target.value)}
                  placeholder="Enter code"
                  maxLength={6}
                  className="flex-1 border border-gray-300 rounded px-3 py-1.5 text-sm tracking-widest text-center"
                  required
                  autoFocus
                />
                <button className="bg-gray-900 text-white text-sm px-3 py-1.5 rounded hover:bg-gray-700">
                  Verify
                </button>
              </form>
            )}
            {error && <p className="text-sm text-red-600">{error}</p>}
          </div>
        )}
      </Section>

      {/* Active Sessions */}
      <Section title="Active Sessions">
        {sessions.length === 0 ? (
          <p className="text-sm text-gray-500">No active sessions.</p>
        ) : (
          <>
            {sessions.map(s => (
              <div key={s.id} className="flex items-center justify-between py-2 border-b border-gray-100 last:border-0">
                <div>
                  <p className="text-sm text-gray-800">{s.device_label}</p>
                  <p className="text-xs text-gray-500">
                    {s.ip_created_from} · {formatRelative(s.last_active_at)}
                  </p>
                </div>
                <button
                  onClick={() => handleRevokeSession(s.id)}
                  className="text-xs text-red-500 hover:text-red-700"
                >
                  Sign out
                </button>
              </div>
            ))}
            <div className="pt-2">
              <button onClick={handleRevokeAll} className="text-sm text-red-600 hover:underline">
                Sign out everywhere
              </button>
            </div>
          </>
        )}
      </Section>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="border border-gray-200 rounded-lg p-4 bg-white">
      <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">{title}</h2>
      {children}
    </div>
  );
}

function Row({ label, value, action }: { label: string; value: string; action?: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between py-2 border-b border-gray-100 last:border-0">
      <div>
        <p className="text-sm text-gray-500">{label}</p>
        <p className="text-sm font-medium text-gray-800">{value}</p>
      </div>
      {action}
    </div>
  );
}

function maskPhone(phone: string) {
  return phone.slice(0, 3) + " ••• " + phone.slice(-4);
}

function maskEmail(email: string) {
  const [user, domain] = email.split("@");
  return user.slice(0, 2) + "••••@" + domain;
}

function formatRelative(iso: string) {
  const diff = Date.now() - new Date(iso).getTime();
  if (diff < 60000) return "active now";
  if (diff < 3600000) return `${Math.floor(diff / 60000)} min ago`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)} hours ago`;
  return `${Math.floor(diff / 86400000)} days ago`;
}

function authHeader(): Record<string, string> {
  if (typeof window === "undefined") return {};
  const token = localStorage.getItem("sb-access-token") || "";
  return token ? { Authorization: `Bearer ${token}` } : ({} as Record<string, string>);
}
