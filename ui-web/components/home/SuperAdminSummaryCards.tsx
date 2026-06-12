"use client";

import { useEffect, useState } from "react";
import { StatCard } from "@/components/ui/page-scaffold";
import { authApi, type SuperAdminSummary } from "@/lib/authApi";
import { useAuth } from "@/lib/authContext";

const EMPTY_SUMMARY: SuperAdminSummary = {
  total_organisations: 0,
  total_active_devices: 0,
};

function formatCount(value: number): string {
  return value.toLocaleString("en-US");
}

export function SuperAdminSummaryCards() {
  const { me, isLoading } = useAuth();
  const [summary, setSummary] = useState<SuperAdminSummary>(EMPTY_SUMMARY);
  const [isSummaryLoading, setIsSummaryLoading] = useState(false);
  const [summaryError, setSummaryError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function loadSummary(): Promise<void> {
      if (!me || me.user.role !== "super_admin") {
        setSummary(EMPTY_SUMMARY);
        setSummaryError(null);
        setIsSummaryLoading(false);
        return;
      }

      setIsSummaryLoading(true);
      setSummaryError(null);

      try {
        const nextSummary = await authApi.getSuperAdminSummary();
        if (!cancelled) {
          setSummary(nextSummary);
        }
      } catch (error) {
        if (!cancelled) {
          setSummary(EMPTY_SUMMARY);
          setSummaryError(error instanceof Error ? error.message : "Failed to load platform summary");
        }
      } finally {
        if (!cancelled) {
          setIsSummaryLoading(false);
        }
      }
    }

    void loadSummary();

    return () => {
      cancelled = true;
    };
  }, [me]);

  if (isLoading || !me || me.user.role !== "super_admin") {
    return null;
  }

  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
      <StatCard
        label="Total Organisations"
        value={isSummaryLoading ? "..." : formatCount(summary.total_organisations)}
        tone="info"
      />
      <StatCard
        label="Active Devices"
        value={isSummaryLoading ? "..." : formatCount(summary.total_active_devices)}
        meta={summaryError ?? undefined}
        tone={summaryError ? "warning" : "success"}
      />
    </div>
  );
}
