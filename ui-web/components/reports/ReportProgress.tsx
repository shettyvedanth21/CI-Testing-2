"use client";

import { useState, useEffect, useCallback } from "react";
import { getReportStatus, getReportResult, ReportApiError, ReportStatus } from "@/lib/reportApi";
import { AsyncJobHandoffCard } from "@/components/reports/AsyncJobHandoffCard";

interface ReportProgressProps {
  reportId: string;
  tenantId: string;
  onComplete: (result: unknown) => void;
  onError: (error: { error_code: string; error_message: string }) => void;
  onConfigureAnother?: () => void;
  onStatusChange?: (status: ReportStatus) => void;
}

export function ReportProgress({
  reportId,
  tenantId,
  onComplete,
  onError,
  onConfigureAnother,
  onStatusChange,
}: ReportProgressProps) {
  const [status, setStatus] = useState<ReportStatus | null>(null);

  const checkStatus = useCallback(async () => {
    try {
      const data = await getReportStatus(reportId, tenantId);
      setStatus(data);
      onStatusChange?.(data);

      if (data.status === "completed" && data.result_ready) {
        const result = await getReportResult(reportId, tenantId);
        onComplete(result);
      } else if (data.status === "failed") {
        onError({
          error_code: data.error_code || "UNKNOWN_ERROR",
          error_message: data.error_message || "Report generation failed",
        });
      }
    } catch (error) {
      console.error("Failed to check report status:", error);
      if (error instanceof ReportApiError) {
        onError({
          error_code: "REPORT_STATUS_ERROR",
          error_message: error.message,
        });
      }
    }
  }, [reportId, tenantId, onComplete, onError, onStatusChange]);

  useEffect(() => {
    const startup = setTimeout(() => {
      void checkStatus();
    }, 0);
    const interval = setInterval(checkStatus, 3000);
    return () => {
      clearTimeout(startup);
      clearInterval(interval);
    };
  }, [checkStatus]);

  return (
    <AsyncJobHandoffCard
      title="Report started"
      backgroundMessage="Processing continues in the background. You can continue using the platform while this runs."
      historyLabel="Open Report History"
      historyHref="/reports"
      summary="Some reports finish in seconds, while larger date ranges can take a few minutes."
      status={status}
      primaryActionLabel="Configure another report"
      onPrimaryAction={onConfigureAnother}
    />
  );
}
