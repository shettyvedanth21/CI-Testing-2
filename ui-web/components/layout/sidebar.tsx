"use client";

import Image from "next/image";
import Link from "next/link";
import type { ReactElement } from "react";
import { usePathname } from "next/navigation";
import { useAuth } from "@/lib/authContext";
import { SuperAdminOrgSwitcher } from "@/components/SuperAdminOrgGate";
import { cn } from "@/lib/utils";
import { type FeatureKey, hasFeature } from "@/lib/features";

type NavItem = {
  label: string;
  href: string;
  icon: (props: { className?: string }) => ReactElement;
  feature?: FeatureKey;
};

const sidebarItems: NavItem[] = [
  { label: "Machines", href: "/machines", icon: MachineIcon, feature: "machines" },
  { label: "Calendar", href: "/calendar", icon: CalendarIcon, feature: "calendar" },
  { label: "Analytics", href: "/analytics", icon: ChartIcon, feature: "analytics" },
  { label: "Reports", href: "/reports", icon: DocumentIcon, feature: "reports" },
  { label: "Waste Analysis", href: "/waste-analysis", icon: FlameIcon, feature: "waste_analysis" },
  { label: "Factory Copilot", href: "/copilot", icon: SparklesIcon, feature: "copilot" },
  { label: "Rules", href: "/rules", icon: ShieldIcon, feature: "rules" },
  { label: "Settings", href: "/settings", icon: SettingsIcon, feature: "settings" },
];

export function Sidebar() {
  const pathname = usePathname();
  const { me } = useAuth();
  const navItems = [
    ...sidebarItems.filter((item) => !item.feature || hasFeature(me, item.feature)),
    ...((me?.user.role === "org_admin" || me?.user.role === "super_admin" || me?.user.role === "plant_manager")
      ? [
          { label: "Team", href: "/tenant/users", icon: ShieldIcon },
        ]
      : []),
    ...((me?.user.role === "org_admin" || me?.user.role === "super_admin")
      ? [
          { label: "Plants", href: "/tenant/plants", icon: MachineIcon },
        ]
      : []),
    ...(me?.user.role === "super_admin"
      ? [
          { label: "Admin", href: "/admin/tenants", icon: ShieldIcon },
        ]
      : []),
  ];

  return (
    <aside
      className={cn(
        "fixed left-0 top-0 z-20 hidden h-screen w-72 flex-col border-r border-[var(--border-subtle)] bg-[var(--surface-0)] text-[var(--text-primary)] lg:flex"
      )}
    >
      <div className="border-b border-[var(--border-subtle)] px-5 py-5">
        <Link href="/" className="block">
          <Image
            src="/shivex-logo.png"
            alt="Shivex"
            width={320}
            height={92}
            className="h-auto w-full max-w-[190px]"
            priority
          />
        </Link>
      </div>

      <nav className="flex-1 space-y-1 p-3">
        {navItems.map((item) => {
          const isActive = pathname === item.href || pathname.startsWith(`${item.href}/`);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "group flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium transition-all",
                isActive
                  ? "bg-[var(--tone-info-bg)] text-[var(--tone-info-text)] ring-1 ring-[var(--tone-info-border)]"
                  : "text-[var(--text-secondary)] hover:bg-[var(--surface-2)] hover:text-[var(--text-primary)]"
              )}
            >
              <item.icon
                className={cn(
                  "h-5 w-5",
                  isActive ? "text-[var(--tone-info-solid)]" : "text-[var(--text-tertiary)] group-hover:text-[var(--text-secondary)]"
                )}
              />
              <span className="font-medium">{item.label}</span>
            </Link>
          );
        })}
      </nav>

      <div className="border-t border-[var(--border-subtle)] px-4 py-4">
        <SuperAdminOrgSwitcher />
        <div className="text-xs text-[var(--text-tertiary)]">Shivex Platform</div>
        <div className="text-xs text-[var(--text-tertiary)]">v1.0.0</div>
      </div>
    </aside>
  );
}

export function MachineIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={2}
        d="M9 3v2m6-2v2M9 19v2m6-2v2M5 9H3m2 6H3m18-6h-2m2 6h-2M7 19h10a2 2 0 002-2V7a2 2 0 00-2-2H7a2 2 0 00-2 2v10a2 2 0 002 2zM9 9h6v6H9V9z"
      />
    </svg>
  );
}

export function ChartIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      fill="none"
      stroke="currentColor"
      viewBox="0 0 24 24"
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={2}
        d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"
      />
    </svg>
  );
}

export function DocumentIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      fill="none"
      stroke="currentColor"
      viewBox="0 0 24 24"
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={2}
        d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
      />
    </svg>
  );
}

export function ShieldIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      fill="none"
      stroke="currentColor"
      viewBox="0 0 24 24"
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={2}
        d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"
      />
    </svg>
  );
}

export function SettingsIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      fill="none"
      stroke="currentColor"
      viewBox="0 0 24 24"
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={2}
        d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"
      />
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={2}
        d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"
      />
    </svg>
  );
}

export function FlameIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={2}
        d="M12 3c.6 2.5-.4 4.2-2 5.7-1.5 1.5-2.5 3-2.5 5.1A4.5 4.5 0 0012 18a4.5 4.5 0 004.5-4.2c0-2.8-1.8-4.2-3.4-5.6C12 7.4 11.6 5.8 12 3z"
      />
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 22a7 7 0 007-7" />
    </svg>
  );
}

export function SparklesIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={2}
        d="M12 3l1.8 3.9L18 8.7l-3.2 2.9.9 4.3L12 13.9l-3.7 2 .9-4.3L6 8.7l4.2-1.8L12 3z"
      />
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 17l.8 1.7L21.5 19l-1.7.7L19 21.5l-.8-1.8L16.5 19l1.7-.3L19 17z" />
    </svg>
  );
}

export function CalendarIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={2}
        d="M8 2v4m8-4v4M3 10h18M5 5h14a2 2 0 012 2v12a2 2 0 01-2 2H5a2 2 0 01-2-2V7a2 2 0 012-2z"
      />
    </svg>
  );
}
