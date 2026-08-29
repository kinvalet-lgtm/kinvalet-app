"use client";
/**
 * Onboarding wizard — primary user registration (§10.4 / §11.1).
 *
 * Steps:
 * 1. Welcome — what KinValet is and what you'll need
 * 2. Your household — name + timezone
 * 3. About you — display name + phone + email
 * 4. Registering... — API call (atomic: household + member + phone route)
 * 5. Success — save the WhatsApp number, invite family, connect calendar
 *
 * Onboarding completes when: invite option shown, WhatsApp number displayed.
 * Calendar connection and family invite are optional but prompted (§11.1).
 *
 * No Supabase required for registration — the backend creates the identity.
 * Auth token is stored in localStorage for dashboard access in this MVP flow.
 */

import { useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const WHATSAPP_NUMBER = "+1 (737) 258-3478";
const WHATSAPP_NUMBER_HREF = "https://wa.me/17372583478";

type Step = "welcome" | "household" | "you" | "registering" | "success" | "error";

interface FormData {
  household_name: string;
  timezone: string;
  display_name: string;
  phone_e164: string;
  email: string;
}

interface RegistrationResult {
  household: { id: string; name: string; timezone: string; forwarding_email?: string };
  member: { id: string; display_name: string; role: string };
}

const TIMEZONES = [
  { value: "America/New_York",    label: "Eastern (New York)" },
  { value: "America/Chicago",     label: "Central (Chicago)" },
  { value: "America/Denver",      label: "Mountain (Denver)" },
  { value: "America/Los_Angeles", label: "Pacific (Los Angeles)" },
  { value: "America/Phoenix",     label: "Mountain (Phoenix, no DST)" },
  { value: "America/Anchorage",   label: "Alaska (Anchorage)" },
  { value: "Pacific/Honolulu",    label: "Hawaii (Honolulu)" },
];

export default function OnboardingPage() {
  const [step, setStep] = useState<Step>("welcome");
  const [form, setForm] = useState<FormData>({
    household_name: "",
    timezone: "America/New_York",
    display_name: "",
    phone_e164: "",
    email: "",
  });
  const [result, setResult] = useState<RegistrationResult | null>(null);
  const [error, setError] = useState("");

  const set = (field: keyof FormData) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm(f => ({ ...f, [field]: e.target.value }));

  const normalizePhone = (raw: string): string => {
    const digits = raw.replace(/\D/g, "");
    if (digits.startsWith("1") && digits.length === 11) return `+${digits}`;
    if (digits.length === 10) return `+1${digits}`;
    return `+${digits}`;
  };

  const register = async () => {
    setStep("registering");
    setError("");
    try {
      const body = {
        household_name: form.household_name,
        timezone: form.timezone,
        display_name: form.display_name,
        phone_e164: normalizePhone(form.phone_e164),
        email: form.email,
      };
      const res = await fetch(`${API}/api/v1/identity/register`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Registration failed");
      setResult(data);
      // Store household/member IDs + forwarding email for the dashboard
      localStorage.setItem("sc_household_id", data.household.id);
      localStorage.setItem("sc_member_id", data.member.id);
      localStorage.setItem("sc_display_name", data.member.display_name);
      localStorage.setItem("sc_household_name", data.household.name);
      if (data.household.forwarding_email) {
        localStorage.setItem("sc_forwarding_email", data.household.forwarding_email);
      }
      setStep("success");
    } catch (e: any) {
      setError(e.message);
      setStep("error");
    }
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 via-indigo-50 to-purple-50 flex flex-col items-center justify-center p-4">
      <div className="w-full max-w-lg">
        {/* Progress bar */}
        {step !== "success" && step !== "error" && (
          <div className="mb-6">
            <div className="flex justify-between text-xs text-gray-400 mb-1">
              <span>Welcome</span>
              <span>Household</span>
              <span>About you</span>
              <span>Done</span>
            </div>
            <div className="h-1.5 bg-gray-200 rounded-full">
              <div
                className="h-1.5 bg-indigo-600 rounded-full transition-all duration-500"
                style={{ width: { welcome: "10%", household: "40%", you: "70%", registering: "90%" }[step] || "0%" }}
              />
            </div>
          </div>
        )}

        {/* Step: Welcome */}
        {step === "welcome" && (
          <Card>
            <div className="text-center mb-6">
              <div className="text-4xl mb-3">✦</div>
              <h1 className="text-2xl font-bold text-gray-900">Welcome to KinValet</h1>
              <p className="text-gray-500 mt-2 text-sm leading-relaxed">
                Your AI-powered family operations assistant. Text us anything — permission slips,
                appointments, errands — and we'll take care of the rest.
              </p>
            </div>

            <div className="space-y-3 mb-6">
              <Feature icon="💬" title="Zero-UI input" desc="Text, photo, or forward an email. No app to install." />
              <Feature icon="📅" title="Smart scheduling" desc="We detect conflicts and send leave-by reminders with real traffic." />
              <Feature icon="🌅" title="Daily 8 AM briefing" desc="Your day at a glance, delivered to WhatsApp every morning." />
              <Feature icon="👨‍👩‍👧" title="Whole family" desc="Invite your spouse or co-caregiver. Everyone stays in the loop." />
            </div>

            <p className="text-xs text-gray-400 mb-4 text-center">
              You'll need: your mobile phone number (US) and an email address.
              Takes about 2 minutes.
            </p>

            <Button onClick={() => setStep("household")}>Get started →</Button>
            <p className="text-xs text-center text-gray-400 mt-4">
              Already have a household?{" "}
              <a href="/login" className="text-indigo-600 hover:underline font-medium">Sign in →</a>
            </p>
          </Card>
        )}

        {/* Step: Household */}
        {step === "household" && (
          <Card>
            <h2 className="text-xl font-bold text-gray-900 mb-1">Name your household</h2>
            <p className="text-sm text-gray-500 mb-5">
              This is how your daily briefing will address you.
            </p>

            <div className="space-y-4">
              <Field label="Household name">
                <input
                  value={form.household_name}
                  onChange={set("household_name")}
                  placeholder='e.g. "The Miller Family"'
                  className={inputCls}
                  autoFocus
                />
              </Field>

              <Field label="Your timezone" hint="Used to send your 8:00 AM briefing at the right time">
                <select value={form.timezone} onChange={set("timezone")} className={selectCls}>
                  {TIMEZONES.map(tz => (
                    <option key={tz.value} value={tz.value}>{tz.label}</option>
                  ))}
                </select>
              </Field>
            </div>

            <div className="flex gap-3 mt-6">
              <Button secondary onClick={() => setStep("welcome")}>← Back</Button>
              <Button
                onClick={() => setStep("you")}
                disabled={!form.household_name.trim()}
              >
                Next →
              </Button>
            </div>
          </Card>
        )}

        {/* Step: About you */}
        {step === "you" && (
          <Card>
            <h2 className="text-xl font-bold text-gray-900 mb-1">About you</h2>
            <p className="text-sm text-gray-500 mb-5">
              Your mobile number is how we recognize you on WhatsApp.
            </p>

            <div className="space-y-4">
              <Field label="Your first name">
                <input
                  value={form.display_name}
                  onChange={set("display_name")}
                  placeholder="Sarah"
                  className={inputCls}
                  autoFocus
                />
              </Field>

              <Field label="Mobile number (US)" hint="We'll route your WhatsApp messages to your household">
                <input
                  value={form.phone_e164}
                  onChange={set("phone_e164")}
                  placeholder="+1 (555) 000-0000"
                  type="tel"
                  className={inputCls}
                />
              </Field>

              <Field label="Email" hint="Used for account recovery only — no marketing">
                <input
                  value={form.email}
                  onChange={set("email")}
                  placeholder="sarah@email.com"
                  type="email"
                  className={inputCls}
                />
              </Field>
            </div>

            {/* Preview */}
            {form.display_name && (
              <div className="mt-4 bg-gray-50 rounded-lg p-3 border border-gray-100 text-sm text-gray-600">
                Setting up <strong>{form.household_name}</strong> for <strong>{form.display_name}</strong>
                {form.timezone && ` · ${TIMEZONES.find(t => t.value === form.timezone)?.label}`}
              </div>
            )}

            <div className="flex gap-3 mt-6">
              <Button secondary onClick={() => setStep("household")}>← Back</Button>
              <Button
                onClick={register}
                disabled={!form.display_name.trim() || !form.phone_e164.trim() || !form.email.trim()}
              >
                Create my household →
              </Button>
            </div>
          </Card>
        )}

        {/* Step: Registering */}
        {step === "registering" && (
          <Card>
            <div className="text-center py-8">
              <div className="text-4xl mb-4 animate-pulse">⚙️</div>
              <h2 className="text-lg font-semibold text-gray-900">Setting up your household…</h2>
              <p className="text-sm text-gray-500 mt-2">Creating your account and routing your WhatsApp number.</p>
            </div>
          </Card>
        )}

        {/* Step: Error */}
        {step === "error" && (
          <Card>
            <div className="text-center py-4">
              <div className="text-4xl mb-3">⚠️</div>
              <h2 className="text-lg font-semibold text-gray-900 mb-2">Something went wrong</h2>
              <p className="text-sm text-red-600 bg-red-50 rounded-lg p-3 mb-4">{error}</p>
              <div className="flex gap-3 justify-center">
                <Button secondary onClick={() => setStep("you")}>← Go back</Button>
                <Button onClick={register}>Try again</Button>
              </div>
            </div>
          </Card>
        )}

        {/* Step: Success */}
        {step === "success" && result && (
          <Card>
            <div className="text-center mb-6">
              <div className="text-5xl mb-3">🎉</div>
              <h2 className="text-2xl font-bold text-gray-900">
                Welcome, {result.member.display_name}!
              </h2>
              <p className="text-sm text-gray-500 mt-1">
                {result.household.name} is all set up.
              </p>
            </div>

            {/* Family forwarding email */}
            {result.household.forwarding_email && (
              <div className="bg-indigo-50 border border-indigo-200 rounded-xl p-4 mb-4">
                <p className="text-xs font-semibold text-indigo-700 uppercase tracking-wide mb-1">Your family's email address</p>
                <div className="flex items-center gap-2">
                  <code className="flex-1 bg-white border border-indigo-200 rounded-lg px-3 py-2 text-sm font-mono text-indigo-900 select-all">
                    {result.household.forwarding_email}
                  </code>
                  <button
                    onClick={() => {
                      navigator.clipboard.writeText(result.household.forwarding_email!);
                    }}
                    className="shrink-0 px-3 py-2 bg-indigo-600 text-white text-xs font-semibold rounded-lg hover:bg-indigo-700"
                  >
                    Copy
                  </button>
                </div>
                <p className="text-xs text-indigo-600 mt-2">
                  Forward school and care emails here. Set up a Gmail filter in step 3 below.
                </p>
              </div>
            )}

            {/* Step 1: Save WhatsApp number */}
            <div className="space-y-4">
              <OnboardingStep
                number={1}
                title="Save the KinValet number"
                status="required"
              >
                <p className="text-sm text-gray-600 mb-3">
                  Save <strong>{WHATSAPP_NUMBER}</strong> as a contact on your phone.
                  Every message you send to it from your registered number goes to your household.
                </p>
                <a
                  href={WHATSAPP_HREF}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-2 bg-green-500 text-white text-sm font-semibold px-5 py-2.5 rounded-xl hover:bg-green-600 transition-colors shadow-sm shadow-green-200"
                >
                  <span>Open in WhatsApp</span>
                  <span>→</span>
                </a>
                <p className="text-xs text-gray-400 mt-2">
                  Or manually save <strong>{WHATSAPP_NUMBER}</strong> and send it a message.
                </p>
              </OnboardingStep>

              {/* Step 2: First message */}
              <OnboardingStep
                number={2}
                title="Send your first message"
                status="optional"
              >
                <p className="text-sm text-gray-600 mb-2">Try texting something like:</p>
                <div className="space-y-2">
                  {[
                    "Leo has soccer practice Tuesday at 6pm at Field B",
                    "Dad's doctor appointment Friday at 2pm at Memorial Hospital",
                    "Pay the tutor $150 this Friday",
                  ].map((ex) => (
                    <div key={ex} className="bg-slate-50 rounded-xl px-4 py-2.5 text-sm text-slate-700 font-mono border border-slate-100 leading-relaxed">
                      "{ex}"
                    </div>
                  ))}
                </div>
                <p className="text-xs text-gray-400 mt-2">
                  You'll get a WhatsApp confirmation within 8 seconds.
                </p>
              </OnboardingStep>

              {/* Step 3: Connect Gmail — pick which folders to watch */}
              <OnboardingStep
                number={3}
                title="Connect Gmail — choose folders to watch"
                status="optional"
              >
                <p className="text-sm text-gray-600 mb-2">
                  Connect your Gmail and select exactly which folders KinValet can read.
                  We <strong>only</strong> scan the labels you choose — nothing else.
                </p>
                <div className="bg-slate-50 border border-slate-200 rounded-xl p-3 mb-3 text-xs text-slate-600 space-y-1.5">
                  <p><strong>How it works:</strong></p>
                  <p>1. Connect your Google account (read-only access — we never send or delete email)</p>
                  <p>2. A label picker shows all your Gmail folders</p>
                  <p>3. Select only the ones that matter — e.g. <strong>School</strong>, <strong>Healthcare</strong></p>
                  <p>4. KinValet extracts appointments, deadlines, and costs from those folders</p>
                </div>
                <div className="bg-indigo-50 border border-indigo-100 rounded-xl p-3 mb-3 text-xs text-indigo-700">
                  <strong>Tip:</strong> Create a label like "KinValet" in Gmail, then set up a filter
                  (e.g. from <em>*@school.edu</em> → apply label "KinValet"). Then select only that label here.
                  This way KinValet only ever sees emails you've explicitly routed to it.
                </div>
                <a
                  href="/settings/connected-services"
                  className="inline-flex items-center gap-2 border border-gray-300 text-gray-700 text-sm font-semibold px-4 py-2.5 rounded-xl hover:bg-gray-50 transition-colors"
                >
                  ✉️ Connect Gmail & pick folders →
                </a>
              </OnboardingStep>

              {/* Step 4: Connect calendar — pick which calendars to sync */}
              <OnboardingStep
                number={4}
                title="Connect your calendar"
                status="optional"
              >
                <p className="text-sm text-gray-600 mb-2">
                  Connect Google Calendar and choose which calendars to sync.
                </p>
                <div className="bg-slate-50 border border-slate-200 rounded-xl p-3 mb-3 text-xs text-slate-600 space-y-1.5">
                  <p><strong>After connecting you'll pick:</strong></p>
                  <p>• Which calendars to <strong>read</strong> (used for conflict detection)</p>
                  <p>• Which <strong>one calendar</strong> to write new events to</p>
                  <p>• Shared calendars (e.g. school calendar) are read-only — we never write to them</p>
                </div>
                <a
                  href="/settings/connected-services"
                  className="inline-flex items-center gap-2 border border-gray-300 text-gray-700 text-sm font-semibold px-4 py-2.5 rounded-xl hover:bg-gray-50 transition-colors"
                >
                  📅 Connect Calendar & pick calendars →
                </a>
              </OnboardingStep>

              {/* Step 5: Invite family */}
              <OnboardingStep
                number={5}
                title="Invite your family"
                status="optional"
              >
                <p className="text-sm text-gray-600 mb-2">
                  Add your spouse, co-parent, or sibling. Each person connects their own
                  Gmail and calendar independently — their folders and calendars are separate from yours.
                </p>
                <a
                  href="/settings/members"
                  className="inline-flex items-center gap-2 border border-gray-300 text-gray-700 text-sm font-semibold px-4 py-2.5 rounded-xl hover:bg-gray-50 transition-colors"
                >
                  👨‍👩‍👧 Add family members →
                </a>
              </OnboardingStep>
            </div>

            <div className="mt-6 pt-4 border-t border-gray-100">
              <a
                href="/briefing"
                className="block w-full text-center bg-indigo-600 text-white font-semibold py-3.5 rounded-xl hover:bg-indigo-700 transition-colors shadow-sm shadow-indigo-200"
              >
                Go to my dashboard →
              </a>
              <p className="text-xs text-center text-gray-400 mt-2">
                Your daily 8:00 AM briefing starts tomorrow.
              </p>
            </div>
          </Card>
        )}
      </div>
    </div>
  );
}

// ── Sub-components ─────────────────────────────────────────────────────────────

function Card({ children }: { children: React.ReactNode }) {
  return (
    <div className="bg-white rounded-2xl shadow-md border border-gray-100 p-8">
      {children}
    </div>
  );
}

function Feature({ icon, title, desc }: { icon: string; title: string; desc: string }) {
  return (
    <div className="flex items-start gap-3 p-3 rounded-xl hover:bg-indigo-50 transition-colors">
      <span className="text-xl shrink-0 mt-0.5">{icon}</span>
      <div>
        <p className="text-sm font-semibold text-gray-800">{title}</p>
        <p className="text-xs text-gray-500 mt-0.5 leading-relaxed">{desc}</p>
      </div>
    </div>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-sm font-semibold text-gray-700 mb-1.5">{label}</label>
      {children}
      {hint && <p className="text-xs text-gray-400 mt-1.5 leading-relaxed">{hint}</p>}
    </div>
  );
}

function Button({
  children,
  onClick,
  disabled,
  secondary,
}: {
  children: React.ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  secondary?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`flex-1 py-3 px-4 rounded-xl text-sm font-semibold transition-all duration-150 disabled:opacity-40 disabled:cursor-not-allowed shadow-sm ${
        secondary
          ? "bg-gray-100 text-gray-600 hover:bg-gray-200 hover:text-gray-800"
          : "bg-indigo-600 text-white hover:bg-indigo-700 shadow-indigo-100"
      }`}
    >
      {children}
    </button>
  );
}

function OnboardingStep({
  number,
  title,
  status,
  children,
}: {
  number: number;
  title: string;
  status: "required" | "optional";
  children: React.ReactNode;
}) {
  return (
    <div className={`rounded-xl p-4 border ${status === "required" ? "border-indigo-200 bg-indigo-50" : "border-gray-200 bg-white"}`}>
      <div className="flex items-center gap-3 mb-3">
        <div className={`w-7 h-7 rounded-full text-white text-xs font-bold flex items-center justify-center shrink-0 ${status === "required" ? "bg-indigo-600" : "bg-gray-700"}`}>
          {number}
        </div>
        <h3 className="text-sm font-semibold text-gray-900 flex-1">{title}</h3>
        <span
          className={`text-xs px-2 py-0.5 rounded-full font-medium ${
            status === "required"
              ? "bg-blue-100 text-blue-700"
              : "bg-gray-100 text-gray-500"
          }`}
        >
          {status === "required" ? "Do this now" : "Optional"}
        </span>
      </div>
      {children}
    </div>
  );
}

const inputCls =
  "w-full border border-gray-300 rounded-xl px-4 py-3 text-[15px] font-medium text-gray-900 placeholder-gray-400 bg-white focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 transition-shadow";

const selectCls =
  "w-full border border-gray-300 rounded-xl px-4 py-3 text-[15px] font-medium text-gray-900 bg-white focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 transition-shadow";

const WHATSAPP_HREF = `https://wa.me/17372583478?text=Hi!%20I%20just%20set%20up%20my%20KinValet%20account.`;
