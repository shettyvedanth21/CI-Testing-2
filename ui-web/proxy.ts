import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

function isHtmlRequest(request: NextRequest): boolean {
  const accept = request.headers.get("accept") ?? "";
  return accept.includes("text/html");
}

export function proxy(request: NextRequest) {
  const response = NextResponse.next();

  // Prevent stale app-shell/document responses from shared caches during rollout validation.
  if (isHtmlRequest(request)) {
    response.headers.set("Cache-Control", "no-store");
    response.headers.set("Pragma", "no-cache");
    response.headers.set("Expires", "0");
  }

  return response;
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
