"use client";
/**
 * Login page — for returning users who already have a household.
 * MVP: phone lookup restores the session. Production: Supabase OTP.
 */
import { useState } from "react";
import { useRouter } from "next/navigation";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const inputCls =
  "w-full border border-gray-300 rounded-xl px-4 py-3 text-[15px] font-medium text-gray-900 placeholder-gray-400 bg-white focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 transition-shadow";

export default function LoginPage() {
  const router = useRouter();
  const [phone, setPhone] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [notFound, setNotFound] = useState(false);

  const normalizePhone = (raw: string): string => {
    const digits = raw.replace(/\D/g, "");
    if (digits.startsWith("1") && digits.length === 11) return `+${digits}`;
    if (digits.length === 10) return `+1${digits}`;
    return `+${digits}`;
  };

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError("");
    setNotFound(false);
    try {
      const normalized = normalizePhone(phone);
      const res = await fetch(`${API}/api/v1/identity/lookup`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ phone_e164: normalized }),
      });
      const data = await res.json();

      if (!data.found) {
        setNotFound(true);
        return;
      }

      // Restore session
      localStorage.setItem("sc_household_id", data.household_id);
      localStorage.setItem("sc_member_id", data.member_id);
      localStorage.setItem("sc_display_name", data.display_name);
      localStorage.setItem("sc_household_name", data.household_name);

      router.push("/briefing");
    } catch (e: any) {
      setError(e.message || "Something went wrong");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-slate-50 via-indigo-50 to-purple-50 p-4">
      <div className="w-full max-w-sm bg-white rounded-2xl shadow-md border border-gray-100 p-8">
        <div className="mb-7 text-center">
          <div className="text-4xl mb-2">✦</div>
          <h1 className="text-xl font-bold text-gray-900">KinValet</h1>
          <p className="text-sm text-gray-500 mt-1">Sign in to your household</p>
        </div>

        <form onSubmit={handleLogin} className="space-y-4">
          <div>
            <label className="block text-sm font-semibold text-gray-700 mb-1.5">
              Your registered phone number
            </label>
            <input
              type="tel"
              value={phone}
              onChange={e => setPhone(e.target.value)}
              placeholder="+1 (555) 000-0000"
              className={inputCls}
              required
              autoFocus
            />
          </div>

          {error && (
            <div className="bg-red-50 border border-red-200 rounded-xl px-4 py-3 text-sm text-red-700">
              {error}
            </div>
          )}

          {notFound && (
            <div className="bg-amber-50 border border-amber-200 rounded-xl px-4 py-3 text-sm text-amber-800">
              No household found for that number.{" "}
              <a href="/onboarding" className="text-indigo-600 font-semibold hover:underline">
                Create one →
              </a>
            </div>
          )}

          <button
            type="submit"
            disabled={loading || !phone.trim()}
            className="w-full bg-indigo-600 text-white rounded-xl py-3 text-sm font-semibold hover:bg-indigo-700 disabled:opacity-50 transition-colors shadow-sm"
          >
            {loading ? "Looking up..." : "Sign in →"}
          </button>
        </form>

        <div className="mt-6 pt-4 border-t border-gray-100 text-center">
          <p className="text-xs text-gray-400">
            New here?{" "}
            <a href="/onboarding" className="text-indigo-600 hover:underline font-medium">
              Create a household
            </a>
          </p>
        </div>
      </div>
    </div>
  );
}
