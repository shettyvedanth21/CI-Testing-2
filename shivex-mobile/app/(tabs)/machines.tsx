import { useMemo, useState } from "react";
import {
  FlatList,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { router } from "expo-router";
import { useQuery } from "@tanstack/react-query";

import { getDevices } from "../../src/api/devices";
import { colors } from "../../src/constants/colors";
import { ErrorState } from "../../src/components/ErrorState";
import { EmptyState } from "../../src/components/EmptyState";
import { SkeletonBox } from "../../src/components/SkeletonBox";
import { StatusBadge } from "../../src/components/StatusBadge";
import { formatTimeAgo, formatValue } from "../../src/utils/format";
import { getStatusColor } from "../../src/utils/status";

const FILTERS = ["All", "Running", "Idle", "Stopped", "Offline"] as const;

export default function MachinesTab() {
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>("All");
  const devicesQuery = useQuery({
    queryKey: ["devices"],
    queryFn: async () => {
      const result = await getDevices();
      if (result === null) {
        throw new Error("Unable to load devices");
      }
      return result;
    },
    refetchInterval: 30000,
  });

  const filteredData = useMemo(() => {
    const query = search.trim().toLowerCase();

    return (devicesQuery.data ?? []).filter((device) => {
      const matchesSearch = !query || device.name.toLowerCase().includes(query) || device.id.toLowerCase().includes(query);
      const matchesFilter = filter === "All" || device.status === filter.toUpperCase();
      return matchesSearch && matchesFilter;
    });
  }, [devicesQuery.data, filter, search]);

  if (devicesQuery.isLoading) {
    return (
      <View style={styles.screen}>
        <View style={styles.content}>
          {Array.from({ length: 6 }).map((_, index) => (
            <SkeletonBox key={index} height={110} borderRadius={12} />
          ))}
        </View>
      </View>
    );
  }

  if (devicesQuery.isError) {
    return (
      <View style={styles.screen}>
        <ErrorState
          message="Cannot connect to Shivex backend\nhttp://192.168.1.3"
          onRetry={() => void devicesQuery.refetch()}
        />
      </View>
    );
  }

  return (
    <FlatList
      style={styles.screen}
      contentContainerStyle={styles.content}
      data={filteredData}
      keyExtractor={(item) => item.id}
      refreshControl={
        <RefreshControl
          refreshing={devicesQuery.isRefetching}
          onRefresh={() => void devicesQuery.refetch()}
          tintColor={colors.primary}
        />
      }
      ListHeaderComponent={
        <View style={styles.header}>
          <TextInput
            value={search}
            onChangeText={setSearch}
            placeholder="Search machines"
            placeholderTextColor={colors.textSecondary}
            style={styles.searchInput}
          />
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
        </View>
      }
      ListEmptyComponent={<EmptyState message="No machines match your filters" icon="cpu" />}
      renderItem={({ item }) => {
        const healthColor =
          item.healthScore === null
            ? colors.textSecondary
            : item.healthScore >= 70
              ? colors.success
              : item.healthScore >= 50
                ? colors.warning
                : colors.error;

        return (
          <Pressable
            onPress={() => router.push(`/machines/${item.id}`)}
            style={[styles.card, { borderLeftColor: getStatusColor(item.status) }]}
          >
            <View style={styles.cardHeader}>
              <View style={styles.cardMeta}>
                <Text style={styles.cardTitle}>{item.name}</Text>
                <Text style={styles.cardSubtle}>{item.id}</Text>
              </View>
              <StatusBadge status={item.status} />
            </View>

            <View style={styles.cardInfoRow}>
              <Text style={styles.infoLabel}>Current</Text>
              <Text style={styles.infoValue}>{formatValue(item.current, "A")}</Text>
            </View>
            <View style={styles.cardInfoRow}>
              <Text style={styles.infoLabel}>Last seen</Text>
              <Text style={styles.infoValue}>{formatTimeAgo(item.lastSeen)}</Text>
            </View>
            <View style={styles.cardInfoRow}>
              <Text style={styles.infoLabel}>Health score</Text>
              <Text style={[styles.infoValue, { color: healthColor }]}> 
                {item.healthScore === null ? "--" : `${Math.round(item.healthScore)}`}
              </Text>
            </View>
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
  header: {
    gap: 12,
    marginBottom: 4,
  },
  searchInput: {
    backgroundColor: colors.card,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 12,
    color: colors.textPrimary,
    paddingHorizontal: 14,
    paddingVertical: 12,
    fontSize: 14,
  },
  filterRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
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
    gap: 10,
  },
  cardHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    gap: 12,
  },
  cardMeta: {
    flex: 1,
    gap: 4,
  },
  cardTitle: {
    color: colors.textPrimary,
    fontSize: 16,
    fontWeight: "700",
  },
  cardSubtle: {
    color: colors.textSecondary,
    fontSize: 12,
  },
  cardInfoRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    gap: 12,
  },
  infoLabel: {
    color: colors.textSecondary,
    fontSize: 13,
  },
  infoValue: {
    color: colors.textPrimary,
    fontSize: 13,
    fontWeight: "600",
  },
});
