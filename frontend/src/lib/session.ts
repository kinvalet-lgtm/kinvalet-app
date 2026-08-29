/**
 * Session helpers — MVP local session using localStorage.
 * Production: replace with Supabase JWT; the API calls are identical.
 */

export function getSession() {
  if (typeof window === "undefined") return null;
  const householdId = localStorage.getItem("sc_household_id");
  const memberId = localStorage.getItem("sc_member_id");
  if (!householdId || !memberId) return null;
  return { householdId, memberId };
}

export function clearSession() {
  localStorage.removeItem("sc_household_id");
  localStorage.removeItem("sc_member_id");
  localStorage.removeItem("sc_display_name");
  localStorage.removeItem("sc_household_name");
}

export function getDisplayName(): string {
  return localStorage.getItem("sc_display_name") || "You";
}

export function getHouseholdName(): string {
  return localStorage.getItem("sc_household_name") || "Your Household";
}
