import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";
import { useRouter } from "expo-router";
import { useFocusEffect } from "@react-navigation/native";
import {
  Pressable,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  View,
} from "react-native";

import { getRules, toggleRule } from "../../src/api/rules";
import { colors } from "../../src/constants/colors";
import { EmptyState } from "../../src/components/EmptyState";
import { ErrorState } from "../../src/components/ErrorState";
import { SkeletonBox } from "../../src/components/SkeletonBox";

function formatRuleType(ruleType: string) {
  return ruleType === "time_based" ? "TIME-BASED" : "THRESHOLD";
}

function describeRule(rule: {
  ruleType: string;
  property: string | null;
  condition: string | null;
  threshold: number | null;
  timeWindowStart: string | null;
  timeWindowEnd: string | null;
}) {
  if (rule.ruleType === "time_based") {
    return `${rule.timeWindowStart ?? "--"} to ${rule.timeWindowEnd ?? "--"}`;
  }

  return `${rule.property ?? "Metric"} ${rule.condition ?? ""} ${rule.threshold ?? "--"}`.trim();
}

export default function RulesScreen() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const rulesQuery = useQuery({
    queryKey: ["rules"],
    queryFn: async () => {
      const result = await getRules();
      if (!result) {
        throw new Error("Unable to load rules");
      }
      return result;
    },
  });

  useFocusEffect(
    useCallback(() => {
      void rulesQuery.refetch();
    }, [rulesQuery])
  );

  const toggleMutation = useMutation({
    mutationFn: ({ ruleId, status }: { ruleId: string; status: "active" | "paused" }) =>
      toggleRule(ruleId, status),
    onMutate: async ({ ruleId, status }) => {
      await queryClient.cancelQueries({ queryKey: ["rules"] });
      const previous = queryClient.getQueryData<{ data: Array<Record<string, unknown>>; total: number }>(["rules"]);

      queryClient.setQueryData(["rules"], (current: { data: Array<Record<string, unknown>>; total: number } | undefined) => {
        if (!current) {
          return current;
        }

        return {
          ...current,
          data: current.data.map((item) => {
            if (item.id !== ruleId) {
              return item;
            }

            return {
              ...item,
              status,
            };
          }),
        };
      });

      return { previous };
    },
    onError: (_error, _variables, context) => {
      if (context?.previous) {
        queryClient.setQueryData(["rules"], context.previous);
      }
    },
    onSettled: async () => {
      await queryClient.invalidateQueries({ queryKey: ["rules"] });
    },
  });

  if (rulesQuery.isLoading) {
    return (
      <View style={styles.screen}>
        <View style={styles.content}>
          <SkeletonBox height={46} borderRadius={14} />
          <SkeletonBox height={132} borderRadius={14} />
          <SkeletonBox height={132} borderRadius={14} />
          <SkeletonBox height={132} borderRadius={14} />
        </View>
      </View>
    );
  }

  if (rulesQuery.isError) {
    return (
      <View style={styles.screen}>
        <ErrorState message="Unable to load rules" onRetry={() => void rulesQuery.refetch()} />
      </View>
    );
  }

  const rules = rulesQuery.data?.data ?? [];

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      <View style={styles.header}>
        <View>
          <Text style={styles.title}>Rules</Text>
          <Text style={styles.subtitle}>Monitor threshold and time-based alerts</Text>
        </View>
        <Pressable style={styles.primaryButton} onPress={() => router.push("/rules/new")}>
          <Text style={styles.primaryButtonText}>+ New Rule</Text>
        </Pressable>
      </View>

      {rules.length === 0 ? (
        <EmptyState message="No rules created yet" icon="sliders" />
      ) : (
        rules.map((rule) => (
          <Pressable key={rule.id} style={styles.card} onPress={() => router.push(`/rules/${rule.id}`)}>
            <View style={styles.cardHeader}>
              <View style={styles.cardMeta}>
                <Text style={styles.cardTitle}>{rule.name}</Text>
                <View style={styles.typeBadge}>
                  <Text style={styles.typeBadgeText}>{formatRuleType(rule.ruleType)}</Text>
                </View>
              </View>
              <Switch
                value={rule.status === "active"}
                onValueChange={(next) =>
                  toggleMutation.mutate({
                    ruleId: rule.id,
                    status: next ? "active" : "paused",
                  })
                }
                trackColor={{ true: colors.primary, false: colors.border }}
                thumbColor={colors.textPrimary}
              />
            </View>

            <Text style={styles.detailText}>Condition: {describeRule(rule)}</Text>
            <Text style={styles.detailText}>Scope: {rule.scope === "all_devices" ? "All devices" : "Selected devices"}</Text>
            <Text style={styles.detailText}>Severity: Standard</Text>
            <Text style={styles.detailText}>Status: {rule.status}</Text>
          </Pressable>
        ))
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: colors.background,
  },
  content: {
    padding: 16,
    gap: 14,
    paddingBottom: 24,
  },
  header: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    gap: 12,
  },
  title: {
    color: colors.textPrimary,
    fontSize: 24,
    fontWeight: "700",
  },
  subtitle: {
    color: colors.textSecondary,
    fontSize: 13,
    marginTop: 4,
  },
  primaryButton: {
    backgroundColor: colors.primary,
    borderRadius: 12,
    paddingHorizontal: 14,
    paddingVertical: 11,
  },
  primaryButtonText: {
    color: colors.textPrimary,
    fontSize: 13,
    fontWeight: "700",
  },
  card: {
    backgroundColor: colors.card,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 14,
    padding: 16,
    gap: 8,
  },
  cardHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    gap: 12,
  },
  cardMeta: {
    flex: 1,
    gap: 8,
  },
  cardTitle: {
    color: colors.textPrimary,
    fontSize: 16,
    fontWeight: "700",
  },
  typeBadge: {
    alignSelf: "flex-start",
    backgroundColor: colors.background,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 999,
    paddingHorizontal: 10,
    paddingVertical: 4,
  },
  typeBadgeText: {
    color: colors.primary,
    fontSize: 11,
    fontWeight: "700",
  },
  detailText: {
    color: colors.textSecondary,
    fontSize: 13,
  },
});
