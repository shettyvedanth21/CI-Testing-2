import { useCallback, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useFocusEffect } from "@react-navigation/native";
import { Linking, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";

import { getDevices } from "../../src/api/devices";
import {
  getWasteDownloadUrl,
  getWasteJobs,
  getWasteResult,
  getWasteStatus,
  runWasteAnalysis,
} from "../../src/api/waste";
import { colors } from "../../src/constants/colors";
import { EmptyState } from "../../src/components/EmptyState";
import { ErrorState } from "../../src/components/ErrorState";
import { SkeletonBox } from "../../src/components/SkeletonBox";
import { formatCompactDate } from "../../src/utils/format";

type WasteDeviceResult = {
  device_id: string;
  device_name?: string;
  off_hours?: { duration_sec?: number | null; energy_kwh?: number | null; cost?: number | null };
  overconsumption?: { duration_sec?: number | null; energy_kwh?: number | null; cost?: number | null };
};

type WasteResultPayload = {
  total_waste_cost?: number | null;
  device_summaries?: WasteDeviceResult[];
};

function formatDuration(seconds?: number | null) {
  if (!seconds) {
    return "--";
  }

  const hours = seconds / 3600;
  return `${hours.toFixed(1)} hrs`;
}

function formatKwh(value?: number | null) {
  return value == null ? "--" : `${value.toFixed(2)} kWh`;
}

function formatCost(value?: number | null) {
  return value == null ? "--" : `₹${value.toFixed(2)}`;
}

export default function WasteScreen() {
  const [selectedDeviceIds, setSelectedDeviceIds] = useState<string[]>([]);
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [jobId, setJobId] = useState("");
  const [result, setResult] = useState<WasteResultPayload | null>(null);

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

  const jobsQuery = useQuery({
    queryKey: ["waste-jobs"],
    queryFn: async () => {
      const result = await getWasteJobs();
      if (!result) {
        throw new Error("Unable to load waste jobs");
      }
      return result;
    },
  });

  useFocusEffect(
    useCallback(() => {
      void devicesQuery.refetch();
      void jobsQuery.refetch();
    }, [devicesQuery, jobsQuery])
  );

  const runMutation = useMutation({
    mutationFn: async () => {
      const created = await runWasteAnalysis({
        scope: selectedDeviceIds.length === 0 ? "all" : "selected",
        device_ids: selectedDeviceIds.length === 0 ? null : selectedDeviceIds,
        start_date: startDate,
        end_date: endDate,
        granularity: "daily",
      });

      if (!created) {
        throw new Error("Unable to start waste analysis");
      }

      return created;
    },
    onSuccess: async (created) => {
      setJobId(created.job_id);
      setResult(null);
      await jobsQuery.refetch();
    },
  });

  useQuery({
    queryKey: ["waste-status", jobId],
    queryFn: async () => {
      const status = await getWasteStatus(jobId);
      if (!status) {
        throw new Error("Unable to load waste status");
      }

      if (status.status === "completed") {
        const payload = await getWasteResult(jobId);
        setResult((payload as WasteResultPayload | null) ?? null);
        await jobsQuery.refetch();
      }

      return status;
    },
    enabled: Boolean(jobId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "completed" || status === "failed" ? false : 3000;
    },
  });

  if (devicesQuery.isLoading || jobsQuery.isLoading) {
    return (
      <View style={styles.screen}>
        <View style={styles.content}>
          <SkeletonBox height={220} borderRadius={14} />
          <SkeletonBox height={140} borderRadius={14} />
        </View>
      </View>
    );
  }

  if (devicesQuery.isError || jobsQuery.isError) {
    return (
      <View style={styles.screen}>
        <ErrorState
          message="Unable to load waste analysis"
          onRetry={() => {
            void devicesQuery.refetch();
            void jobsQuery.refetch();
          }}
        />
      </View>
    );
  }

  const devices = devicesQuery.data ?? [];
  const jobs = jobsQuery.data ?? [];

  if (devices.length === 0) {
    return (
      <View style={styles.screen}>
        <EmptyState message="No machines available for waste analysis" icon="cpu" />
      </View>
    );
  }

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      <Text style={styles.title}>Waste Analysis</Text>

      <View style={styles.card}>
        <Text style={styles.label}>Run New Analysis</Text>
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
        <TextInput
          value={startDate}
          onChangeText={setStartDate}
          placeholder="Start date YYYY-MM-DD"
          placeholderTextColor={colors.textSecondary}
          style={styles.input}
        />
        <TextInput
          value={endDate}
          onChangeText={setEndDate}
          placeholder="End date YYYY-MM-DD"
          placeholderTextColor={colors.textSecondary}
          style={styles.input}
        />
        <Pressable style={styles.primaryButton} onPress={() => runMutation.mutate()}>
          <Text style={styles.primaryButtonText}>{runMutation.isPending ? "Running..." : "Run New Analysis"}</Text>
        </Pressable>

        {runMutation.isError ? (
          <ErrorState message="Unable to start waste analysis" onRetry={() => runMutation.reset()} />
        ) : null}
      </View>

      {result ? (
        <View style={styles.card}>
          <Text style={styles.sectionTitle}>Result</Text>
          <Text style={styles.resultSummary}>Total Waste Cost: {formatCost(result.total_waste_cost)}</Text>
          {result.device_summaries?.map((device) => {
            const totalCost = (device.off_hours?.cost ?? 0) + (device.overconsumption?.cost ?? 0);

            return (
              <View key={device.device_id} style={styles.resultBlock}>
                <Text style={styles.resultDevice}>{device.device_name ?? device.device_id}</Text>
                <View style={styles.tableHeader}>
                  <Text style={styles.tableHeaderText}>Category</Text>
                  <Text style={styles.tableHeaderText}>Duration</Text>
                  <Text style={styles.tableHeaderText}>kWh</Text>
                  <Text style={styles.tableHeaderText}>Cost</Text>
                </View>
                <View style={styles.tableRow}>
                  <Text style={styles.tableCell}>Idle Running</Text>
                  <Text style={styles.tableCell}>--</Text>
                  <Text style={styles.tableCell}>--</Text>
                  <Text style={styles.tableCell}>--</Text>
                </View>
                <View style={styles.tableRow}>
                  <Text style={styles.tableCell}>Off-Hours</Text>
                  <Text style={styles.tableCell}>{formatDuration(device.off_hours?.duration_sec)}</Text>
                  <Text style={styles.tableCell}>{formatKwh(device.off_hours?.energy_kwh)}</Text>
                  <Text style={styles.tableCell}>{formatCost(device.off_hours?.cost)}</Text>
                </View>
                <View style={styles.tableRow}>
                  <Text style={styles.tableCell}>Overconsumption</Text>
                  <Text style={styles.tableCell}>{formatDuration(device.overconsumption?.duration_sec)}</Text>
                  <Text style={styles.tableCell}>{formatKwh(device.overconsumption?.energy_kwh)}</Text>
                  <Text style={styles.tableCell}>{formatCost(device.overconsumption?.cost)}</Text>
                </View>
                <View style={styles.tableRow}>
                  <Text style={styles.tableCell}>Total</Text>
                  <Text style={styles.tableCell}>--</Text>
                  <Text style={styles.tableCell}>--</Text>
                  <Text style={styles.tableCell}>{formatCost(totalCost)}</Text>
                </View>
              </View>
            );
          })}
          {jobId ? (
            <Pressable onPress={async () => {
              const url = await getWasteDownloadUrl(jobId);
              if (url) {
                await Linking.openURL(url);
              }
            }}>
              <Text style={styles.downloadText}>Download PDF</Text>
            </Pressable>
          ) : null}
        </View>
      ) : null}

      <Text style={styles.sectionTitle}>Recent Jobs</Text>
      {jobs.length === 0 ? (
        <EmptyState message="No waste analysis jobs yet" icon="archive" />
      ) : (
        jobs.map((job) => (
          <View key={job.jobId} style={styles.jobCard}>
            <View>
              <Text style={styles.jobTitle}>{job.jobName ?? job.jobId}</Text>
              <Text style={styles.jobMeta}>{formatCompactDate(job.createdAt)}</Text>
            </View>
            <Text style={styles.jobMeta}>{job.status}</Text>
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
  sectionTitle: { color: colors.textPrimary, fontSize: 18, fontWeight: "700" },
  card: {
    backgroundColor: colors.card,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 14,
    padding: 16,
    gap: 10,
  },
  label: { color: colors.textPrimary, fontSize: 14, fontWeight: "700" },
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
  primaryButton: {
    backgroundColor: colors.primary,
    borderRadius: 12,
    paddingVertical: 14,
    alignItems: "center",
  },
  primaryButtonText: { color: colors.textPrimary, fontSize: 15, fontWeight: "700" },
  resultSummary: { color: colors.textSecondary, fontSize: 14 },
  resultBlock: {
    backgroundColor: colors.background,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 12,
    padding: 12,
    gap: 8,
  },
  resultDevice: { color: colors.textPrimary, fontSize: 15, fontWeight: "700" },
  tableHeader: { flexDirection: "row", justifyContent: "space-between", gap: 8 },
  tableHeaderText: { color: colors.textSecondary, fontSize: 11, fontWeight: "700", flex: 1 },
  tableRow: { flexDirection: "row", justifyContent: "space-between", gap: 8 },
  tableCell: { color: colors.textPrimary, fontSize: 12, flex: 1 },
  downloadText: { color: colors.primary, fontSize: 14, fontWeight: "700" },
  jobCard: {
    backgroundColor: colors.card,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 14,
    padding: 16,
    flexDirection: "row",
    justifyContent: "space-between",
    gap: 12,
  },
  jobTitle: { color: colors.textPrimary, fontSize: 15, fontWeight: "700" },
  jobMeta: { color: colors.textSecondary, fontSize: 12 },
});
