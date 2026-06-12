import { useCallback, useEffect, useState } from "react";
import {
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { useLocalSearchParams } from "expo-router";
import { useFocusEffect } from "@react-navigation/native";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { acknowledgeAlert, getAlert } from "../../src/api/alerts";
import { colors } from "../../src/constants/colors";
import { useUserStore } from "../../src/store/useUserStore";
import { ErrorState } from "../../src/components/ErrorState";
import { SkeletonBox } from "../../src/components/SkeletonBox";
import { formatTimeAgo, formatValue } from "../../src/utils/format";
import { getSeverityColor, getSeverityEmoji } from "../../src/utils/status";

export default function AlertDetailScreen() {
  const params = useLocalSearchParams<{ alertId: string }>();
  const alertId = Array.isArray(params.alertId) ? params.alertId[0] : params.alertId;
  const userName = useUserStore((state) => state.userName);
  const queryClient = useQueryClient();
  const [ackName, setAckName] = useState(userName ?? "");
  const [note, setNote] = useState("");
  const [submitted, setSubmitted] = useState(false);

  const alertQuery = useQuery({
    queryKey: ["alert", alertId],
    queryFn: async () => {
      const result = await getAlert(alertId);
      if (result === null) {
        throw new Error("Unable to load alert");
      }
      return result;
    },
    enabled: Boolean(alertId),
  });

  useEffect(() => {
    setAckName(userName ?? "");
  }, [userName]);

  useFocusEffect(
    useCallback(() => {
      void alertQuery.refetch();
    }, [alertQuery])
  );

  const acknowledgeMutation = useMutation({
    mutationFn: () => acknowledgeAlert(alertId, ackName || userName || "Shivex User", note),
    onSuccess: async () => {
      setSubmitted(true);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["alerts"] }),
        queryClient.invalidateQueries({ queryKey: ["alert", alertId] }),
      ]);
    },
  });

  if (alertQuery.isLoading) {
    return (
      <View style={styles.screen}>
        <View style={styles.content}>
          <SkeletonBox height={180} borderRadius={14} />
          <SkeletonBox height={180} borderRadius={14} />
        </View>
      </View>
    );
  }

  if (alertQuery.isError || !alertQuery.data) {
    return (
      <View style={styles.screen}>
        <ErrorState message="Cannot connect to Shivex backend\nhttp://192.168.1.3" onRetry={() => void alertQuery.refetch()} />
      </View>
    );
  }

  const alert = alertQuery.data;

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      <View style={[styles.heroCard, { borderLeftColor: getSeverityColor(alert.severity) }]}>
        <Text style={styles.heroTitle}>
          {getSeverityEmoji(alert.severity)} {alert.title}
        </Text>
        <Text style={styles.heroText}>Machine: {alert.machineName}</Text>
        <Text style={styles.heroText}>Rule: {alert.ruleName}</Text>
        <Text style={styles.heroText}>Triggered: {formatTimeAgo(alert.triggeredAt)}</Text>
        <Text style={styles.heroText}>Value: {formatValue(alert.triggeredValue, "")}</Text>
        <Text style={styles.heroText}>Message: {alert.message}</Text>
      </View>

      {alert.status === "acknowledged" || submitted ? (
        <View style={styles.successCard}>
          <Text style={styles.successText}>Alert acknowledged successfully.</Text>
          <Text style={styles.heroText}>Acknowledged by {alert.acknowledgedBy || ackName}</Text>
        </View>
      ) : (
        <View style={styles.formCard}>
          <Text style={styles.sectionTitle}>Acknowledge Alert</Text>
          <TextInput
            value={ackName}
            onChangeText={setAckName}
            placeholder="Your name"
            placeholderTextColor={colors.textSecondary}
            style={styles.input}
          />
          <TextInput
            value={note}
            onChangeText={setNote}
            placeholder="Optional note"
            placeholderTextColor={colors.textSecondary}
            style={[styles.input, styles.noteInput]}
            multiline
          />
          <Pressable onPress={() => acknowledgeMutation.mutate()} style={styles.submitButton}>
            <Text style={styles.submitText}>Submit</Text>
          </Pressable>
        </View>
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.background },
  content: { padding: 16, gap: 16, paddingBottom: 24 },
  heroCard: { backgroundColor: colors.card, borderColor: colors.border, borderWidth: 1, borderLeftWidth: 4, borderRadius: 14, padding: 16, gap: 8 },
  heroTitle: { color: colors.textPrimary, fontSize: 20, fontWeight: "700" },
  heroText: { color: colors.textSecondary, fontSize: 14 },
  formCard: { backgroundColor: colors.card, borderColor: colors.border, borderWidth: 1, borderRadius: 14, padding: 16, gap: 12 },
  sectionTitle: { color: colors.textPrimary, fontSize: 18, fontWeight: "700" },
  input: { backgroundColor: colors.background, borderColor: colors.border, borderWidth: 1, borderRadius: 12, color: colors.textPrimary, paddingHorizontal: 14, paddingVertical: 12, fontSize: 14 },
  noteInput: { minHeight: 96, textAlignVertical: "top" },
  submitButton: { backgroundColor: colors.primary, borderRadius: 10, paddingVertical: 12, alignItems: "center" },
  submitText: { color: colors.textPrimary, fontSize: 14, fontWeight: "700" },
  successCard: { backgroundColor: colors.card, borderColor: colors.success, borderWidth: 1, borderRadius: 14, padding: 16, gap: 8 },
  successText: { color: colors.success, fontSize: 15, fontWeight: "700" },
});
