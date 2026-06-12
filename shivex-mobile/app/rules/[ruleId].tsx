import { useCallback } from "react";
import { useQuery } from "@tanstack/react-query";
import { useLocalSearchParams } from "expo-router";
import { useFocusEffect } from "@react-navigation/native";
import { ScrollView, StyleSheet, Text, View } from "react-native";

import { getRule } from "../../src/api/rules";
import { colors } from "../../src/constants/colors";
import { EmptyState } from "../../src/components/EmptyState";
import { ErrorState } from "../../src/components/ErrorState";
import { SkeletonBox } from "../../src/components/SkeletonBox";

export default function RuleDetailScreen() {
  const params = useLocalSearchParams<{ ruleId: string }>();
  const ruleId = Array.isArray(params.ruleId) ? params.ruleId[0] : params.ruleId;

  const ruleQuery = useQuery({
    queryKey: ["rule", ruleId],
    queryFn: async () => {
      const result = await getRule(ruleId);
      if (!result) {
        throw new Error("Unable to load rule");
      }
      return result;
    },
    enabled: Boolean(ruleId),
  });

  useFocusEffect(
    useCallback(() => {
      void ruleQuery.refetch();
    }, [ruleQuery])
  );

  if (ruleQuery.isLoading) {
    return (
      <View style={styles.screen}>
        <View style={styles.content}>
          <SkeletonBox height={88} borderRadius={14} />
          <SkeletonBox height={180} borderRadius={14} />
        </View>
      </View>
    );
  }

  if (ruleQuery.isError || !ruleQuery.data) {
    return (
      <View style={styles.screen}>
        <EmptyState message="Rule not found" icon="file-text" />
      </View>
    );
  }

  const rule = ruleQuery.data;

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      <View style={styles.banner}>
        <Text style={styles.bannerText}>Edit rules on the web platform</Text>
      </View>

      <View style={styles.card}>
        <Text style={styles.title}>{rule.name}</Text>
        <Text style={styles.detail}>Type: {rule.ruleType}</Text>
        <Text style={styles.detail}>Scope: {rule.scope}</Text>
        <Text style={styles.detail}>Status: {rule.status}</Text>
        <Text style={styles.detail}>Property: {rule.property ?? "Power status"}</Text>
        <Text style={styles.detail}>Condition: {rule.condition ?? rule.timeCondition ?? "--"}</Text>
        <Text style={styles.detail}>Threshold: {rule.threshold ?? "--"}</Text>
        <Text style={styles.detail}>Window: {rule.timeWindowStart ?? "--"} to {rule.timeWindowEnd ?? "--"}</Text>
        <Text style={styles.detail}>Channels: {rule.notificationChannels.join(", ") || "--"}</Text>
        <Text style={styles.detail}>Created: {rule.createdAt}</Text>
      </View>
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
  },
  banner: {
    backgroundColor: colors.warning,
    borderRadius: 12,
    paddingHorizontal: 14,
    paddingVertical: 12,
  },
  bannerText: {
    color: colors.background,
    fontSize: 13,
    fontWeight: "700",
  },
  card: {
    backgroundColor: colors.card,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 14,
    padding: 16,
    gap: 10,
  },
  title: {
    color: colors.textPrimary,
    fontSize: 22,
    fontWeight: "700",
  },
  detail: {
    color: colors.textSecondary,
    fontSize: 14,
  },
});
