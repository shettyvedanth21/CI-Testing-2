import { useCallback, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useFocusEffect } from "@react-navigation/native";
import { ScrollView, Pressable, StyleSheet, Text, TextInput, View } from "react-native";

import { getDevices } from "../../src/api/devices";
import {
  generateComparisonReport,
  getReportResult,
  getReportStatus,
} from "../../src/api/reports";
import { colors } from "../../src/constants/colors";
import { EmptyState } from "../../src/components/EmptyState";
import { ErrorState } from "../../src/components/ErrorState";
import { SkeletonBox } from "../../src/components/SkeletonBox";

export default function CompareReportsScreen() {
  const [deviceId, setDeviceId] = useState("");
  const [periodAStart, setPeriodAStart] = useState("");
  const [periodAEnd, setPeriodAEnd] = useState("");
  const [periodBStart, setPeriodBStart] = useState("");
  const [periodBEnd, setPeriodBEnd] = useState("");
  const [reportId, setReportId] = useState("");
  const [result, setResult] = useState<unknown | null>(null);
  const [error, setError] = useState("");

  const devicesQuery = useQuery({
    queryKey: ["devices"],
    queryFn: async () => {
      const result = await getDevices();
      if (!result) {
        throw new Error("Unable to load devices");
      }
      return result;
    },
  });

  useFocusEffect(
    useCallback(() => {
      void devicesQuery.refetch();
    }, [devicesQuery])
  );

  const createMutation = useMutation({
    mutationFn: async () => {
      const created = await generateComparisonReport({
        tenant_id: "SH00000001",
        comparison_type: "period_vs_period",
        device_id: deviceId,
        period_a_start: periodAStart,
        period_a_end: periodAEnd,
        period_b_start: periodBStart,
        period_b_end: periodBEnd,
      });

      if (!created) {
        throw new Error("Unable to generate comparison report");
      }

      return created;
    },
    onSuccess: (created) => {
      setReportId(created.report_id);
      setResult(null);
      setError("");
    },
  });

  useQuery({
    queryKey: ["comparison-report-status", reportId],
    queryFn: async () => {
      const status = await getReportStatus(reportId, "SH00000001");
      if (!status) {
        throw new Error("Unable to load comparison status");
      }

      if (status.status === "completed") {
        const reportResult = await getReportResult(reportId, "SH00000001");
        setResult(reportResult);
      }

      return status;
    },
    enabled: Boolean(reportId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "completed" || status === "failed" ? false : 3000;
    },
  });

  if (devicesQuery.isLoading) {
    return (
      <View style={styles.screen}>
        <View style={styles.content}>
          <SkeletonBox height={200} borderRadius={14} />
        </View>
      </View>
    );
  }

  if (devicesQuery.isError) {
    return (
      <View style={styles.screen}>
        <ErrorState message="Unable to load compare form" onRetry={() => void devicesQuery.refetch()} />
      </View>
    );
  }

  const devices = devicesQuery.data ?? [];

  if (devices.length === 0) {
    return (
      <View style={styles.screen}>
        <EmptyState message="No machines available for comparison" icon="cpu" />
      </View>
    );
  }

  function submit() {
    if (!deviceId) {
      setError("Select a machine");
      return;
    }

    if (!periodAStart || !periodAEnd || !periodBStart || !periodBEnd) {
      setError("Enter both periods");
      return;
    }

    setError("");
    createMutation.mutate();
  }

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      <Text style={styles.title}>Compare Reports</Text>

      <View style={styles.card}>
        <Text style={styles.label}>Device</Text>
        <View style={styles.chipRow}>
          {devices.map((device) => {
            const selected = device.id === deviceId;
            return (
              <Pressable
                key={device.id}
                onPress={() => setDeviceId(device.id)}
                style={[styles.selectChip, selected && styles.selectChipActive]}
              >
                <Text style={[styles.selectChipText, selected && styles.selectChipTextActive]}>{device.name}</Text>
              </Pressable>
            );
          })}
        </View>

        {[
          { label: "Period A Start", value: periodAStart, setter: setPeriodAStart },
          { label: "Period A End", value: periodAEnd, setter: setPeriodAEnd },
          { label: "Period B Start", value: periodBStart, setter: setPeriodBStart },
          { label: "Period B End", value: periodBEnd, setter: setPeriodBEnd },
        ].map((field) => (
          <View key={field.label} style={styles.inputGroup}>
            <Text style={styles.label}>{field.label}</Text>
            <TextInput
              value={field.value}
              onChangeText={field.setter}
              placeholder="YYYY-MM-DD"
              placeholderTextColor={colors.textSecondary}
              style={styles.input}
            />
          </View>
        ))}
      </View>

      {error ? <Text style={styles.errorText}>{error}</Text> : null}

      <Pressable style={styles.primaryButton} onPress={submit}>
        <Text style={styles.primaryButtonText}>
          {createMutation.isPending ? "Generating..." : "Generate Comparison"}
        </Text>
      </Pressable>

      {createMutation.isError ? (
        <ErrorState message="Unable to generate comparison report" onRetry={() => createMutation.reset()} />
      ) : null}

      {reportId ? (
        <View style={styles.card}>
          <Text style={styles.resultTitle}>Comparison Report: {reportId}</Text>
          <Text style={styles.resultText}>{result ? JSON.stringify(result, null, 2) : "Processing comparison..."}</Text>
        </View>
      ) : null}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.background },
  content: { padding: 16, gap: 14, paddingBottom: 24 },
  title: { color: colors.textPrimary, fontSize: 24, fontWeight: "700" },
  card: {
    backgroundColor: colors.card,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 14,
    padding: 16,
    gap: 10,
  },
  inputGroup: { gap: 8 },
  label: { color: colors.textPrimary, fontSize: 13, fontWeight: "700" },
  input: {
    backgroundColor: colors.background,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 12,
    color: colors.textPrimary,
    paddingHorizontal: 14,
    paddingVertical: 12,
    fontSize: 14,
  },
  chipRow: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  selectChip: {
    backgroundColor: colors.background,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 999,
    paddingHorizontal: 12,
    paddingVertical: 8,
  },
  selectChipActive: { backgroundColor: colors.primary, borderColor: colors.primary },
  selectChipText: { color: colors.textSecondary, fontSize: 12, fontWeight: "600" },
  selectChipTextActive: { color: colors.textPrimary },
  primaryButton: {
    backgroundColor: colors.primary,
    borderRadius: 12,
    paddingVertical: 14,
    alignItems: "center",
  },
  primaryButtonText: { color: colors.textPrimary, fontSize: 15, fontWeight: "700" },
  resultTitle: { color: colors.textPrimary, fontSize: 16, fontWeight: "700" },
  resultText: { color: colors.textSecondary, fontSize: 13 },
  errorText: { color: colors.error, fontSize: 13, fontWeight: "600" },
});
