"use client";

import { useEffect, useMemo, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/EmptyState";
import { SectionCard, StatCard } from "@/components/ui/page-scaffold";
import { Input, Select } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatIST } from "@/lib/utils";
import {
  buildNotificationUsageRequestKey,
  buildSummaryCards,
  downloadNotificationUsageCsv,
  formatNotificationFailureReason,
  getCurrentMonthLocal,
  getNotificationUsageErrorMessage,
  getNotificationUsageLogs,
  getNotificationUsageSummary,
  shouldShowNotificationUsageEmptyState,
  type NotificationUsageFilters,
  type NotificationUsageLogsResponse,
  type NotificationUsageStatus,
  type NotificationUsageSummaryResponse,
} from "@/lib/adminNotificationUsage";

type OrgNotificationUsageTabProps = {
  orgId: string;
  active: boolean;
};

const CHANNEL_OPTIONS = [
  { value: "", label: "All channels" },
  { value: "email", label: "Email" },
  { value: "sms", label: "SMS" },
  { value: "whatsapp", label: "WhatsApp" },
];

const STATUS_OPTIONS = [
  { value: "", label: "All statuses" },
  { value: "attempted", label: "Attempted" },
  { value: "provider_accepted", label: "Provider accepted" },
  { value: "delivered", label: "Delivered" },
  { value: "failed", label: "Failed" },
  { value: "skipped", label: "Skipped" },
];

function toneForStatus(status: NotificationUsageStatus): "default" | "success" | "warning" | "error" | "info" {
  if (status === "delivered") {
    return "success";
  }
  if (status === "provider_accepted") {
    return "info";
  }
  if (status === "failed") {
    return "error";
  }
  if (status === "skipped") {
    return "warning";
  }
  return "default";
}

function DataSkeletonTable({ columns }: { columns: string[] }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          {columns.map((column) => (
            <TableHead key={column}>{column}</TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {Array.from({ length: 4 }).map((_, index) => (
          <TableRow key={index} className="animate-pulse">
            {columns.map((column) => (
              <TableCell key={`${column}-${index}`}>
                <div className="h-4 w-24 rounded bg-[var(--surface-2)]" />
              </TableCell>
            ))}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

export function OrgNotificationUsageTab({ orgId, active }: OrgNotificationUsageTabProps) {
  const [month, setMonth] = useState<string>(getCurrentMonthLocal());
  const [filters, setFilters] = useState<NotificationUsageFilters>({
    channel: "",
    status: "",
    ruleId: "",
    deviceId: "",
    search: "",
    page: 1,
    pageSize: 50,
  });
  const [summary, setSummary] = useState<NotificationUsageSummaryResponse | null>(null);
  const [logs, setLogs] = useState<NotificationUsageLogsResponse | null>(null);
  const [isLoadingSummary, setIsLoadingSummary] = useState(false);
  const [isLoadingLogs, setIsLoadingLogs] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isExporting, setIsExporting] = useState(false);

  const requestKey = useMemo(
    () => buildNotificationUsageRequestKey(orgId, month, filters),
    [filters, month, orgId],
  );

  useEffect(() => {
    if (!active || !orgId) {
      return;
    }
    let cancelled = false;

    async function loadSummary() {
      setIsLoadingSummary(true);
      try {
        const nextSummary = await getNotificationUsageSummary(orgId, month, filters);
        if (!cancelled) {
          setSummary(nextSummary);
        }
      } catch (err) {
        if (!cancelled) {
          setError(getNotificationUsageErrorMessage(err));
        }
      } finally {
        if (!cancelled) {
          setIsLoadingSummary(false);
        }
      }
    }

    async function loadLogs() {
      setIsLoadingLogs(true);
      try {
        const nextLogs = await getNotificationUsageLogs(orgId, month, filters);
        if (!cancelled) {
          setLogs(nextLogs);
        }
      } catch (err) {
        if (!cancelled) {
          setError(getNotificationUsageErrorMessage(err));
        }
      } finally {
        if (!cancelled) {
          setIsLoadingLogs(false);
        }
      }
    }

    setError(null);
    void Promise.all([loadSummary(), loadLogs()]);
    return () => {
      cancelled = true;
    };
  }, [active, orgId, month, requestKey]);

  async function handleExport(): Promise<void> {
    setIsExporting(true);
    setError(null);
    try {
      await downloadNotificationUsageCsv(orgId, month, filters);
    } catch (err) {
      setError(getNotificationUsageErrorMessage(err));
    } finally {
      setIsExporting(false);
    }
  }

  const summaryCards = summary ? buildSummaryCards(summary) : [];
  const rows = logs?.data ?? [];
  const total = logs?.total ?? 0;
  const page = logs?.page ?? filters.page ?? 1;
  const pageSize = logs?.page_size ?? filters.pageSize ?? 50;
  const totalPages = total > 0 ? Math.ceil(total / pageSize) : 1;
  const hasRows = rows.length > 0;
  const showEmptyState = shouldShowNotificationUsageEmptyState(logs);

  return (
    <div className="space-y-5">
      <SectionCard
        title="Notification Usage"
        subtitle="Counts are derived from recorded notification delivery attempts for this organisation and month."
        actions={(
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              onClick={() => void handleExport()}
              disabled={isExporting || !hasRows}
              isLoading={isExporting}
            >
              Download CSV
            </Button>
          </div>
        )}
      >
        <p className="mb-4 text-sm text-[var(--text-secondary)]">
          Billable counts are based on accepted/delivered ledger rows, not estimated alert volume.
        </p>
        <div className="grid gap-3 md:grid-cols-4">
          <Input
            type="month"
            label="Month"
            value={month}
            onChange={(event) => {
              setMonth(event.target.value);
              setFilters((current) => ({ ...current, page: 1 }));
            }}
          />
          <Select
            label="Channel"
            options={CHANNEL_OPTIONS}
            value={filters.channel ?? ""}
            onChange={(event) =>
              setFilters((current) => ({ ...current, channel: event.target.value as NotificationUsageFilters["channel"], page: 1 }))
            }
          />
          <Select
            label="Status"
            options={STATUS_OPTIONS}
            value={filters.status ?? ""}
            onChange={(event) =>
              setFilters((current) => ({ ...current, status: event.target.value as NotificationUsageFilters["status"], page: 1 }))
            }
          />
          <Input
            label="Search"
            placeholder="Recipient or provider message id"
            value={filters.search ?? ""}
            onChange={(event) => setFilters((current) => ({ ...current, search: event.target.value, page: 1 }))}
          />
          <Input
            label="Rule Id"
            placeholder="Filter by rule id"
            value={filters.ruleId ?? ""}
            onChange={(event) => setFilters((current) => ({ ...current, ruleId: event.target.value, page: 1 }))}
          />
          <Input
            label="Device Id"
            placeholder="Filter by device id"
            value={filters.deviceId ?? ""}
            onChange={(event) => setFilters((current) => ({ ...current, deviceId: event.target.value, page: 1 }))}
          />
          <Select
            label="Page Size"
            options={[
              { value: "25", label: "25" },
              { value: "50", label: "50" },
              { value: "100", label: "100" },
            ]}
            value={`${filters.pageSize ?? 50}`}
            onChange={(event) =>
              setFilters((current) => ({ ...current, pageSize: Number(event.target.value), page: 1 }))
            }
          />
        </div>
      </SectionCard>

      {error ? (
        <div className="rounded-2xl border border-[var(--tone-danger-border)] bg-[var(--tone-danger-bg)] px-4 py-3 text-sm text-[var(--tone-danger-text)]">
          {error}
        </div>
      ) : null}

      <SectionCard title="Monthly Usage Summary" subtitle={`Organisation: ${orgId} · Month: ${month}`}>
        {isLoadingSummary && !summary ? (
          <div className="grid gap-3 md:grid-cols-4">
            {Array.from({ length: 8 }).map((_, index) => (
              <div key={index} className="h-24 animate-pulse rounded-xl bg-[var(--surface-1)]" />
            ))}
          </div>
        ) : (
          <div className="grid gap-3 md:grid-cols-4">
            {summaryCards.map((card) => (
              <StatCard key={card.label} label={card.label} value={card.value} tone={card.tone} />
            ))}
          </div>
        )}
      </SectionCard>

      <SectionCard title="Channel Breakdown">
        {isLoadingSummary && !summary ? (
          <DataSkeletonTable columns={["Channel", "Attempted", "Accepted", "Delivered", "Failed", "Skipped", "Billable"]} />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Channel</TableHead>
                <TableHead>Attempted</TableHead>
                <TableHead>Accepted</TableHead>
                <TableHead>Delivered</TableHead>
                <TableHead>Failed</TableHead>
                <TableHead>Skipped</TableHead>
                <TableHead>Billable</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {(["email", "sms", "whatsapp"] as const).map((channel) => (
                <TableRow key={channel}>
                  <TableCell className="font-medium uppercase">{channel}</TableCell>
                  <TableCell>{summary?.by_channel[channel]?.attempted_count ?? 0}</TableCell>
                  <TableCell>{summary?.by_channel[channel]?.accepted_count ?? 0}</TableCell>
                  <TableCell>{summary?.by_channel[channel]?.delivered_count ?? 0}</TableCell>
                  <TableCell>{summary?.by_channel[channel]?.failed_count ?? 0}</TableCell>
                  <TableCell>{summary?.by_channel[channel]?.skipped_count ?? 0}</TableCell>
                  <TableCell>{summary?.by_channel[channel]?.billable_count ?? 0}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </SectionCard>

      <SectionCard title="Delivery Logs">
        {isLoadingLogs && !logs ? (
          <DataSkeletonTable
            columns={[
              "Attempted At",
              "Channel",
              "Status",
              "Recipient",
              "Provider",
              "Provider Message Id",
              "Rule Id",
              "Device Id",
              "Billable Units",
              "Failure Reason",
            ]}
          />
        ) : showEmptyState ? (
          <EmptyState message="No notification delivery activity for this month and filter selection." />
        ) : (
          <>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Attempted At</TableHead>
                  <TableHead>Channel</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Recipient</TableHead>
                  <TableHead>Provider</TableHead>
                  <TableHead>Provider Message Id</TableHead>
                  <TableHead>Rule Id</TableHead>
                  <TableHead>Device Id</TableHead>
                  <TableHead>Billable Units</TableHead>
                  <TableHead>Failure Reason</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((row) => (
                  <TableRow key={row.id}>
                    <TableCell>{formatIST(row.attempted_at, "—")}</TableCell>
                    <TableCell>
                      <Badge variant="default" className="uppercase">{row.channel}</Badge>
                    </TableCell>
                    <TableCell>
                      <Badge variant={toneForStatus(row.status)}>{row.status.replaceAll("_", " ")}</Badge>
                    </TableCell>
                    <TableCell>{row.recipient_masked}</TableCell>
                    <TableCell>{row.provider_name}</TableCell>
                    <TableCell>{row.provider_message_id || "—"}</TableCell>
                    <TableCell>{row.rule_id || "—"}</TableCell>
                    <TableCell>{row.device_id || "—"}</TableCell>
                    <TableCell>{row.billable_units}</TableCell>
                    <TableCell className="max-w-[260px] truncate">{formatNotificationFailureReason(row.failure_code, row.failure_message)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            <div className="mt-4 flex items-center justify-between">
              <p className="text-sm text-[var(--text-secondary)]">
                Showing page {page} of {totalPages} · {total} total rows
              </p>
              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page <= 1 || isLoadingLogs}
                  onClick={() => setFilters((current) => ({ ...current, page: Math.max(1, (current.page ?? 1) - 1) }))}
                >
                  Previous
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page >= totalPages || isLoadingLogs}
                  onClick={() => setFilters((current) => ({ ...current, page: (current.page ?? 1) + 1 }))}
                >
                  Next
                </Button>
              </div>
            </div>
          </>
        )}
      </SectionCard>
    </div>
  );
}
