/**
 * API client — typed wrapper around the KinValet backend.
 *
 * Polls every 30 seconds when tab is focused (no WebSockets in MVP).
 * Auth: Supabase JWT sent as Bearer token on every request.
 */

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function request<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  // Auth: use Supabase JWT if configured, otherwise fall back to localStorage household ID.
  // Supabase is optional — the backend also accepts x-household-id for MVP local sessions.
  let token = "";
  let householdId = "";

  if (typeof window !== "undefined") {
    const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL;
    const supabaseKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

    if (supabaseUrl && supabaseKey) {
      // Supabase configured — use JWT
      const { createClient } = await import("@supabase/supabase-js");
      const supabase = createClient(supabaseUrl, supabaseKey);
      const { data: { session } } = await supabase.auth.getSession();
      token = session?.access_token ?? "";
    }

    // Always include household + member ID for backend routing (MVP session)
    householdId = localStorage.getItem("sc_household_id") ?? "";
  }

  const memberId = typeof window !== "undefined"
    ? localStorage.getItem("sc_member_id") ?? ""
    : "";

  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { "Authorization": `Bearer ${token}` } : {}),
      ...(householdId ? { "x-household-id": householdId } : {} as Record<string, string>),
      ...(memberId ? { "x-member-id": memberId } : {} as Record<string, string>),
      ...options.headers,
    },
  });

  if (!res.ok) {
    const error = await res.json().catch(() => ({ message: res.statusText }));
    throw new Error(error.detail || error.message || "API error");
  }

  return res.json();
}

// ── Identity ──────────────────────────────────────────────────────────────────

export const api = {
  identity: {
    me: () => request<Member>("/api/v1/identity/me"),
    listMembers: (householdId: string) =>
      request<Member[]>(`/api/v1/identity/household/${householdId}/members`),
    invite: (body: { display_name: string; intended_role: string; phone_e164?: string; email?: string }) =>
      request("/api/v1/identity/invite", { method: "POST", body: JSON.stringify(body) }),
  },

  operations: {
    listItems: (archived = false) =>
      request<OperationalItem[]>(`/api/v1/operations/items?archived=${archived}`),
    getItem: (id: string) => request<OperationalItem>(`/api/v1/operations/items/${id}`),
    createItem: (body: Partial<OperationalItem>) =>
      request<OperationalItem>("/api/v1/operations/items", { method: "POST", body: JSON.stringify(body) }),
    confirmItem: (id: string, confirmed: boolean) =>
      request(`/api/v1/operations/items/${id}/confirm`, {
        method: "POST",
        body: JSON.stringify({ confirmed, channel: "dashboard" }),
      }),
    delegateItem: (id: string, toMemberId: string) =>
      request(`/api/v1/operations/items/${id}/delegate`, {
        method: "POST",
        body: JSON.stringify({ delegated_to_member_id: toMemberId }),
      }),
    completeItem: (id: string) =>
      request(`/api/v1/operations/items/${id}/complete`, { method: "POST" }),
    archiveItem: (id: string) =>
      request(`/api/v1/operations/items/${id}/archive`, { method: "POST" }),
    myTasks: (memberId: string) =>
      request<MyTasksResponse>(`/api/v1/operations/items/my-tasks/${memberId}`),
  },

  briefing: {
    latest: () => request<Briefing>("/api/v1/briefing/latest"),
  },

  connectors: {
    available: () => request<ConnectorCard[]>("/api/v1/connectors/available"),
    connect: (connectorTypeId: string) =>
      request<{ auth_url: string }>(`/api/v1/connectors/${connectorTypeId}/connect`, { method: "POST" }),
    disconnect: (connectorTypeId: string) =>
      request(`/api/v1/connectors/${connectorTypeId}/disconnect`, { method: "DELETE" }),
  },

  notification: {
    getPreferences: () => request<NotificationPreference[]>("/api/v1/notification/preferences"),
    updatePreference: (body: Partial<NotificationPreference>) =>
      request("/api/v1/notification/preferences", { method: "PUT", body: JSON.stringify(body) }),
  },

  financial: {
    listAlerts: () => request<FinancialAlert[]>("/api/v1/financial/alerts"),
    resolveAlert: (id: string, decision: "confirmed_issue" | "false_positive") =>
      request(`/api/v1/financial/alerts/${id}/resolve`, {
        method: "POST",
        body: JSON.stringify({ decision }),
      }),
  },
};

// ── Types ─────────────────────────────────────────────────────────────────────

export interface Member {
  id: string;
  household_id: string;
  display_name: string;
  role: string;
  status: string;
}

export interface OperationalItem {
  id: string;
  household_id: string;
  category: string;
  title: string;
  status: string;
  priority: string;
  assigned_to_member_id?: string;
  start_at?: string;
  location?: string;
  cost_cents?: number;
  requires_approval: boolean;
  calendar_sync_status: string;
  is_archived: boolean;
}

export interface MyTasksResponse {
  awaiting_response: string[];
  assigned_to_you: OperationalItem[];
}

export interface Briefing {
  id: string;
  household_id: string;
  briefing_date: string;
  content_json: {
    today_items: Array<{ id: string; title: string; start_at?: string; location?: string; priority: string }>;
    pending_approvals: Array<{ id: string; item_id: string; amount_cents?: number }>;
    unresolved_conflicts: Array<{ id: string; item_id: string }>;
    pending_delegations: Array<{ id: string; item_id: string; to_member_id: string }>;
    is_quiet_day: boolean;
  };
}

export interface ConnectorCard {
  id: string;
  display_name: string;
  category: string;
  auth_method: string;
  copy_what_we_read: string;
  copy_what_we_write: string;
  copy_used_for: string;
  connected: boolean;
  status: string;
  external_account_ref?: string;
  last_synced_at?: string;
}

export interface NotificationPreference {
  category: string;
  enabled: boolean;
  delivery_mode: string;
  lead_time_minutes?: number;
  quiet_hours_start: string;
  quiet_hours_end: string;
  daily_notification_ceiling: number;
  whatsapp_opted_out: boolean;
}

export interface FinancialAlert {
  id: string;
  alert_type: string;
  status: string;
  transaction: Record<string, unknown>;
  surfaced_at: string;
}
