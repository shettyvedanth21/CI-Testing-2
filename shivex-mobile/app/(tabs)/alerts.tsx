import { useMemo, useState } from "react";
import {
  FlatList,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { router } from "expo-router";

import { acknowledgeAlert, getAlerts } from "../../src/api/alerts";
import { colors } from "../../src/constants/colors";
import { useUserStore } from "../../src/store/user";
import { ErrorState } from "../../src/components/ErrorState";
import { EmptyState } from "../../src/components/EmptyState";
import { SkeletonBox } from "../../src/components/SkeletonBox";
import { formatTimeAgo, formatValue } from "../../src/utils/format";
import { getSeverityColor, getSeverityEmoji } from "../../src/utils/status";

const FILTERS = ["All", "Unacknowledged", "High", "Medium", "Low"] as const;

export default function AlertsTab() {
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>("All");
  const userName = useUserStore((state) => state.userName);
  const queryClient = useQueryClient();
  const alertsQuery = useQuery({
    queryKey: ["alerts"],
    queryFn: async () => {
      const result = await getAlerts();
      if (result === null) {
        throw new Error("Unable to load alerts");
      }
      return result;
    },
    refetchInterval: 30000,
  });

  const acknowledgeMutation = useMutation({
    mutationFn: ({ alertId }: { alertId: string }) => acknowledgeAlert(alertId, userName || "Shivex User"),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["alerts"] });
    },
  });

  const filteredAlerts = useMemo(() => {
    return (alertsQuery.data ?? []).filter((item) => {
      if (filter === "Unacknowledged") {
        return item.status !== "acknowledged";
      }

      if (filter !== "All") {
        return item.severity === filter.toUpperCase();
      }

      return true;
    });
  }, [alertsQuery.data, filter]);

  if (alertsQuery.isLoading) {
    return (
      <View style={styles.screen}>
        <View style={styles.content}>
          {Array.from({ length: 5 }).map((_, index) => (
            <SkeletonBox key={index} height={116} borderRadius={12} />
          ))}
        </View>
      </View>
    );
  }

  if (alertsQuery.isError) {
    return (
      <View style={styles.screen}>
        <ErrorState
          message="Cannot connect to Shivex backend\nhttp://192.168.1.3"
          onRetry={() => void alertsQuery.refetch()}
        />
      </View>
    );
  }

  return (
    <FlatList
      style={styles.screen}
      contentContainerStyle={styles.content}
      data={filteredAlerts}
      keyExtractor={(item) => item.id}
      refreshControl={
        <RefreshControl
          refreshing={alertsQuery.isRefetching}
          onRefresh={() => void alertsQuery.refetch()}
          tintColor={colors.primary}
        />
      }
      ListHeaderComponent={
        <View style={styles.filterRow}>
          {FILTERS.map((item) => {
            const active = item === filter;
            return (
              <Pressable
                key={item}
                onPress={() => setFilter(item)}
                style={[styles.filterChip, active && styles.filterChipActive]}
              >
                <Text style={[styles.filterText, active && styles.filterTextActive]}>{item}</Text>
              </Pressable>
            );
          })}
        </View>
      }
      ListEmptyComponent={<EmptyState message="No alerts — all clear ✓" icon="shield" />}
      renderItem={({ item }) => {
        const borderColor = getSeverityColor(item.severity);
        const acknowledged = item.status === "acknowledged";

        return (
          <Pressable
            style={[styles.card, { borderLeftColor: borderColor }]}
            onPress={() => router.push(`/alerts/${item.id}`)}
          >
            <View style={styles.cardHeader}>
              <Text style={styles.cardTitle}>
                {getSeverityEmoji(item.severity)} {item.machineName}
              </Text>
              <Text style={styles.timeText}>{formatTimeAgo(item.triggeredAt)}</Text>
            </View>
            <Text style={styles.ruleText}>{item.ruleName}</Text>
            <Text style={styles.valueText}>Triggered value: {formatValue(item.triggeredValue, "")}</Text>

            {acknowledged ? (
              <Text style={styles.ackText}>✓ Acknowledged by {item.acknowledgedBy || "Unknown"}</Text>
            ) : (
              <Pressable
                onPress={() => acknowledgeMutation.mutate({ alertId: item.id })}
                style={styles.ackButton}
              >
                <Text style={styles.ackButtonText}>Acknowledge</Text>
              </Pressable>
            )}
          </Pressable>
        );
      }}
    />
  );
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: colors.background,
  },
  content: {
    padding: 16,
    gap: 12,
    paddingBottom: 24,
  },
  filterRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
    marginBottom: 4,
  },
  filterChip: {
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 999,
    paddingHorizontal: 12,
    paddingVertical: 8,
    backgroundColor: colors.card,
  },
  filterChipActive: {
    borderColor: colors.primary,
    backgroundColor: colors.primary,
  },
  filterText: {
    color: colors.textSecondary,
    fontSize: 12,
    fontWeight: "600",
  },
  filterTextActive: {
    color: colors.textPrimary,
  },
  card: {
    backgroundColor: colors.card,
    borderRadius: 12,
    borderColor: colors.border,
    borderWidth: 1,
    borderLeftWidth: 4,
    padding: 14,
    gap: 8,
  },
  cardHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    gap: 12,
  },
  cardTitle: {
    color: colors.textPrimary,
    fontSize: 15,
    fontWeight: "700",
    flex: 1,
  },
  timeText: {
    color: colors.textSecondary,
    fontSize: 12,
  },
  ruleText: {
    color: colors.textPrimary,
    fontSize: 14,
  },
  valueText: {
    color: colors.textSecondary,
    fontSize: 13,
  },
  ackButton: {
    alignSelf: "flex-start",
    backgroundColor: colors.primary,
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 10,
  },
  ackButtonText: {
    color: colors.textPrimary,
    fontSize: 13,
    fontWeight: "700",
  },
  ackText: {
    color: colors.success,
    fontSize: 13,
    fontWeight: "600",
  },
});
