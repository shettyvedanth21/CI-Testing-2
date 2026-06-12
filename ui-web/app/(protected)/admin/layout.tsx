"use client";

import Link from "next/link";
import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useAuth } from "@/lib/authContext";
import { cn } from "@/lib/utils";

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const { me, isLoading } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (!isLoading && me?.user.role !== "super_admin") {
      router.replace("/machines");
    }
  }, [me, isLoading, router]);

  if (isLoading) return null;
  if (me?.user.role !== "super_admin") return null;

  const isOrgsTabActive = pathname === "/admin" || pathname.startsWith("/admin/tenants");
  const isMaintenanceTabActive = pathname.startsWith("/admin/platform-maintenance");

  return (
    <div className="space-y-5">
      <div className="surface-panel overflow-hidden">
        <div className="flex items-center gap-2 border-b border-[var(--border-subtle)] px-4 py-3 sm:px-5">
          <Link
            href="/admin/tenants"
            className={cn(
              "rounded-xl px-3 py-2 text-sm font-medium transition-colors",
              isOrgsTabActive
                ? "bg-[var(--tone-info-bg)] text-[var(--tone-info-text)]"
                : "text-[var(--text-secondary)] hover:bg-[var(--surface-1)] hover:text-[var(--text-primary)]",
            )}
          >
            Organisations
          </Link>
          <Link
            href="/admin/platform-maintenance"
            className={cn(
              "rounded-xl px-3 py-2 text-sm font-medium transition-colors",
              isMaintenanceTabActive
                ? "bg-[var(--tone-info-bg)] text-[var(--tone-info-text)]"
                : "text-[var(--text-secondary)] hover:bg-[var(--surface-1)] hover:text-[var(--text-primary)]",
            )}
          >
            Platform Maintenance
          </Link>
        </div>
      </div>
      {children}
    </div>
  );
}
