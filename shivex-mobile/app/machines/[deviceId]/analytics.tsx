import { useCallback } from "react";
import { StyleSheet, Text, View } from "react-native";
import { useFocusEffect } from "@react-navigation/native";
import { useQuery } from "@tanstack/react-query";
import { useLocalSearchParams } from "expo-router";

import { getTelemetry } from "../../../src/api/devices";
import { colors } from "../../../src/constants/colors";
import { EmptyState } from "../../../src/components/EmptyState";
import { ErrorState } from "../../../src/components/ErrorState";
import { SkeletonBox } from "../../../src/components/SkeletonBox";
import { formatValue } from "../../../src/utils/format";

export default function MachineAnalyticsScreen() {
  const params = useLocalSearchParams<{ deviceId: string }>();
  const deviceId = Array.isArray(params.deviceId) ? params.deviceId[0] : params.deviceId;

  const telemetryQuery = useQuery({
    queryKey: ["telemetry", deviceId, "analytics"],
    queryFn: async () => {
      const result = await getTelemetry(deviceId, 2);
      if (result === null) {
        throw new Error("Unable to load telemetry");
      }
      return result;
    },
    enabled: Boolean(deviceId),
    refetchInterval: 30000,
  });

  useFocusEffect(
    useCallback(() => {
      void telemetryQuery.refetch();
    }, [telemetryQuery])
  );

  if (telemetryQuery.isLoading) {
    return (
      <View style={styles.screen}>
        <View style={styles.card}>
          <SkeletonBox height={88} borderRadius={14} />
          <SkeletonBox height={88} borderRadius={14} />
          <SkeletonBox height={88} borderRadius={14} />
          <SkeletonBox height={88} borderRadius={14} />
        </View>
      </View>
    );
  }

  if (telemetryQuery.isError) {
    return (
      <View style={styles.screen}>
        <ErrorState message="Cannot connect to Shivex backend\nhttp://192.168.1.3" onRetry={() => void telemetryQuery.refetch()} />
      </View>
    );
  }

  const points = telemetryQuery.data ?? [];

  if (points.length === 0) {
    return (
      <View style={styles.screen}>
        <EmptyState message="No telemetry points available" icon="activity" />
      </View>
    );
  }

  const avgCurrent = points.reduce((sum, item) => sum + (item.current ?? 0), 0) / points.length;
  const peakVoltage = Math.max(...points.map((item) => item.voltage ?? 0));
  const peakPower = Math.max(...points.map((item) => item.power ?? 0));
  const latestEnergy = points[points.length - 1]?.energy ?? 0;

  return (
    <View style={styles.screen}>
      <View style={styles.card}>
        <Text style={styles.title}>Analytics</Text>
        <Text style={styles.text}>Machine {deviceId}</Text>
      </View>
      <View style={styles.grid}>
        <View style={styles.statCard}>
          <Text style={styles.statLabel}>Avg Current</Text>
          <Text style={styles.statValue}>{formatValue(avgCurrent, "A")}</Text>
        </View>
        <View style={styles.statCard}>
          <Text style={styles.statLabel}>Peak Voltage</Text>
          <Text style={styles.statValue}>{formatValue(peakVoltage, "V")}</Text>
        </View>
        <View style={styles.statCard}>
          <Text style={styles.statLabel}>Peak Power</Text>
          <Text style={styles.statValue}>{formatValue(peakPower, "kW")}</Text>
        </View>
        <View style={styles.statCard}>
          <Text style={styles.statLabel}>Latest Energy</Text>
          <Text style={styles.statValue}>{formatValue(latestEnergy, "kWh")}</Text>
        </View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.background, padding: 16 },
  card: { backgroundColor: colors.card, borderColor: colors.border, borderWidth: 1, borderRadius: 14, padding: 16, gap: 8 },
  title: { color: colors.textPrimary, fontSize: 18, fontWeight: "700" },
  text: { color: colors.textSecondary, fontSize: 14 },
  grid: { flexDirection: "row", flexWrap: "wrap", gap: 12, marginTop: 12 },
  statCard: {
    width: "47%",
    backgroundColor: colors.card,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 14,
    padding: 14,
    gap: 8,
  },
  statLabel: { color: colors.textSecondary, fontSize: 12 },
  statValue: { color: colors.textPrimary, fontSize: 18, fontWeight: "700" },
});
