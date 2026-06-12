import { useCallback } from "react";
import { ScrollView, StyleSheet, Text, View } from "react-native";
import { useFocusEffect } from "@react-navigation/native";
import { useLocalSearchParams } from "expo-router";
import { useQuery } from "@tanstack/react-query";

import { getTelemetry } from "../../../src/api/devices";
import { colors } from "../../../src/constants/colors";
import { EmptyState } from "../../../src/components/EmptyState";
import { ErrorState } from "../../../src/components/ErrorState";
import { LineChart } from "../../../src/components/LineChart";
import { SkeletonBox } from "../../../src/components/SkeletonBox";

function MachineChartsScreen() {
  const params = useLocalSearchParams<{ deviceId: string }>();
  const deviceId = Array.isArray(params.deviceId) ? params.deviceId[0] : params.deviceId;
  const telemetryQuery = useQuery({
    queryKey: ["telemetry", deviceId, "charts"],
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
        <View style={styles.content}>
          <SkeletonBox height={220} borderRadius={14} />
          <SkeletonBox height={220} borderRadius={14} />
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

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      {[
        { label: "Current", key: "current" as const },
        { label: "Voltage", key: "voltage" as const },
      ].map((chart) => {
        const data = points.map((item) => ({
          timestamp: new Date(item.timestamp).getTime(),
          value: item[chart.key] ?? 0,
        }));

        return (
          <View key={chart.label} style={styles.card}>
            <Text style={styles.title}>{chart.label}</Text>
            {data.length === 0 ? (
              <EmptyState message="No telemetry points available" icon="activity" />
            ) : (
              <LineChart data={data.map((item) => item.value)} height={220} strokeColor={colors.primary} />
            )}
          </View>
        );
      })}
    </ScrollView>
  );
}

export default MachineChartsScreen;

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.background },
  content: { padding: 16, gap: 16, paddingBottom: 24 },
  card: { backgroundColor: colors.card, borderColor: colors.border, borderWidth: 1, borderRadius: 14, padding: 14, gap: 12 },
  chartWrap: { height: 220 },
  title: { color: colors.textPrimary, fontSize: 17, fontWeight: "700" },
});
