import { useCallback, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useFocusEffect } from "@react-navigation/native";
import { Linking, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";

import { getDevices } from "../../src/api/devices";
import {
  generateEnergyReport,
  getReportDownloadUrl,
  getReportResult,
  getReportStatus,
} from "../../src/api/reports";
import { colors } from "../../src/constants/colors";
import { EmptyState } from "../../src/components/EmptyState";
import { ErrorState } from "../../src/components/ErrorState";
import { SkeletonBox } from "../../src/components/SkeletonBox";

export default function EnergyReportScreen() {
  const [selectedDeviceIds, setSelectedDeviceIds] = useState<string[]>([]);
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
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
      const created = await generateEnergyReport({
        tenant_id: "SH00000001",
        device_id: selectedDeviceIds.length > 1 ? "ALL" : selectedDeviceIds[0],
        start_date: startDate,
        end_date: endDate,
        report_name: "Energy Consumption Report",
      });

      if (!created) {
        throw new Error("Unable to generate report");
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
    queryKey: ["report-status", reportId],
    queryFn: async () => {
      const status = await getReportStatus(reportId, "SH00000001");
      if (!status) {
        throw new Error("Unable to load report status");
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
        <ErrorState message="Unable to load energy report form" onRetry={() => void devicesQuery.refetch()} />
      </View>
    );
  }

  const devices = devicesQuery.data ?? [];

  if (devices.length === 0) {
    return (
      <View style={styles.screen}>
        <EmptyState message="No machines available for report generation" icon="cpu" />
      </View>
    );
  }

  function submit() {
    if (selectedDeviceIds.length === 0) {
      setError("Select at least one machine");
      return;
    }

    if (!startDate || !endDate) {
      setError("Enter start and end dates");
      return;
    }

    setError("");
    createMutation.mutate();
  }

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      <Text style={styles.title}>Energy Report</Text>

      <View style={styles.card}>
        <Text style={styles.label}>Devices</Text>
        <View style={styles.chipRow}>
          {devices.map((device) => {
            const selected = selectedDeviceIds.includes(device.id);
            return (
              <Pressable
                key={device.id}
                onPress={() =>
                  setSelectedDeviceIds((current) =>
                    current.includes(device.id)
                      ? current.filter((item) => item !== device.id)
                      : [...current, device.id]
                  )
                }
                style={[styles.selectChip, selected && styles.selectChipActive]}
              >
                <Text style={[styles.selectChipText, selected && styles.selectChipTextActive]}>{device.name}</Text>
              </Pressable>
            );
          })}
        </View>

        <Text style={styles.label}>Start Date</Text>
        <TextInput
          value={startDate}
          onChangeText={setStartDate}
          placeholder="YYYY-MM-DD"
          placeholderTextColor={colors.textSecondary}
          style={styles.input}
        />

        <Text style={styles.label}>End Date</Text>
        <TextInput
          value={endDate}
          onChangeText={setEndDate}
          placeholder="YYYY-MM-DD"
          placeholderTextColor={colors.textSecondary}
          style={styles.input}
        />
      </View>

      {error ? <Text style={styles.errorText}>{error}</Text> : null}

      <Pressable
        style={styles.primaryButton}
        onPress={submit}
        disabled={createMutation.isPending}
      >
        <Text style={styles.primaryButtonText}>{createMutation.isPending ? "Generating..." : "Generate"}</Text>
      </Pressable>

      {createMutation.isError ? (
        <ErrorState message="Unable to generate report" onRetry={() => createMutation.reset()} />
      ) : null}

      {reportId ? (
        <View style={styles.card}>
          <Text style={styles.resultTitle}>Report ID: {reportId}</Text>
          {result ? (
            <>
              <Text style={styles.resultText}>{JSON.stringify(result, null, 2)}</Text>
              <Pressable onPress={() => void Linking.openURL(getReportDownloadUrl(reportId, "SH00000001"))}>
                <Text style={styles.downloadText}>Download PDF</Text>
              </Pressable>
            </>
          ) : (
            <Text style={styles.resultText}>Processing report. Polling every 3 seconds...</Text>
          )}
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
  selectChipActive: {
    backgroundColor: colors.primary,
    borderColor: colors.primary,
  },
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
  downloadText: { color: colors.primary, fontSize: 14, fontWeight: "700" },
  errorText: { color: colors.error, fontSize: 13, fontWeight: "600" },
});
