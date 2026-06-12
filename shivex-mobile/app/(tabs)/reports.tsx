import { useQuery } from "@tanstack/react-query";
import { useCallback } from "react";
import { useRouter } from "expo-router";
import { useFocusEffect } from "@react-navigation/native";
import { Linking, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";

import { getReportsList, getReportDownloadUrl, getReportResult } from "../../src/api/reports";
import { colors } from "../../src/constants/colors";
import { EmptyState } from "../../src/components/EmptyState";
import { ErrorState } from "../../src/components/ErrorState";
import { SkeletonBox } from "../../src/components/SkeletonBox";
import { formatCompactDate } from "../../src/utils/format";

export default function ReportsTab() {
  const router = useRouter();
  const reportsQuery = useQuery({
    queryKey: ["reports-history"],
    queryFn: async () => {
      const history = await getReportsList();
      if (!history) {
        throw new Error("Unable to load reports");
      }

      const completed = history.filter((item) => item.status === "completed").slice(0, 6);
      const summaries = await Promise.all(
        completed.map(async (item) => {
          const result = await getReportResult(item.reportId);
          const totalKwh =
            typeof result === "object" &&
            result !== null &&
            "summary" in result &&
            typeof result.summary === "object" &&
            result.summary !== null &&
            "total_kwh" in result.summary &&
            typeof result.summary.total_kwh === "number"
              ? result.summary.total_kwh
              : null;

          return {
            reportId: item.reportId,
            totalKwh,
            createdAt: item.createdAt,
          };
        })
      );

      const now = Date.now();
      const weekCutoff = now - 7 * 24 * 60 * 60 * 1000;
      const monthCutoff = now - 30 * 24 * 60 * 60 * 1000;

      const weeklyKwh = summaries.reduce((sum, item) => {
        const createdAt = item.createdAt ? new Date(item.createdAt).getTime() : 0;
        return createdAt >= weekCutoff ? sum + (item.totalKwh ?? 0) : sum;
      }, 0);

      const monthlyKwh = summaries.reduce((sum, item) => {
        const createdAt = item.createdAt ? new Date(item.createdAt).getTime() : 0;
        return createdAt >= monthCutoff ? sum + (item.totalKwh ?? 0) : sum;
      }, 0);

      return {
        history,
        weeklyKwh: weeklyKwh > 0 ? weeklyKwh : null,
        monthlyKwh: monthlyKwh > 0 ? monthlyKwh : null,
      };
    },
  });

  useFocusEffect(
    useCallback(() => {
      void reportsQuery.refetch();
    }, [reportsQuery])
  );

  if (reportsQuery.isLoading) {
    return (
      <View style={styles.screen}>
        <View style={styles.content}>
          <SkeletonBox height={96} borderRadius={14} />
          <SkeletonBox height={96} borderRadius={14} />
          <SkeletonBox height={120} borderRadius={14} />
        </View>
      </View>
    );
  }

  if (reportsQuery.isError || !reportsQuery.data) {
    return (
      <View style={styles.screen}>
        <ErrorState message="Unable to load reports" onRetry={() => void reportsQuery.refetch()} />
      </View>
    );
  }

  const { history, weeklyKwh, monthlyKwh } = reportsQuery.data;

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      <Text style={styles.title}>Reports</Text>

      {weeklyKwh !== null || monthlyKwh !== null ? (
        <View style={styles.summaryRow}>
          <View style={styles.summaryCard}>
            <Text style={styles.summaryLabel}>This Week</Text>
            <Text style={styles.summaryValue}>{weeklyKwh?.toFixed(1) ?? "--"} kWh</Text>
          </View>
          <View style={styles.summaryCard}>
            <Text style={styles.summaryLabel}>This Month</Text>
            <Text style={styles.summaryValue}>{monthlyKwh?.toFixed(1) ?? "--"} kWh</Text>
          </View>
        </View>
      ) : null}

      <Pressable style={styles.primaryButton} onPress={() => router.push("/reports/energy")}>
        <Text style={styles.primaryButtonText}>Generate Energy Report</Text>
      </Pressable>
      <Pressable style={styles.secondaryButton} onPress={() => router.push("/reports/compare")}>
        <Text style={styles.secondaryButtonText}>Compare Reports</Text>
      </Pressable>

      <Text style={styles.sectionTitle}>Recent Reports</Text>
      {history.length === 0 ? (
        <EmptyState message="No reports generated yet" icon="file-text" />
      ) : (
        history.map((report) => (
          <View key={report.reportId} style={styles.reportCard}>
            <View style={styles.reportMeta}>
              <Text style={styles.reportTitle}>{report.reportType}</Text>
              <Text style={styles.reportDate}>
                {formatCompactDate(report.createdAt)} - {formatCompactDate(report.completedAt ?? report.createdAt)}
              </Text>
            </View>
            <View style={styles.reportActions}>
              <View style={styles.statusChip}>
                <Text style={styles.statusChipText}>{report.status}</Text>
              </View>
              <Pressable onPress={() => void Linking.openURL(getReportDownloadUrl(report.reportId))}>
                <Text style={styles.downloadText}>Download</Text>
              </Pressable>
            </View>
          </View>
        ))
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.background },
  content: { padding: 16, gap: 14, paddingBottom: 24 },
  title: { color: colors.textPrimary, fontSize: 24, fontWeight: "700" },
  summaryRow: { flexDirection: "row", gap: 12 },
  summaryCard: {
    flex: 1,
    backgroundColor: colors.card,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 14,
    padding: 16,
    gap: 8,
  },
  summaryLabel: { color: colors.textSecondary, fontSize: 13 },
  summaryValue: { color: colors.textPrimary, fontSize: 24, fontWeight: "700" },
  primaryButton: {
    backgroundColor: colors.primary,
    borderRadius: 12,
    paddingVertical: 14,
    alignItems: "center",
  },
  primaryButtonText: { color: colors.textPrimary, fontSize: 15, fontWeight: "700" },
  secondaryButton: {
    backgroundColor: colors.card,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 12,
    paddingVertical: 14,
    alignItems: "center",
  },
  secondaryButtonText: { color: colors.textPrimary, fontSize: 15, fontWeight: "700" },
  sectionTitle: { color: colors.textPrimary, fontSize: 18, fontWeight: "700" },
  reportCard: {
    backgroundColor: colors.card,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 14,
    padding: 16,
    flexDirection: "row",
    justifyContent: "space-between",
    gap: 12,
  },
  reportMeta: { flex: 1, gap: 4 },
  reportTitle: { color: colors.textPrimary, fontSize: 15, fontWeight: "700" },
  reportDate: { color: colors.textSecondary, fontSize: 12 },
  reportActions: { alignItems: "flex-end", gap: 10 },
  statusChip: {
    backgroundColor: colors.background,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 999,
    paddingHorizontal: 10,
    paddingVertical: 5,
  },
  statusChipText: { color: colors.primary, fontSize: 11, fontWeight: "700" },
  downloadText: { color: colors.primary, fontSize: 13, fontWeight: "700" },
});
