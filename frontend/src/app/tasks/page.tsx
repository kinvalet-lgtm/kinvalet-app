"use client";
/**
 * Tasks page — My Tasks + Family Tasks board (§11.12).
 *
 * Two tabs:
 * - My Tasks: items assigned to me + delegations awaiting my response
 * - Family Tasks: household-wide kanban view
 *
 * WhatsApp parity: "show my tasks" / "what's on my plate" returns the same
 * data as this screen (one query, two presentations — AC 11.12.2).
 */
import { useEffect, useState } from "react";
import { api, OperationalItem } from "@/lib/api";
import { format, parseISO } from "date-fns";

const STATUS_COLUMNS = [
  { key: "pending_confirmation", label: "To Confirm" },
  { key: "pending_approval", label: "Needs Approval" },
  { key: "confirmed", label: "Confirmed" },
  { key: "completed", label: "Done" },
];

export default function TasksPage() {
  const [items, setItems] = useState<OperationalItem[]>([]);
  const [tab, setTab] = useState<"mine" | "family">("mine");
  const [loading, setLoading] = useState(true);

  const fetchItems = async () => {
    try {
      const data = await api.operations.listItems();
      setItems(data);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchItems();
    const interval = setInterval(() => {
      if (!document.hidden) fetchItems();
    }, 30_000);
    return () => clearInterval(interval);
  }, []);

  const handleComplete = async (id: string) => {
    await api.operations.completeItem(id);
    fetchItems();
  };

  const handleArchive = async (id: string) => {
    await api.operations.archiveItem(id);
    fetchItems();
  };

  if (loading) return <LoadingState />;

  return (
    <div className="max-w-4xl mx-auto p-6 pt-10">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-semibold text-gray-900">Tasks</h1>
        <div className="flex gap-2">
          <TabButton active={tab === "mine"} onClick={() => setTab("mine")}>My Tasks</TabButton>
          <TabButton active={tab === "family"} onClick={() => setTab("family")}>Family Board</TabButton>
        </div>
      </div>

      {tab === "mine" ? (
        <MyTasksView items={items} onComplete={handleComplete} />
      ) : (
        <FamilyBoardView items={items} onComplete={handleComplete} onArchive={handleArchive} />
      )}
    </div>
  );
}

function MyTasksView({ items, onComplete }: {
  items: OperationalItem[];
  onComplete: (id: string) => void;
}) {
  const assigned = items.filter(i => i.status === "confirmed" && !i.is_archived);
  const pending = items.filter(i => ["pending_confirmation", "pending_approval"].includes(i.status));

  return (
    <div className="space-y-4">
      {pending.length > 0 && (
        <Section title="Awaiting your response">
          {pending.map(item => <TaskCard key={item.id} item={item} onComplete={onComplete} showActions />)}
        </Section>
      )}
      <Section title="Assigned to you">
        {assigned.length === 0 ? (
          <p className="text-sm text-gray-500 py-2">Nothing on your plate right now.</p>
        ) : (
          assigned.map(item => <TaskCard key={item.id} item={item} onComplete={onComplete} showActions />)
        )}
      </Section>
    </div>
  );
}

function FamilyBoardView({ items, onComplete, onArchive }: {
  items: OperationalItem[];
  onComplete: (id: string) => void;
  onArchive: (id: string) => void;
}) {
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
      {STATUS_COLUMNS.map(col => (
        <div key={col.key} className="bg-gray-50 rounded-lg p-3">
          <h3 className="text-xs font-semibold text-gray-500 uppercase mb-3">{col.label}</h3>
          <div className="space-y-2">
            {items.filter(i => i.status === col.key && !i.is_archived).map(item => (
              <KanbanCard key={item.id} item={item} onComplete={onComplete} />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function TaskCard({ item, onComplete, showActions }: {
  item: OperationalItem;
  onComplete: (id: string) => void;
  showActions?: boolean;
}) {
  const time = item.start_at ? format(parseISO(item.start_at), "h:mm a, MMM d") : null;

  return (
    <div className={`border rounded-lg p-3 ${item.priority === "critical" ? "border-red-200 bg-red-50" : "border-gray-200 bg-white"}`}>
      <div className="flex items-start justify-between gap-2">
        <div>
          {item.priority === "critical" && (
            <span className="text-xs text-red-600 font-semibold mr-1">URGENT</span>
          )}
          <p className="text-sm font-medium text-gray-900">{item.title}</p>
          {time && <p className="text-xs text-gray-500 mt-0.5">{time}</p>}
          {item.location && <p className="text-xs text-gray-500">{item.location}</p>}
          {item.cost_cents && (
            <p className="text-xs text-gray-600 mt-1">${(item.cost_cents / 100).toFixed(2)}</p>
          )}
        </div>
        {showActions && item.status === "confirmed" && (
          <button
            onClick={() => onComplete(item.id)}
            className="text-xs bg-green-100 text-green-700 px-2 py-1 rounded hover:bg-green-200 whitespace-nowrap"
          >
            ✓ Done
          </button>
        )}
      </div>
      <div className="mt-2">
        <StatusBadge status={item.status} />
      </div>
    </div>
  );
}

function KanbanCard({ item, onComplete }: {
  item: OperationalItem;
  onComplete: (id: string) => void;
}) {
  return (
    <div className="bg-white border border-gray-200 rounded p-2 text-xs">
      <p className="font-medium text-gray-800 truncate">{item.title}</p>
      {item.start_at && (
        <p className="text-gray-500 mt-1">{format(parseISO(item.start_at), "MMM d h:mm a")}</p>
      )}
      {item.status === "confirmed" && (
        <button
          onClick={() => onComplete(item.id)}
          className="mt-1 text-green-600 hover:text-green-700"
        >
          ✓ Mark done
        </button>
      )}
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const config: Record<string, { label: string; class: string }> = {
    pending_confirmation: { label: "To confirm", class: "bg-yellow-100 text-yellow-700" },
    pending_approval: { label: "Needs approval", class: "bg-orange-100 text-orange-700" },
    confirmed: { label: "Confirmed", class: "bg-blue-100 text-blue-700" },
    completed: { label: "Done", class: "bg-green-100 text-green-700" },
    declined: { label: "Declined", class: "bg-red-100 text-red-700" },
    expired: { label: "Missed", class: "bg-gray-100 text-gray-600" },
  };
  const cfg = config[status] || { label: status, class: "bg-gray-100 text-gray-600" };
  return (
    <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${cfg.class}`}>
      {cfg.label}
    </span>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="border border-gray-200 rounded-lg p-4 bg-white">
      <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">{title}</h2>
      <div className="space-y-2">{children}</div>
    </div>
  );
}

function TabButton({ active, onClick, children }: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={`px-4 py-1.5 text-sm rounded-md font-medium transition-colors ${
        active
          ? "bg-gray-900 text-white"
          : "bg-gray-100 text-gray-600 hover:bg-gray-200"
      }`}
    >
      {children}
    </button>
  );
}

function LoadingState() {
  return (
    <div className="flex items-center justify-center min-h-screen">
      <div className="text-gray-500">Loading tasks...</div>
    </div>
  );
}
