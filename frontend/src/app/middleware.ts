import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

const PUBLIC_PATHS = ["/login", "/onboarding"];

export async function middleware(req: NextRequest) {
  const pathname = req.nextUrl.pathname;

  // Public paths never need auth
  if (PUBLIC_PATHS.some(p => pathname.startsWith(p))) {
    return NextResponse.next();
  }

  // Static assets
  if (pathname.startsWith("/_next") || pathname.includes(".")) {
    return NextResponse.next();
  }

  // Check for stored session (MVP: localStorage-based, checked via cookie fallback)
  // In production with Supabase this would verify the JWT
  const hasSession =
    req.cookies.get("sc_household_id")?.value ||
    req.headers.get("x-household-id");

  // In MVP local dev, allow access to the dashboard directly
  // The individual API calls will fail with 401 if not authenticated
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
