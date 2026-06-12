"use client";

import Link from "next/link";
import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useAuth } from "@/lib/authContext";
import { cn } from "@/lib/utils";

export default function OrgLayout({ children }: { children: React.ReactNode }) {
  const { me, isLoading } = useAuth();
  const pathname = usePathname();
  const router = useRouter();
  const role = me?.user.role ?? null;
  const canViewTeam = role === "org_admin" || role === "super_admin" || role === "plant_manager";
  const canViewPlants = role === "org_admin" || role === "super_admin";

  useEffect(() => {
    if (!isLoading && me && !canViewTeam) {
      router.replace("/machines");
    }
  }, [canViewTeam, isLoading, me, router]);

  if (isLoading) return null;
  if (!me || !canViewTeam) return null;

  return (
    <div className="space-y-5">
      <div className="surface-panel overflow-hidden">
        <div className="flex items-center gap-2 border-b border-[var(--border-subtle)] px-4 py-3 sm:px-5">
          <Link
            href="/tenant/users"
            className={cn(
              "rounded-xl px-3 py-2 text-sm font-medium transition-colors",
              pathname.startsWith("/tenant/users")
                ? "bg-[var(--tone-info-bg)] text-[var(--tone-info-text)]"
                : "text-[var(--text-secondary)] hover:bg-[var(--surface-1)] hover:text-[var(--text-primary)]",
            )}
          >
            Users
          </Link>
          {canViewPlants ? (
            <Link
              href="/tenant/plants"
              className={cn(
                "rounded-xl px-3 py-2 text-sm font-medium transition-colors",
                pathname.startsWith("/tenant/plants")
                  ? "bg-[var(--tone-info-bg)] text-[var(--tone-info-text)]"
                  : "text-[var(--text-secondary)] hover:bg-[var(--surface-1)] hover:text-[var(--text-primary)]",
              )}
            >
              Plants
            </Link>
          ) : null}
        </div>
      </div>
      {children}
    </div>
  );
}
