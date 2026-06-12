"use client";

import { useEffect, useMemo, useState } from "react";
import {
  authApi,
  type PlatformMaintenanceAnnouncement,
  type PlatformMaintenanceStatus,
  type TenantProfile,
} from "@/lib/authApi";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/input";
import { EmptyState } from "@/components/EmptyState";
import { PageHeader, SectionCard } from "@/components/ui/page-scaffold";
import { PlatformMaintenancePreview } from "@/components/admin/PlatformMaintenancePreview";
import { formatIST } from "@/lib/utils";
import {
  buildPlatformMaintenancePayload,
  createDefaultPlatformMaintenanceForm,
  formFromAnnouncement,
  formatPlatformMaintenanceDuration,
  getPlatformMaintenanceAudienceSummary,
  getPlatformMaintenanceSeverityBadgeVariant,
  getPlatformMaintenanceStatusBadgeVariant,
  getPlatformMaintenanceStatusLabel,
  PLATFORM_MAINTENANCE_SEVERITY_OPTIONS,
  toLocalDateTimeInputValue,
  validatePlatformMaintenanceForm,
  type PlatformMaintenanceFormErrors,
  type PlatformMaintenanceFormState,
} from "@/lib/platformMaintenance";

function AnnouncementListSkeleton() {
  return (
    <div className="space-y-3">
      {Array.from({ length: 4 }).map((_, index) => (
        <div
          key={index}
          className="animate-pulse rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-1)] px-4 py-4"
        >
          <div className="h-4 w-36 rounded bg-[var(--surface-2)]" />
          <div className="mt-2 h-3 w-24 rounded bg-[var(--surface-2)]" />
          <div className="mt-4 h-3 w-44 rounded bg-[var(--surface-2)]" />
        </div>
      ))}
    </div>
  );
}

function countSelectedTenantNames(tenants: TenantProfile[], selectedIds: string[]): string {
  const names = tenants
    .filter((tenant) => selectedIds.includes(tenant.id))
    .slice(0, 3)
    .map((tenant) => tenant.name);

  if (names.length === 0) {
    return "No organisations selected";
  }

  if (selectedIds.length > names.length) {
    return `${names.join(", ")} +${selectedIds.length - names.length} more`;
  }

  return names.join(", ");
}

export default function PlatformMaintenanceAdminPage() {
  const [announcements, setAnnouncements] = useState<PlatformMaintenanceAnnouncement[]>([]);
  const [tenants, setTenants] = useState<TenantProfile[]>([]);
  const [isLoadingPage, setIsLoadingPage] = useState(true);
  const [isLoadingAnnouncement, setIsLoadingAnnouncement] = useState(false);
  const [selectedAnnouncementId, setSelectedAnnouncementId] = useState<string | null>(null);
  const [form, setForm] = useState<PlatformMaintenanceFormState>(createDefaultPlatformMaintenanceForm());
  const [errors, setErrors] = useState<PlatformMaintenanceFormErrors>({});
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isSavingDraft, setIsSavingDraft] = useState(false);
  const [isScheduling, setIsScheduling] = useState(false);
  const [isSavingChanges, setIsSavingChanges] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [orgSearch, setOrgSearch] = useState("");

  const selectedAnnouncement = useMemo(
    () => announcements.find((announcement) => announcement.id === selectedAnnouncementId) ?? null,
    [announcements, selectedAnnouncementId],
  );

  const visibleTenants = useMemo(() => {
    const query = orgSearch.trim().toLowerCase();
    if (!query) {
      return tenants;
    }
    return tenants.filter((tenant) =>
      `${tenant.name} ${tenant.slug} ${tenant.id}`.toLowerCase().includes(query),
    );
  }, [orgSearch, tenants]);

  const currentStatus = selectedAnnouncement?.effective_status ?? form.status;

  useEffect(() => {
    let isMounted = true;

    async function load(): Promise<void> {
      setIsLoadingPage(true);
      setError(null);
      try {
        const [announcementRows, tenantRows] = await Promise.all([
          authApi.listPlatformMaintenanceAnnouncements(),
          authApi.listTenants(),
        ]);
        if (!isMounted) {
          return;
        }
        setAnnouncements(announcementRows);
        setTenants(tenantRows);
      } catch (err) {
        if (isMounted) {
          setError(err instanceof Error ? err.message : "Failed to load the platform maintenance workspace.");
        }
      } finally {
        if (isMounted) {
          setIsLoadingPage(false);
        }
      }
    }

    void load();

    return () => {
      isMounted = false;
    };
  }, []);

  useEffect(() => {
    if (!notice) {
      return undefined;
    }
    const timer = window.setTimeout(() => setNotice(null), 3000);
    return () => window.clearTimeout(timer);
  }, [notice]);

  function updateForm<K extends keyof PlatformMaintenanceFormState>(
    key: K,
    value: PlatformMaintenanceFormState[K],
  ) {
    setForm((current) => ({ ...current, [key]: value }));
    setErrors((current) => ({ ...current, [key]: undefined }));
  }

  function resetForNewNotice() {
    setSelectedAnnouncementId(null);
    setForm(createDefaultPlatformMaintenanceForm());
    setErrors({});
    setError(null);
    setNotice(null);
  }

  async function handleSelectAnnouncement(announcementId: string) {
    setSelectedAnnouncementId(announcementId);
    setIsLoadingAnnouncement(true);
    setError(null);
    setNotice(null);
    try {
      const detail = await authApi.getPlatformMaintenanceAnnouncement(announcementId);
      setForm(formFromAnnouncement(detail));
      setAnnouncements((current) =>
        current.map((announcement) => (announcement.id === detail.id ? detail : announcement)),
      );
      setErrors({});
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to open the maintenance notice.");
    } finally {
      setIsLoadingAnnouncement(false);
    }
  }

  function toggleTenantSelection(tenantId: string) {
    setForm((current) => {
      const selected = current.targetTenantIds.includes(tenantId)
        ? current.targetTenantIds.filter((id) => id !== tenantId)
        : [...current.targetTenantIds, tenantId];
      return { ...current, targetTenantIds: selected };
    });
    setErrors((current) => ({ ...current, targetTenantIds: undefined }));
  }

  async function submitWithStatus(
    nextStatus: PlatformMaintenanceStatus,
    mode: "draft" | "schedule" | "save",
    sourceForm: PlatformMaintenanceFormState = form,
  ) {
    const nextForm = { ...sourceForm, status: nextStatus };
    const nextErrors = validatePlatformMaintenanceForm(nextForm, tenants);
    if (Object.keys(nextErrors).length > 0) {
      setErrors(nextErrors);
      setError("Please fix the highlighted fields before saving.");
      return;
    }

    setErrors({});
    setError(null);
    setNotice(null);

    const setSaving = mode === "draft"
      ? setIsSavingDraft
      : mode === "schedule"
        ? setIsScheduling
        : setIsSavingChanges;
    setSaving(true);

    try {
      const payload = buildPlatformMaintenancePayload(nextForm);
      const saved = selectedAnnouncementId
        ? await authApi.updatePlatformMaintenanceAnnouncement(selectedAnnouncementId, payload)
        : await authApi.createPlatformMaintenanceAnnouncement(payload);

      setForm(formFromAnnouncement(saved));
      setSelectedAnnouncementId(saved.id);
      setAnnouncements((current) => {
        const existingIndex = current.findIndex((announcement) => announcement.id === saved.id);
        if (existingIndex >= 0) {
          return current.map((announcement) => (announcement.id === saved.id ? saved : announcement));
        }
        return [saved, ...current];
      });

      setNotice(
        mode === "draft"
          ? "Draft saved."
          : mode === "schedule"
            ? "Maintenance notice scheduled."
            : "Maintenance notice updated.",
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save the maintenance notice.");
    } finally {
      setSaving(false);
    }
  }

  async function handleDeleteAnnouncement() {
    if (!selectedAnnouncementId || !selectedAnnouncement) {
      return;
    }
    const confirmed = window.confirm(`Delete "${selectedAnnouncement.title}"? This cannot be undone.`);
    if (!confirmed) {
      return;
    }

    setIsDeleting(true);
    setError(null);
    setNotice(null);
    try {
      await authApi.deletePlatformMaintenanceAnnouncement(selectedAnnouncementId);
      setAnnouncements((current) => current.filter((announcement) => announcement.id !== selectedAnnouncementId));
      resetForNewNotice();
      setNotice("Maintenance notice deleted.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete the maintenance notice.");
    } finally {
      setIsDeleting(false);
    }
  }

  async function handleGoLiveNow() {
    const liveForm = {
      ...form,
      startsAt: toLocalDateTimeInputValue(new Date()),
    };
    setForm(liveForm);
    await submitWithStatus("active", "save", liveForm);
  }

  async function handleCancelNotice() {
    await submitWithStatus("cancelled", "save");
  }

  async function handleMarkComplete() {
    await submitWithStatus("completed", "save");
  }

  const statusSummary =
    currentStatus === "draft"
      ? "Draft notices stay internal until you schedule them."
      : currentStatus === "scheduled"
        ? "This notice is planned and will appear when the maintenance window begins."
        : currentStatus === "active"
          ? "This notice is live for the selected organisations right now."
          : currentStatus === "completed"
            ? "This maintenance window has finished. You can still update the wording or delete the notice."
            : "This notice has been cancelled and is no longer shown to users.";

  const audienceSummary = getPlatformMaintenanceAudienceSummary(form, tenants);

  return (
    <div className="space-y-5">
      <PageHeader
        title="Platform Maintenance"
        subtitle="Create and manage maintenance notices for the organisations that should receive platform-wide updates."
        actions={(
          <Button variant="outline" onClick={resetForNewNotice}>
            New Notice
          </Button>
        )}
      />

      {notice ? (
        <div className="rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
          {notice}
        </div>
      ) : null}

      {error ? (
        <div className="rounded-2xl border border-[var(--tone-danger-border)] bg-[var(--tone-danger-bg)] px-4 py-3 text-sm text-[var(--tone-danger-text)]">
          {error}
        </div>
      ) : null}

      <div className="grid gap-5 xl:grid-cols-[380px_minmax(0,1fr)]">
        <SectionCard
          title="Saved notices"
          subtitle="Open an existing notice to update it, or start a fresh draft."
        >
          {isLoadingPage ? (
            <AnnouncementListSkeleton />
          ) : announcements.length === 0 ? (
            <EmptyState message="No maintenance notices yet. Start a draft to plan your first platform announcement." />
          ) : (
            <div className="space-y-3">
              {announcements.map((announcement) => {
                const isActive = announcement.id === selectedAnnouncementId;
                const audienceLabel = announcement.broadcast_all_tenants
                  ? "All organisations"
                  : countSelectedTenantNames(tenants, announcement.target_tenant_ids);
                return (
                  <button
                    key={announcement.id}
                    type="button"
                    onClick={() => void handleSelectAnnouncement(announcement.id)}
                    className={`w-full rounded-2xl border px-4 py-4 text-left transition ${
                      isActive
                        ? "border-[var(--tone-info-border)] bg-[var(--tone-info-bg)]"
                        : "border-[var(--border-subtle)] bg-[var(--surface-0)] hover:bg-[var(--surface-1)]"
                    }`}
                  >
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="truncate text-sm font-semibold text-[var(--text-primary)]">{announcement.title}</p>
                        <p className="mt-1 text-xs text-[var(--text-secondary)]">{audienceLabel}</p>
                      </div>
                      <div className="flex flex-wrap gap-2">
                        <Badge variant={getPlatformMaintenanceSeverityBadgeVariant(announcement.severity)}>
                          {PLATFORM_MAINTENANCE_SEVERITY_OPTIONS.find((option) => option.value === announcement.severity)?.label ?? announcement.severity}
                        </Badge>
                        <Badge variant={getPlatformMaintenanceStatusBadgeVariant(announcement.effective_status)}>
                          {getPlatformMaintenanceStatusLabel(announcement.effective_status)}
                        </Badge>
                      </div>
                    </div>
                    <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-[var(--text-tertiary)]">
                      <span>Starts {formatIST(announcement.starts_at, "Not set")}</span>
                      <span>{formatPlatformMaintenanceDuration(announcement.estimated_duration_minutes)}</span>
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </SectionCard>

        <div className="space-y-5">
          <SectionCard
            title="Maintenance details"
            subtitle="Set the timing, urgency, and message people will see."
          >
            <div className={`grid gap-4 ${isLoadingAnnouncement ? "pointer-events-none opacity-60" : ""}`}>
              <Input
                label="Notice title"
                value={form.title}
                onChange={(event) => updateForm("title", event.target.value)}
                placeholder="Scheduled platform maintenance"
                error={errors.title}
              />
              <div className="grid gap-4 md:grid-cols-2">
                <Select
                  label="Severity"
                  value={form.severity}
                  onChange={(event) => updateForm("severity", event.target.value as PlatformMaintenanceFormState["severity"])}
                  helperText={PLATFORM_MAINTENANCE_SEVERITY_OPTIONS.find((option) => option.value === form.severity)?.helper}
                  options={PLATFORM_MAINTENANCE_SEVERITY_OPTIONS.map((option) => ({
                    value: option.value,
                    label: option.label,
                  }))}
                />
                <div className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-1)] px-4 py-3">
                  <p className="text-sm font-medium text-[var(--text-primary)]">Notice state</p>
                  <div className="mt-2 flex flex-wrap items-center gap-3">
                    <Badge variant={getPlatformMaintenanceStatusBadgeVariant(currentStatus)}>
                      {getPlatformMaintenanceStatusLabel(currentStatus)}
                    </Badge>
                    {selectedAnnouncement ? (
                      <span className="text-xs text-[var(--text-secondary)]">
                        Based on the saved timing and current maintenance window.
                      </span>
                    ) : (
                      <span className="text-xs text-[var(--text-secondary)]">
                        New notices start as drafts until you schedule them.
                      </span>
                    )}
                  </div>
                  <p className="mt-2 text-xs text-[var(--text-secondary)]">{statusSummary}</p>
                </div>
              </div>
              <div className="grid gap-4 md:grid-cols-2">
                <Input
                  label="Start time"
                  type="datetime-local"
                  value={form.startsAt}
                  onChange={(event) => updateForm("startsAt", event.target.value)}
                  error={errors.startsAt}
                />
                <Input
                  label="Expected duration (minutes)"
                  type="number"
                  min="1"
                  step="1"
                  value={form.estimatedDurationMinutes}
                  onChange={(event) => updateForm("estimatedDurationMinutes", event.target.value)}
                  helperText="Example: 60 for one hour, 180 for three hours."
                  error={errors.estimatedDurationMinutes}
                />
              </div>
              <div className="space-y-1">
                <label htmlFor="maintenance-message" className="block text-sm font-medium text-slate-700">
                  Message to users
                </label>
                <textarea
                  id="maintenance-message"
                  rows={6}
                  value={form.message}
                  onChange={(event) => updateForm("message", event.target.value)}
                  placeholder="We’re carrying out planned platform work during this window. Some screens may be slower to update while the maintenance is in progress."
                  className={`block w-full rounded-xl border bg-[var(--surface-0)] px-3 py-2 text-sm text-[var(--text-primary)] shadow-sm transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)] ${
                    errors.message ? "border-[var(--tone-danger-border)] focus-visible:ring-[var(--tone-danger-solid)]" : "border-[var(--border-subtle)]"
                  }`}
                />
                {errors.message ? (
                  <p className="text-sm text-red-600">{errors.message}</p>
                ) : (
                  <p className="text-xs text-[var(--text-secondary)]">
                    Keep this plain and practical so teams immediately understand the timing and likely impact.
                  </p>
                )}
              </div>
            </div>
          </SectionCard>

          <SectionCard
            title="Audience / target organisations"
            subtitle="Choose whether the notice reaches everyone or only selected organisations."
          >
            <div className={`space-y-4 ${isLoadingAnnouncement ? "pointer-events-none opacity-60" : ""}`}>
              <div className="grid gap-3 md:grid-cols-2">
                <button
                  type="button"
                  onClick={() => updateForm("broadcastAllTenants", true)}
                  className={`rounded-2xl border px-4 py-4 text-left transition ${
                    form.broadcastAllTenants
                      ? "border-[var(--tone-info-border)] bg-[var(--tone-info-bg)]"
                      : "border-[var(--border-subtle)] bg-[var(--surface-0)] hover:bg-[var(--surface-1)]"
                  }`}
                >
                  <p className="text-sm font-semibold text-[var(--text-primary)]">All organisations</p>
                  <p className="mt-1 text-sm text-[var(--text-secondary)]">
                    Send the notice across the whole platform.
                  </p>
                </button>
                <button
                  type="button"
                  onClick={() => updateForm("broadcastAllTenants", false)}
                  className={`rounded-2xl border px-4 py-4 text-left transition ${
                    !form.broadcastAllTenants
                      ? "border-[var(--tone-info-border)] bg-[var(--tone-info-bg)]"
                      : "border-[var(--border-subtle)] bg-[var(--surface-0)] hover:bg-[var(--surface-1)]"
                  }`}
                >
                  <p className="text-sm font-semibold text-[var(--text-primary)]">Selected organisations</p>
                  <p className="mt-1 text-sm text-[var(--text-secondary)]">
                    Limit the notice to the organisations you choose below.
                  </p>
                </button>
              </div>

              <div className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-1)] px-4 py-3 text-sm text-[var(--text-secondary)]">
                {audienceSummary}
              </div>

              {form.broadcastAllTenants ? (
                <div className="rounded-2xl border border-dashed border-[var(--border-subtle)] bg-[var(--surface-0)] px-4 py-5 text-sm text-[var(--text-secondary)]">
                  Every organisation will be included automatically for this notice.
                </div>
              ) : (
                <div className="space-y-4">
                  <Input
                    label="Find organisations"
                    value={orgSearch}
                    onChange={(event) => setOrgSearch(event.target.value)}
                    placeholder="Search by name, slug, or organisation ID"
                  />
                  <p className="text-xs text-[var(--text-secondary)]">
                    Suspended organisations are shown for visibility, but they cannot be selected for new delivery.
                  </p>
                  {errors.targetTenantIds ? (
                    <p className="text-sm text-red-600">{errors.targetTenantIds}</p>
                  ) : null}
                  {visibleTenants.length === 0 ? (
                    <EmptyState
                      message={tenants.length === 0
                        ? "No organisations are available yet. Create an organisation first or switch this notice to all organisations."
                        : "No organisations match your search."}
                    />
                  ) : (
                    <div className="max-h-[320px] space-y-2 overflow-y-auto pr-1">
                      {visibleTenants.map((tenant) => {
                        const checked = form.targetTenantIds.includes(tenant.id);
                        const isDisabled = !tenant.is_active && !checked;
                        return (
                          <label
                            key={tenant.id}
                            className={`flex items-start justify-between gap-3 rounded-2xl border px-4 py-3 transition ${
                              isDisabled
                                ? "cursor-not-allowed opacity-70"
                                : "cursor-pointer"
                            } ${
                              checked
                                ? "border-[var(--tone-info-border)] bg-[var(--tone-info-bg)]"
                                : "border-[var(--border-subtle)] bg-[var(--surface-0)] hover:bg-[var(--surface-1)]"
                            }`}
                          >
                            <div className="min-w-0">
                              <p className="text-sm font-semibold text-[var(--text-primary)]">{tenant.name}</p>
                              <p className="mt-1 text-xs text-[var(--text-secondary)]">
                                {tenant.slug} • {tenant.id}
                              </p>
                            </div>
                            <div className="flex items-center gap-3">
                              <Badge variant={tenant.is_active ? "success" : "warning"}>
                                {tenant.is_active ? "Active" : "Suspended"}
                              </Badge>
                              <input
                                type="checkbox"
                                checked={checked}
                                disabled={isDisabled}
                                onChange={() => toggleTenantSelection(tenant.id)}
                                className="h-4 w-4 rounded border-[var(--border-subtle)] text-[var(--tone-info-solid)] focus:ring-[var(--focus-ring)]"
                              />
                            </div>
                          </label>
                        );
                      })}
                    </div>
                  )}
                </div>
              )}
            </div>
          </SectionCard>

          <SectionCard
            title="Live preview of the in-app banner"
            subtitle="Review the message as people will later see it inside the product."
          >
            <PlatformMaintenancePreview form={form} />
          </SectionCard>

          <SectionCard
            title="Notice actions"
            subtitle="Use the action that matches what you want this notice to do next."
          >
            <div className={`space-y-4 ${isLoadingAnnouncement ? "pointer-events-none opacity-60" : ""}`}>
              <div className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-1)] px-4 py-3 text-sm text-[var(--text-secondary)]">
                {selectedAnnouncement
                  ? `Editing a ${getPlatformMaintenanceStatusLabel(selectedAnnouncement.effective_status).toLowerCase()} notice last updated ${formatIST(selectedAnnouncement.updated_at, "recently")}.`
                  : "You are creating a new maintenance notice."}
              </div>
              <div className="flex flex-wrap gap-3">
                {!selectedAnnouncementId || currentStatus === "draft" ? (
                  <>
                    <Button
                      variant="outline"
                      isLoading={isSavingDraft}
                      disabled={isScheduling || isSavingChanges || isDeleting}
                      onClick={() => void submitWithStatus("draft", "draft")}
                    >
                      Save Draft
                    </Button>
                    <Button
                      isLoading={isScheduling}
                      disabled={isSavingDraft || isSavingChanges || isDeleting}
                      onClick={() => void submitWithStatus("scheduled", "schedule")}
                    >
                      Schedule Notice
                    </Button>
                  </>
                ) : null}

                {selectedAnnouncementId && currentStatus !== "draft" ? (
                  <Button
                    variant="secondary"
                    isLoading={isSavingChanges}
                    disabled={isSavingDraft || isScheduling || isDeleting}
                    onClick={() => void submitWithStatus(currentStatus, "save")}
                  >
                    Save Changes
                  </Button>
                ) : null}

                {selectedAnnouncementId && currentStatus === "scheduled" ? (
                  <>
                    <Button
                      isLoading={isSavingChanges}
                      disabled={isSavingDraft || isScheduling || isDeleting}
                      onClick={() => void handleGoLiveNow()}
                    >
                      Go Live Now
                    </Button>
                    <Button
                      variant="outline"
                      isLoading={isSavingChanges}
                      disabled={isSavingDraft || isScheduling || isDeleting}
                      onClick={() => void handleCancelNotice()}
                    >
                      Cancel Notice
                    </Button>
                  </>
                ) : null}

                {selectedAnnouncementId && currentStatus === "active" ? (
                  <>
                    <Button
                      variant="outline"
                      isLoading={isSavingChanges}
                      disabled={isSavingDraft || isScheduling || isDeleting}
                      onClick={() => void handleMarkComplete()}
                    >
                      Mark Complete
                    </Button>
                    <Button
                      variant="outline"
                      isLoading={isSavingChanges}
                      disabled={isSavingDraft || isScheduling || isDeleting}
                      onClick={() => void handleCancelNotice()}
                    >
                      Cancel Notice
                    </Button>
                  </>
                ) : null}

                {selectedAnnouncementId ? (
                  <Button
                    variant="ghost"
                    isLoading={isDeleting}
                    disabled={isSavingDraft || isScheduling || isSavingChanges}
                    onClick={() => void handleDeleteAnnouncement()}
                  >
                    Delete Notice
                  </Button>
                ) : null}
              </div>
              {selectedAnnouncement ? (
                <div className="flex flex-wrap gap-2 text-xs text-[var(--text-tertiary)]">
                  <span className="rounded-full bg-[var(--surface-1)] px-3 py-1">
                    Current state: {getPlatformMaintenanceStatusLabel(selectedAnnouncement.effective_status)}
                  </span>
                  <span className="rounded-full bg-[var(--surface-1)] px-3 py-1">
                    Duration: {formatPlatformMaintenanceDuration(selectedAnnouncement.estimated_duration_minutes)}
                  </span>
                </div>
              ) : null}
            </div>
          </SectionCard>
        </div>
      </div>
    </div>
  );
}
