"use client";

import { usePermissions } from "@/hooks/usePermissions";

export function ReadOnlyBanner() {
  const { isReadOnly } = usePermissions();

  if (!isReadOnly) {
    return null;
  }

  return (
    <div className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900 shadow-sm">
      You have read-only access. Contact your admin to request changes.
    </div>
  );
}
