"use client";

import { Badge } from "@/components/ui/badge";
import { formatIST } from "@/lib/utils";
import {
  formatPlatformMaintenanceDuration,
  getPlatformMaintenanceSeverityBadgeVariant,
  type PlatformMaintenanceFormState,
} from "@/lib/platformMaintenance";

export function PlatformMaintenancePreview({
  form,
}: {
  form: PlatformMaintenanceFormState;
}) {
  const durationMinutes = Number.parseInt(form.estimatedDurationMinutes, 10);
  const title = form.title.trim() || "Scheduled platform maintenance";
  const message =
    form.message.trim() ||
    "Planned platform work will appear here so users know what to expect before the maintenance window begins.";
  const startsAtDate = form.startsAt ? new Date(form.startsAt) : null;
  const startsAtLabel = startsAtDate && !Number.isNaN(startsAtDate.getTime())
    ? formatIST(startsAtDate.toISOString(), "Scheduled time")
    : "Choose a start time";
  const durationLabel = Number.isFinite(durationMinutes) && durationMinutes > 0
    ? formatPlatformMaintenanceDuration(durationMinutes)
    : "Add a duration";

  return (
    <div className="space-y-4">
      <div className="rounded-[1.25rem] border border-[var(--border-subtle)] bg-[linear-gradient(135deg,rgba(255,255,255,0.98),rgba(239,246,255,0.78))] p-4 shadow-[var(--shadow-soft)]">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="space-y-2">
            <Badge variant={getPlatformMaintenanceSeverityBadgeVariant(form.severity)}>
              {form.severity === "critical" ? "Critical maintenance" : form.severity === "warning" ? "Important maintenance" : "Maintenance notice"}
            </Badge>
            <div>
              <p className="text-base font-semibold tracking-[-0.01em] text-[var(--text-primary)]">{title}</p>
              <p className="mt-1 max-w-2xl whitespace-pre-wrap text-sm leading-6 text-[var(--text-secondary)]">{message}</p>
            </div>
          </div>
          <div className="min-w-[220px] rounded-2xl border border-[var(--border-subtle)] bg-white/80 px-3 py-3 text-sm text-[var(--text-secondary)]">
            <p className="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--text-tertiary)]">Maintenance window</p>
            <p className="mt-2 font-medium text-[var(--text-primary)]">{startsAtLabel}</p>
            <p className="mt-1 text-[var(--text-secondary)]">Expected duration: {durationLabel}</p>
          </div>
        </div>
      </div>
      <p className="text-xs text-[var(--text-tertiary)]">
        Preview only. This reflects the banner style users will see in the product during scheduled or active maintenance.
      </p>
    </div>
  );
}
