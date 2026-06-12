import { useCallback } from "react";
import {
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { useFocusEffect } from "@react-navigation/native";
import { useLocalSearchParams, router } from "expo-router";
import { useQuery } from "@tanstack/react-query";

import { getAlerts } from "../../../src/api/alerts";
import { getDevice, getHealthScore, getTelemetry } from "../../../src/api/devices";
import { colors } from "../../../src/constants/colors";
import { EmptyState } from "../../../src/components/EmptyState";
import { ErrorState } from "../../../src/components/ErrorState";
import { LineChart } from "../../../src/components/LineChart";
import { SkeletonBox } from "../../../src/components/SkeletonBox";
import { StatusBadge } from "../../../src/components/StatusBadge";
import { formatTimeAgo, formatValue } from "../../../src/utils/format";
import { getSeverityColor } from "../../../src/utils/status";

function MachineDetailScreen() {
  const params = useLocalSearchParams<{ deviceId: string }>();
  const deviceId = Array.isArray(params.deviceId) ? params.deviceId[0] : params.deviceId;

  const deviceQuery = useQuery({
    queryKey: ["device", deviceId],
    queryFn: async () => {
      const result = await getDevice(deviceId);
      if (result === null) {
        throw new Error("Unable to load device");
      }
      return result;
    },
    enabled: Boolean(deviceId),
    refetchInterval: 30000,
  });
  const telemetryQuery = useQuery({
    queryKey: ["telemetry", deviceId],
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
  const healthQuery = useQuery({
    queryKey: ["health-score", deviceId],
    queryFn: () => getHealthScore(deviceId),
    enabled: Boolean(deviceId),
  });
  const alertsQuery = useQuery({
    queryKey: ["alerts", deviceId],
    queryFn: async () => {
      const result = await getAlerts({ device_id: deviceId });
      if (result === null) {
        throw new Error("Unable to load alerts");
      }
      return result;
    },
    enabled: Boolean(deviceId),
    refetchInterval: 30000,
  });

  const onRefresh = useCallback(() => {
    void Promise.all([
      deviceQuery.refetch(),
      telemetryQuery.refetch(),
      healthQuery.refetch(),
      alertsQuery.refetch(),
    ]);
  }, [alertsQuery, deviceQuery, healthQuery, telemetryQuery]);

  useFocusEffect(
    useCallback(() => {
      onRefresh();
    }, [onRefresh])
  );

  if (deviceQuery.isLoading) {
    return (
      <View style={styles.screen}>
        <View style={styles.content}>
          <SkeletonBox height={28} width="60%" />
          <View style={styles.tileGrid}>
            {Array.from({ length: 6 }).map((_, index) => (
              <SkeletonBox key={index} height={88} borderRadius={14} />
            ))}
          </View>
        </View>
      </View>
    );
  }

  if (deviceQuery.isError || telemetryQuery.isError || alertsQuery.isError || !deviceQuery.data) {
    return (
      <View style={styles.screen}>
        <ErrorState message="Cannot connect to Shivex backend\nhttp://192.168.1.3" onRetry={onRefresh} />
      </View>
    );
  }

  const device = deviceQuery.data;
  const latestTelemetry = telemetryQuery.data?.[telemetryQuery.data.length - 1] ?? null;
  const recentAlerts = (alertsQuery.data ?? []).slice(0, 3);
  const chartData = (telemetryQuery.data ?? []).map((item) => ({
    timestamp: new Date(item.timestamp).getTime(),
    current: item.current ?? 0,
  }));

  return (
    <ScrollView
      style={styles.screen}
      contentContainerStyle={styles.content}
      refreshControl={<RefreshControl refreshing={telemetryQuery.isRefetching} onRefresh={onRefresh} tintColor={colors.primary} />}
    >
      <View style={styles.header}>
        <View style={styles.headerMeta}>
          <Text style={styles.title}>{device.name}</Text>
          <Text style={styles.subtitle}>{device.id}</Text>
        </View>
        <StatusBadge status={device.status} />
      </View>

      <View style={styles.tileGrid}>
        {[
          { label: "Current", value: formatValue(latestTelemetry?.current ?? device.current, "A") },
          { label: "Voltage", value: formatValue(latestTelemetry?.voltage ?? device.voltage, "V") },
          { label: "Power", value: formatValue(latestTelemetry?.power ?? device.power, "kW") },
          { label: "Power Factor", value: formatValue(latestTelemetry?.powerFactor ?? device.powerFactor, "", 2) },
          { label: "Energy", value: formatValue(latestTelemetry?.energy ?? device.energy, "kWh") },
          {
            label: "Health Score",
            value:
              healthQuery.isLoading || healthQuery.data === null || healthQuery.data === undefined
                ? "--"
                : `${Math.round(healthQuery.data)}`,
          },
        ].map((item) => (
          <View key={item.label} style={styles.tile}>
            <Text style={styles.tileLabel}>{item.label}</Text>
            {telemetryQuery.isLoading && item.label !== "Health Score" ? (
              <SkeletonBox height={24} width="70%" />
            ) : (
              <Text style={styles.tileValue}>{item.value}</Text>
            )}
          </View>
        ))}
      </View>

      <View style={styles.chartCard}>
        <Text style={styles.sectionTitle}>Current - Last 2 Hours</Text>
        {telemetryQuery.isLoading ? (
          <SkeletonBox height={180} borderRadius={12} />
        ) : chartData.length === 0 ? (
          <EmptyState message="No telemetry points available" icon="activity" />
        ) : (
          <LineChart
            data={chartData.map((item) => item.current)}
            height={180}
            strokeColor={colors.primary}
          />
        )}
      </View>

      <View style={styles.sectionCard}>
        <Text style={styles.sectionTitle}>Recent Alerts</Text>
        {recentAlerts.length === 0 ? (
          <EmptyState message="No recent alerts for this machine" icon="shield" />
        ) : (
          recentAlerts.map((item) => (
            <Pressable key={item.id} style={styles.alertRow} onPress={() => router.push(`/alerts/${item.id}`)}>
              <View style={[styles.alertDot, { backgroundColor: getSeverityColor(item.severity) }]} />
              <View style={styles.alertMeta}>
                <Text style={styles.alertTitle}>{item.ruleName}</Text>
                <Text style={styles.alertTime}>{formatTimeAgo(item.triggeredAt)}</Text>
              </View>
            </Pressable>
          ))
        )}
      </View>

      <View style={styles.linkRow}>
        {[
          { label: "Charts", href: `/machines/${device.id}/charts` },
          { label: "Telemetry", href: `/machines/${device.id}/telemetry` },
          { label: "Analytics", href: `/machines/${device.id}/analytics` },
        ].map((item) => (
          <Pressable key={item.label} style={styles.linkCard} onPress={() => router.push(item.href)}>
            <Text style={styles.linkLabel}>{item.label}</Text>
            <Text style={styles.linkHint}>Open</Text>
          </Pressable>
        ))}
      </View>
    </ScrollView>
  );
}

export default MachineDetailScreen;

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: colors.background,
  },
  content: {
    padding: 16,
    gap: 16,
    paddingBottom: 24,
  },
  header: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    gap: 12,
  },
  headerMeta: {
    flex: 1,
    gap: 4,
  },
  title: {
    color: colors.textPrimary,
    fontSize: 28,
    fontWeight: "700",
  },
  subtitle: {
    color: colors.textSecondary,
    fontSize: 13,
  },
  tileGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 12,
  },
  tile: {
    width: "47%",
    backgroundColor: colors.card,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 14,
    padding: 14,
    gap: 8,
  },
  tileLabel: {
    color: colors.textSecondary,
    fontSize: 12,
  },
  tileValue: {
    color: colors.textPrimary,
    fontSize: 20,
    fontWeight: "700",
  },
  chartCard: {
    backgroundColor: colors.card,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 14,
    padding: 14,
    gap: 12,
  },
  chartWrap: {
    height: 180,
  },
  sectionCard: {
    backgroundColor: colors.card,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 14,
    padding: 14,
    gap: 12,
  },
  sectionTitle: {
    color: colors.textPrimary,
    fontSize: 17,
    fontWeight: "700",
  },
  alertRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
  },
  alertDot: {
    width: 10,
    height: 10,
    borderRadius: 99,
  },
  alertMeta: {
    flex: 1,
    flexDirection: "row",
    justifyContent: "space-between",
    gap: 12,
  },
  alertTitle: {
    color: colors.textPrimary,
    fontSize: 14,
    fontWeight: "600",
  },
  alertTime: {
    color: colors.textSecondary,
    fontSize: 12,
  },
  linkRow: {
    flexDirection: "row",
    gap: 10,
  },
  linkCard: {
    flex: 1,
    backgroundColor: colors.card,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 14,
    padding: 14,
    gap: 6,
  },
  linkLabel: {
    color: colors.textPrimary,
    fontSize: 15,
    fontWeight: "700",
  },
  linkHint: {
    color: colors.primary,
    fontSize: 12,
    fontWeight: "600",
  },
});
