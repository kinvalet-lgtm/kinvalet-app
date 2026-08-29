"use client";
/**
 * Members settings page (§11.11 / §11.1).
 *
 * Shows all household members with their roles and calendar coverage.
 * Primary admin can invite new members and change roles.
 *
 * Calendar Coverage indicator: "1 of 2 parents connected" (AC 11.1.2).
 * Never implies coverage that can't actually be provided.
 *
 * Dependent members are shown as records but never invited (AC 3.1).
 */
import { useEffect, useState } from "react";
import { api, Member } from "@/lib/api";

const ROLE_LABELS: Record<string, string> = {
  primary_admin: "Primary Admin",
  co_parent: "Co-Parent (read/write)",
  secondary_readonly: "View Only",
  dependent_minor: "Child (dependent)",
  dependent_care_recipient: "Care Recipient (dependent)",
};

const STATUS_LABELS: Record<string, { label: string; color: string }> = {
  active: { label: "Active", color: "text-green-600" },
  invited: { label: "Invited", color: "text-yellow-600" },
  pending_verification: { label: "Verifying", color: "text-blue-600" },
  suspended: { label: "Suspended", color: "text-red-600" },
  removed: { label: "Removed", color: "text-gray-400" },
};

export default function MembersPage() {
  const [members, setMembers] = useState<Member[]>([]);
  const [currentMember, setCurrentMember] = useState<Member | null>(null);
  const [loading, setLoading] = useState(true);
  const [showInvite, setShowInvite] = useState(false);
  const [inviteForm, setInviteForm] = useState({ display_name: "", intended_role: "co_parent", phone_e164: "", email: "" });
  const [inviting, setInviting] = useState(false);
  const [inviteSuccess, setInviteSuccess] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([api.identity.me(), fetchMembers()])
      .then(([me, mems]) => {
        setCurrentMember(me);
        setMembers(mems);
      })
      .finally(() => setLoading(false));
  }, []);

  const fetchMembers = async () => {
    const me = await api.identity.me();
    return api.identity.listMembers(me.household_id);
  };

  const handleInvite = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setInviting(true);
    try {
      const body: any = {
        display_name: inviteForm.display_name,
        intended_role: inviteForm.intended_role,
      };
      if (inviteForm.phone_e164) body.phone_e164 = inviteForm.phone_e164;
      if (inviteForm.email) body.email = inviteForm.email;

      await api.identity.invite(body);
      setInviteSuccess(`Invite sent to ${inviteForm.phone_e164 || inviteForm.email}. It expires in 7 days.`);
      setShowInvite(false);
      setInviteForm({ display_name: "", intended_role: "co_parent", phone_e164: "", email: "" });
    } catch (e: any) {
      setError(e.message);
    } finally {
      setInviting(false);
    }
  };

  const adults = members.filter(m => ["primary_admin", "co_parent", "secondary_readonly"].includes(m.role));
  const connectedCalendars = 0; // TODO: fetch from connectors API
  const adultCount = adults.filter(m => m.status === "active").length;

  if (loading) return <p className="p-6 text-gray-500">Loading...</p>;
  const isPrimary = currentMember?.role === "primary_admin";

  return (
    <div className="max-w-xl mx-auto p-6 pt-10 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold text-gray-900">Family Members</h1>
        {isPrimary && (
          <button
            onClick={() => setShowInvite(true)}
            className="bg-gray-900 text-white text-sm px-4 py-2 rounded-lg hover:bg-gray-700"
          >
            + Invite
          </button>
        )}
      </div>

      {inviteSuccess && (
        <div className="bg-green-50 border border-green-200 rounded-lg p-3 text-sm text-green-800">
          {inviteSuccess}
        </div>
      )}

      {/* Calendar Coverage indicator (AC 11.1.2) */}
      <div className="bg-blue-50 border border-blue-200 rounded-lg p-3">
        <p className="text-sm text-blue-800">
          <strong>Calendar Coverage:</strong> {connectedCalendars} of {adultCount} adults connected.{" "}
          {connectedCalendars < adultCount && (
            <span>Conflict detection only checks connected calendars.</span>
          )}
        </p>
      </div>

      {/* Member list */}
      <div className="border border-gray-200 rounded-lg bg-white divide-y divide-gray-100">
        {members.filter(m => m.status !== "removed").map(m => {
          const statusCfg = STATUS_LABELS[m.status] || { label: m.status, color: "text-gray-500" };
          const isDependent = ["dependent_minor", "dependent_care_recipient"].includes(m.role);
          return (
            <div key={m.id} className="flex items-center justify-between p-4">
              <div>
                <div className="flex items-center gap-2">
                  <p className="text-sm font-medium text-gray-900">{m.display_name}</p>
                  {m.id === currentMember?.id && (
                    <span className="text-xs bg-gray-100 text-gray-500 px-2 py-0.5 rounded-full">You</span>
                  )}
                </div>
                <p className="text-xs text-gray-500 mt-0.5">{ROLE_LABELS[m.role] || m.role}</p>
                {isDependent && (
                  <p className="text-xs text-gray-400">Represented as a dependent — not directly interactive</p>
                )}
              </div>
              <span className={`text-xs font-medium ${statusCfg.color}`}>{statusCfg.label}</span>
            </div>
          );
        })}
      </div>

      {/* Invite form */}
      {showInvite && (
        <div className="border border-gray-200 rounded-lg bg-white p-4 space-y-4">
          <h2 className="text-sm font-semibold text-gray-900">Invite a family member</h2>
          <form onSubmit={handleInvite} className="space-y-3">
            <Field label="Name">
              <input
                value={inviteForm.display_name}
                onChange={e => setInviteForm(f => ({ ...f, display_name: e.target.value }))}
                placeholder="Mark"
                className={inputClass}
                required
              />
            </Field>
            <Field label="Role">
              <select
                value={inviteForm.intended_role}
                onChange={e => setInviteForm(f => ({ ...f, intended_role: e.target.value }))}
                className={inputClass}
              >
                <option value="co_parent">Co-Parent (read/write)</option>
                <option value="secondary_readonly">View Only</option>
              </select>
            </Field>
            <Field label="Phone (or Email — at least one required)">
              <input
                value={inviteForm.phone_e164}
                onChange={e => setInviteForm(f => ({ ...f, phone_e164: e.target.value }))}
                placeholder="+1 555 000 0000"
                className={inputClass}
              />
            </Field>
            <Field label="Email">
              <input
                type="email"
                value={inviteForm.email}
                onChange={e => setInviteForm(f => ({ ...f, email: e.target.value }))}
                placeholder="mark@email.com"
                className={inputClass}
              />
            </Field>
            {error && <p className="text-sm text-red-600">{error}</p>}
            <div className="flex gap-2">
              <button
                type="submit"
                disabled={inviting}
                className="bg-gray-900 text-white text-sm px-4 py-2 rounded hover:bg-gray-700 disabled:opacity-50"
              >
                {inviting ? "Sending..." : "Send invite"}
              </button>
              <button
                type="button"
                onClick={() => { setShowInvite(false); setError(""); }}
                className="text-sm text-gray-500 hover:text-gray-700"
              >
                Cancel
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}

const inputClass = "w-full border border-gray-300 rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-gray-900";

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-xs font-medium text-gray-600 mb-1">{label}</label>
      {children}
    </div>
  );
}
