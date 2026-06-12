import { useCallback } from "react";
import { FlatList, StyleSheet, Text, View } from "react-native";
import { useFocusEffect } from "@react-navigation/native";
import { useLocalSearchParams } from "expo-router";
import { useQuery } from "@tanstack/react-query";

import { getTelemetry } from "../../../src/api/devices";
import { colors } from "../../../src/constants/colors";
import { EmptyState } from "../../../src/components/EmptyState";
import { ErrorState } from "../../../src/components/ErrorState";
import { SkeletonBox } from "../../../src/components/SkeletonBox";
import { formatCompactDate, formatValue } from "../../../src/utils/format";

export default function MachineTelemetryScreen() {
  const params = useLocalSearchParams<{ deviceId: string }>();
  const deviceId = Array.isArray(params.deviceId) ? params.deviceId[0] : params.deviceId;
  const telemetryQuery = useQuery({
    queryKey: ["telemetry", deviceId, "list"],
    queryFn: async () => {
      const result = await getTelemetry(deviceId, 2);
      if (result === null) {
        throw new Error("Unable to load telemetry");
      }
      return result.slice().reverse();
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
          {Array.from({ length: 8 }).map((_, index) => (
            <SkeletonBox key={index} height={64} borderRadius={12} />
          ))}
        </View>
      </View>
    );
  }

  if (telemetryQuery.isError) {
    return <View style={styles.screen}><ErrorState message="Cannot connect to Shivex backend\nhttp://192.168.1.3" onRetry={() => void telemetryQuery.refetch()} /></View>;
  }

  if ((telemetryQuery.data ?? []).length === 0) {
    return (
      <View style={styles.screen}>
        <EmptyState message="No telemetry points available" icon="activity" />
      </View>
    );
  }

  return (
    <FlatList
      style={styles.screen}
      contentContainerStyle={styles.content}
      data={telemetryQuery.data}
      keyExtractor={(item) => item.timestamp}
      renderItem={({ item }) => (
        <View style={styles.card}>
          <Text style={styles.timestamp}>{formatCompactDate(item.timestamp)}</Text>
          <Text style={styles.reading}>Current: {formatValue(item.current, "A")}</Text>
          <Text style={styles.reading}>Voltage: {formatValue(item.voltage, "V")}</Text>
          <Text style={styles.reading}>Power: {formatValue(item.power, "kW")}</Text>
        </View>
      )}
    />
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.background },
  content: { padding: 16, gap: 12, paddingBottom: 24 },
  card: { backgroundColor: colors.card, borderColor: colors.border, borderWidth: 1, borderRadius: 12, padding: 14, gap: 6 },
  timestamp: { color: colors.textPrimary, fontSize: 14, fontWeight: "700" },
  reading: { color: colors.textSecondary, fontSize: 13 },
});
