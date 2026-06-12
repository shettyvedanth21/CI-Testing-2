"use client";

import Link from "next/link";
import type { ReactElement } from "react";
import { useMemo, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/authContext";
import {
  CalendarIcon,
  ChartIcon,
  DocumentIcon,
  MachineIcon,
  SettingsIcon,
  Sidebar,
  SparklesIcon,
} from "@/components/layout/sidebar";
import { PlatformMaintenanceBanner } from "@/components/layout/PlatformMaintenanceBanner";
import { hasFeature, type FeatureKey } from "@/lib/features";

const PAGE_TITLES: Record<string, { title: string; subtitle: string }> = {
  "/": { title: "Shivex Overview", subtitle: "Operational intelligence at a glance" },
  "/admin": { title: "Admin", subtitle: "Organisation onboarding, plant setup, and admin access control" },
  "/admin/platform-maintenance": {
    title: "Platform Maintenance",
    subtitle: "Plan and schedule maintenance notices for selected organisations",
  },
  "/org": { title: "Org Management", subtitle: "Users, plants, and plant-scoped access for your organisation" },
  "/machines": { title: "Machines", subtitle: "Fleet health, runtime, and downtime insights" },
  "/calendar": { title: "Calendar", subtitle: "Energy and loss trends by day" },
  "/analytics": { title: "Analytics", subtitle: "Model-driven diagnosis and predictions" },
  "/reports": { title: "Reports", subtitle: "Scheduled and on-demand exports" },
  "/rules": { title: "Rules", subtitle: "Alerting logic and response controls" },
  "/settings": { title: "Settings", subtitle: "Tenant-wide operational configuration" },
  "/waste-analysis": { title: "Waste Analysis", subtitle: "Actionable waste and loss opportunities" },
  "/copilot": { title: "Factory Copilot", subtitle: "Conversational operations assistant" },
};

function resolvePageMeta(pathname: string): { title: string; subtitle: string } {
  const direct = PAGE_TITLES[pathname];
  if (direct) return direct;
  const match = Object.keys(PAGE_TITLES)
    .filter((key) => key !== "/" && pathname.startsWith(key))
    .sort((a, b) => b.length - a.length)[0];
  if (match) return PAGE_TITLES[match];
  return { title: "Shivex", subtitle: "Industrial operations platform" };
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { me, logout } = useAuth();
  const pageMeta = useMemo(() => resolvePageMeta(pathname), [pathname]);
  const [mobileMoreOpen, setMobileMoreOpen] = useState(false);

  async function handleLogout(): Promise<void> {
    await logout();
    router.push("/login");
  }

  const mobilePrimaryItems: Array<{
    href: string;
    label: string;
    icon: (props: { className?: string }) => ReactElement;
    feature?: FeatureKey;
  }> = [
    { href: "/machines", label: "Machines", icon: MachineIcon, feature: "machines" },
    { href: "/calendar", label: "Calendar", icon: CalendarIcon, feature: "calendar" },
    { href: "/analytics", label: "Analytics", icon: ChartIcon, feature: "analytics" },
    { href: "/reports", label: "Reports", icon: DocumentIcon, feature: "reports" },
    { href: "/copilot", label: "Copilot", icon: SparklesIcon, feature: "copilot" },
  ];

  const mobileMoreItems = [
    ...(hasFeature(me, "rules") ? [{ href: "/rules", label: "Rules" }] : []),
    ...(hasFeature(me, "waste_analysis") ? [{ href: "/waste-analysis", label: "Waste Analysis" }] : []),
    ...(hasFeature(me, "settings") ? [{ href: "/settings", label: "Settings" }] : []),
    ...((me?.user.role === "org_admin" || me?.user.role === "super_admin" || me?.user.role === "plant_manager")
      ? [
          { href: "/tenant/users", label: "Team" },
        ]
      : []),
    ...((me?.user.role === "org_admin" || me?.user.role === "super_admin")
      ? [
          { href: "/tenant/plants", label: "Plants" },
        ]
      : []),
    ...(me?.user.role === "super_admin"
      ? [
          { href: "/admin/tenants", label: "Admin" },
        ]
      : []),
  ];

  return (
    <div className="min-h-screen bg-[var(--app-bg)] text-[var(--text-primary)]">
      <Sidebar />
      <div className="lg:pl-72">
        <header
          className="sticky top-0 z-30 border-b border-[var(--border-subtle)] bg-[color:var(--surface-0)]/92 backdrop-blur"
          style={{ paddingTop: "env(safe-area-inset-top)" }}
        >
          <div className="mx-auto flex h-16 w-full max-w-[1680px] items-center justify-between gap-4 px-4 sm:px-6 lg:px-8">
            <div className="min-w-0 flex-1">
              <p className="hidden truncate text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--text-tertiary)] lg:block">
                SHIVEX
              </p>
              <h1 className="truncate text-lg font-semibold tracking-[-0.015em] text-[var(--text-primary)]">
                {pageMeta.title}
              </h1>
              <p className="hidden truncate text-sm text-[var(--text-secondary)] lg:block">
                {pageMeta.subtitle}
              </p>
            </div>
            <div className="hidden items-center gap-3 lg:flex">
              <div className="items-center gap-2 rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-1)] px-3 py-2 text-xs font-medium text-[var(--text-secondary)] lg:flex">
                <span className="h-2 w-2 rounded-full bg-[var(--tone-success-solid)]" />
                Platform Live
              </div>
              <div className="rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-1)] px-3 py-2 text-right">
                <p className="max-w-[12rem] truncate text-sm font-semibold text-[var(--text-primary)]">
                  {me?.user.full_name ?? me?.user.email ?? "Operator"}
                </p>
                <p className="text-[11px] uppercase tracking-[0.14em] text-[var(--text-tertiary)]">
                  {me?.user.role?.replaceAll("_", " ") ?? "authenticated"}
                </p>
              </div>
              <Button variant="outline" size="sm" onClick={() => void handleLogout()}>
                Sign out
              </Button>
            </div>
          </div>
        </header>
        <main className="mx-auto w-full max-w-[1680px] px-4 py-5 pb-24 sm:px-6 lg:px-8 lg:py-6 lg:pb-0">
          <PlatformMaintenanceBanner />
          {children}
        </main>
        <nav className="fixed bottom-0 left-0 right-0 z-50 flex lg:hidden border-t border-[var(--border-subtle)] bg-[var(--surface-0)] pb-safe">
          {mobilePrimaryItems.filter((item) => !item.feature || hasFeature(me, item.feature)).map((item) => {
            const Icon = item.icon;
            const isActive =
              pathname === item.href ||
              (item.href !== "/" && pathname.startsWith(`${item.href}/`));

            return (
              <Link
                key={item.href}
                href={item.href}
                className={`flex w-[16.66%] shrink-0 flex-col items-center justify-center gap-1 py-3 text-[9px] font-medium ${
                  isActive ? "text-blue-500" : "text-gray-400"
                }`}
              >
                <Icon className="h-4 w-4" />
                <span>{item.label}</span>
              </Link>
            );
          })}
          <button
            type="button"
            onClick={() => setMobileMoreOpen(true)}
            className={`flex w-[16.66%] shrink-0 flex-col items-center justify-center gap-1 py-3 text-[9px] font-medium ${
              ["/rules", "/waste-analysis", "/settings"].some(
                (href) => pathname === href || pathname.startsWith(`${href}/`),
              )
                ? "text-blue-500"
                : "text-gray-400"
            }`}
          >
            <SettingsIcon className="h-4 w-4" />
            <span>More</span>
          </button>
        </nav>
        {mobileMoreOpen ? (
          <div className="fixed inset-0 z-[60] flex items-end justify-center bg-black/45 px-4 pb-[calc(5.5rem+env(safe-area-inset-bottom))] lg:hidden">
            <button
              type="button"
              aria-label="Close more menu"
              className="absolute inset-0"
              onClick={() => setMobileMoreOpen(false)}
            />
            <div className="relative z-10 w-full max-w-md rounded-t-3xl border border-[var(--border-subtle)] bg-[var(--surface-0)] p-4 shadow-2xl">
              <div className="mb-3 flex items-center justify-between">
                <h2 className="text-sm font-semibold text-[var(--text-primary)]">
                  More
                </h2>
                <button
                  type="button"
                  onClick={() => setMobileMoreOpen(false)}
                  className="rounded-full px-2 py-1 text-xs font-medium text-[var(--text-secondary)]"
                >
                  Close
                </button>
              </div>
              <div className="space-y-2">
                {mobileMoreItems.map((item) => {
                  const isActive =
                    pathname === item.href ||
                    pathname.startsWith(`${item.href}/`);

                  return (
                    <Link
                      key={item.href}
                      href={item.href}
                      onClick={() => setMobileMoreOpen(false)}
                      className={`flex items-center justify-between rounded-2xl border px-4 py-4 text-sm font-medium ${
                        isActive
                          ? "border-[var(--tone-info-border)] bg-[var(--tone-info-surface)] text-[var(--tone-info-text)]"
                          : "border-[var(--border-subtle)] bg-[var(--surface-1)] text-[var(--text-primary)]"
                      }`}
                    >
                      <span>{item.label}</span>
                      <span className="text-[var(--text-tertiary)]">Open</span>
                    </Link>
                  );
                })}
                <button
                  type="button"
                  onClick={() => {
                    setMobileMoreOpen(false);
                    void handleLogout();
                  }}
                  className="flex w-full items-center justify-between rounded-2xl border border-red-200 bg-red-50 px-4 py-4 text-sm font-medium text-red-700"
                >
                  <span>Sign out</span>
                  <span className="text-red-400">Exit</span>
                </button>
              </div>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
