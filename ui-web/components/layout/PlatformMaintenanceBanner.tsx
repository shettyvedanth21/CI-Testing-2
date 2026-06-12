"use client";

import { useEffect, useMemo, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { authApi, type PlatformMaintenanceAnnouncement } from "@/lib/authApi";
import { useAuth } from "@/lib/authContext";
import { useTenantStore } from "@/lib/tenantStore";
import { formatIST } from "@/lib/utils";
import {
  chooseVisiblePlatformMaintenanceAnnouncements,
  formatPlatformMaintenanceDuration,
  getPlatformMaintenanceSeverityBadgeVariant,
} from "@/lib/platformMaintenance";

export function PlatformMaintenanceBanner() {
  const { me } = useAuth();
  const { selectedTenantId } = useTenantStore();
  const [announcements, setAnnouncements] = useState<PlatformMaintenanceAnnouncement[]>([]);

  const canResolveTenantScope = Boolean(
    me && (me.user.role !== "super_admin" || selectedTenantId),
  );

  useEffect(() => {
    let active = true;

    async function load() {
      if (!me || !canResolveTenantScope) {
        if (active) {
          setAnnouncements([]);
        }
        return;
      }
      try {
        const payload = await authApi.getCurrentPlatformMaintenance();
        if (active) {
          setAnnouncements(payload.announcements ?? []);
        }
      } catch {
        if (active) {
          setAnnouncements([]);
        }
      }
    }

    void load();
    const intervalId = window.setInterval(() => {
      void load();
    }, 60_000);

    return () => {
      active = false;
      window.clearInterval(intervalId);
    };
  }, [canResolveTenantScope, me, selectedTenantId]);

  const visibleAnnouncements = useMemo(
    () => chooseVisiblePlatformMaintenanceAnnouncements(announcements),
    [announcements],
  );

  if (!me || visibleAnnouncements.length === 0) {
    return null;
  }

  return (
    <div className="mb-5 space-y-3">
      {visibleAnnouncements.map((announcement) => {
        const isActive = announcement.effective_status === "active";
        const startsLabel = formatIST(announcement.starts_at, "Scheduled time");
        const durationLabel = formatPlatformMaintenanceDuration(announcement.estimated_duration_minutes);
        return (
          <section
            key={announcement.id}
            className={`overflow-hidden rounded-[1.25rem] border px-4 py-4 shadow-[var(--shadow-soft)] ${
              isActive
                ? "border-[var(--tone-danger-border)] bg-[linear-gradient(135deg,rgba(254,242,242,0.98),rgba(255,255,255,0.96))]"
                : "border-[var(--tone-info-border)] bg-[linear-gradient(135deg,rgba(239,246,255,0.98),rgba(255,255,255,0.96))]"
            }`}
          >
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0 space-y-2">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant={getPlatformMaintenanceSeverityBadgeVariant(announcement.severity)}>
                    {isActive ? "Maintenance in progress" : "Scheduled maintenance"}
                  </Badge>
                  <span className="text-xs font-medium uppercase tracking-[0.08em] text-[var(--text-tertiary)]">
                    {announcement.severity === "critical" ? "Critical" : announcement.severity === "warning" ? "Important" : "Heads-up"}
                  </span>
                </div>
                <div>
                  <h2 className="text-base font-semibold tracking-[-0.01em] text-[var(--text-primary)]">
                    {announcement.title}
                  </h2>
                  <p className="mt-1 whitespace-pre-wrap text-sm leading-6 text-[var(--text-secondary)]">
                    {announcement.message}
                  </p>
                </div>
              </div>
              <div className="min-w-[220px] rounded-2xl border border-black/5 bg-white/80 px-3 py-3 text-sm text-[var(--text-secondary)]">
                <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--text-tertiary)]">
                  {isActive ? "Happening now" : "Starts"}
                </p>
                <p className="mt-2 font-medium text-[var(--text-primary)]">{startsLabel}</p>
                <p className="mt-1">Expected duration: {durationLabel}</p>
              </div>
            </div>
          </section>
        );
      })}
      {announcements.length > visibleAnnouncements.length ? (
        <p className="px-1 text-xs text-[var(--text-tertiary)]">
          {announcements.length - visibleAnnouncements.length} more maintenance notice{announcements.length - visibleAnnouncements.length === 1 ? "" : "s"} apply to your organisation.
        </p>
      ) : null}
    </div>
  );
}
