"use client";
/**
 * Main navigation — tabs across all surfaces.
 * Settings submenu for the nested settings pages.
 */
import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV_ITEMS = [
  { href: "/briefing", label: "Briefing", icon: "📋" },
  { href: "/assistant", label: "Assistant", icon: "✦" },
  { href: "/calendar", label: "Calendar", icon: "📅" },
  { href: "/tasks", label: "Tasks", icon: "✅" },
  { href: "/settings/connected-services", label: "Settings", icon: "⚙️", settingsRoot: true },
];

const SETTINGS_ITEMS = [
  { href: "/settings/connected-services", label: "Connected Services" },
  { href: "/settings/members", label: "Family Members" },
  { href: "/settings/preferences", label: "Notifications" },
  { href: "/settings/security", label: "Security" },
];

export function Nav() {
  const pathname = usePathname();
  const isSettings = pathname.startsWith("/settings");

  return (
    <>
      {/* Top nav bar */}
      <nav className="fixed top-0 inset-x-0 z-10 bg-white border-b border-gray-200 h-12">
        <div className="max-w-5xl mx-auto px-4 h-full flex items-center justify-between">
          <span className="text-sm font-semibold text-gray-900">KinValet</span>
          <div className="flex items-center gap-1">
            {NAV_ITEMS.map(item => {
              const active = item.settingsRoot ? isSettings : pathname === item.href;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm transition-colors ${
                    active
                      ? "bg-gray-900 text-white"
                      : "text-gray-600 hover:bg-gray-100"
                  }`}
                >
                  <span>{item.icon}</span>
                  <span className="hidden sm:inline">{item.label}</span>
                </Link>
              );
            })}
          </div>
        </div>
      </nav>

      {/* Settings sidebar — shown only within /settings/* */}
      {isSettings && (
        <aside className="fixed left-0 top-12 bottom-0 w-48 bg-gray-50 border-r border-gray-200 p-4 space-y-1">
          {SETTINGS_ITEMS.map(item => (
            <Link
              key={item.href}
              href={item.href}
              className={`block text-sm px-3 py-2 rounded-lg transition-colors ${
                pathname === item.href
                  ? "bg-white text-gray-900 font-medium shadow-sm"
                  : "text-gray-600 hover:bg-white hover:text-gray-900"
              }`}
            >
              {item.label}
            </Link>
          ))}
        </aside>
      )}
    </>
  );
}
