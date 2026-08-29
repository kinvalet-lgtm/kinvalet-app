/**
 * Root page — redirect based on registration state.
 * New users go to /onboarding; returning users go to /briefing.
 */
"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";

export default function Home() {
  const router = useRouter();
  useEffect(() => {
    const householdId = localStorage.getItem("sc_household_id");
    router.replace(householdId ? "/briefing" : "/onboarding");
  }, [router]);
  return (
    <div className="min-h-screen flex items-center justify-center">
      <div className="text-gray-400 text-sm">Loading…</div>
    </div>
  );
}
