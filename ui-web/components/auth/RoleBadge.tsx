"use client";

import { cn } from "@/lib/utils";
import type { UserRole } from "@/lib/authApi";

interface RoleBadgeProps {
  role: UserRole;
  size?: "sm" | "md";
}

const ROLE_LABELS: Record<UserRole, string> = {
  super_admin: "Super admin",
  org_admin: "Org admin",
  plant_manager: "Plant manager",
  operator: "Operator",
  viewer: "Viewer",
};

const ROLE_STYLES: Record<UserRole, string> = {
  super_admin: "border-red-200 bg-red-50 text-red-700",
  org_admin: "border-violet-200 bg-violet-50 text-violet-700",
  plant_manager: "border-teal-200 bg-teal-50 text-teal-700",
  operator: "border-blue-200 bg-blue-50 text-blue-700",
  viewer: "border-slate-200 bg-slate-100 text-slate-700",
};

const SIZE_STYLES = {
  sm: "px-2 py-0.5 text-[11px]",
  md: "px-2.5 py-1 text-xs",
};

export function RoleBadge({ role, size = "md" }: RoleBadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border font-medium",
        ROLE_STYLES[role],
        SIZE_STYLES[size],
      )}
    >
      {ROLE_LABELS[role]}
    </span>
  );
}
