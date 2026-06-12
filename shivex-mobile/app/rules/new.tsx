import { useCallback, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "expo-router";
import { useFocusEffect } from "@react-navigation/native";
import {
  Pressable,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  View,
} from "react-native";

import { getDevices } from "../../src/api/devices";
import { createRule, getDeviceFields, toggleRule } from "../../src/api/rules";
import { colors } from "../../src/constants/colors";
import { EmptyState } from "../../src/components/EmptyState";
import { ErrorState } from "../../src/components/ErrorState";
import { SkeletonBox } from "../../src/components/SkeletonBox";

const CONDITION_OPTIONS = [">", ">=", "<", "<=", "==", "!="];
const COOLDOWN_OPTIONS = ["5", "15", "30", "60", "120", "240", "1440", "no_repeat"];

export default function NewRuleScreen() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [ruleName, setRuleName] = useState("");
  const [ruleType, setRuleType] = useState<"threshold" | "time_based">("threshold");
  const [selectedDeviceId, setSelectedDeviceId] = useState("");
  const [property, setProperty] = useState("");
  const [condition, setCondition] = useState(">");
  const [threshold, setThreshold] = useState("");
  const [timeWindowStart, setTimeWindowStart] = useState("20:00");
  const [timeWindowEnd, setTimeWindowEnd] = useState("06:00");
  const [cooldown, setCooldown] = useState("15");
  const [notificationChannels, setNotificationChannels] = useState<string[]>(["email"]);
  const [enabled, setEnabled] = useState(true);
  const [error, setError] = useState("");

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

  const fieldsQuery = useQuery({
    queryKey: ["rule-fields", selectedDeviceId],
    queryFn: async () => {
      const result = await getDeviceFields(selectedDeviceId);
      return result;
    },
    enabled: Boolean(selectedDeviceId),
  });

  useFocusEffect(
    useCallback(() => {
      void devicesQuery.refetch();
      if (selectedDeviceId) {
        void fieldsQuery.refetch();
      }
    }, [devicesQuery, fieldsQuery, selectedDeviceId])
  );

  const createMutation = useMutation({
    mutationFn: async () => {
      const created = await createRule({
        ruleName: ruleName.trim(),
        ruleType,
        scope: "selected_devices",
        property: ruleType === "threshold" ? property : undefined,
        condition: ruleType === "threshold" ? condition : undefined,
        threshold: ruleType === "threshold" ? Number(threshold) : undefined,
        timeWindowStart: ruleType === "time_based" ? timeWindowStart : undefined,
        timeWindowEnd: ruleType === "time_based" ? timeWindowEnd : undefined,
        timezone: "Asia/Kolkata",
        timeCondition: ruleType === "time_based" ? "running_in_window" : undefined,
        notificationChannels,
        cooldownMode: cooldown === "no_repeat" ? "no_repeat" : "interval",
        cooldownMinutes: cooldown === "no_repeat" ? 0 : Number(cooldown),
        deviceIds: [selectedDeviceId],
      });

      if (created && !enabled) {
        await toggleRule(created.id, "paused");
      }

      return created;
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["rules"] });
      router.replace("/rules");
    },
  });

  if (devicesQuery.isLoading) {
    return (
      <View style={styles.screen}>
        <View style={styles.content}>
          <SkeletonBox height={72} borderRadius={14} />
          <SkeletonBox height={72} borderRadius={14} />
          <SkeletonBox height={72} borderRadius={14} />
        </View>
      </View>
    );
  }

  if (devicesQuery.isError) {
    return (
      <View style={styles.screen}>
        <ErrorState message="Unable to load rule form" onRetry={() => void devicesQuery.refetch()} />
      </View>
    );
  }

  const devices = devicesQuery.data ?? [];
  const availableFields = fieldsQuery.data ?? [];

  if (devices.length === 0) {
    return (
      <View style={styles.screen}>
        <EmptyState message="No machines available for rule creation" icon="cpu" />
      </View>
    );
  }

  function toggleChannel(channel: string) {
    setNotificationChannels((current) =>
      current.includes(channel) ? current.filter((item) => item !== channel) : [...current, channel]
    );
  }

  function submit() {
    if (!ruleName.trim()) {
      setError("Rule name is required");
      return;
    }

    if (!selectedDeviceId) {
      setError("Select a device");
      return;
    }

    if (ruleType === "threshold" && (!threshold || Number.isNaN(Number(threshold)))) {
      setError("Threshold must be a valid number");
      return;
    }

    if (notificationChannels.length === 0) {
      setError("Select at least one notification channel");
      return;
    }

    setError("");
    createMutation.mutate();
  }

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      <Text style={styles.title}>Create Rule</Text>

      {error ? <Text style={styles.errorText}>{error}</Text> : null}

      <View style={styles.card}>
        <Text style={styles.label}>Rule Name</Text>
        <TextInput
          value={ruleName}
          onChangeText={setRuleName}
          placeholder="Enter rule name"
          placeholderTextColor={colors.textSecondary}
          style={styles.input}
        />

        <Text style={styles.label}>Device</Text>
        <View style={styles.chipRow}>
          {devices.map((device) => {
            const selected = device.id === selectedDeviceId;
            return (
              <Pressable
                key={device.id}
                onPress={() => {
                  setSelectedDeviceId(device.id);
                  setProperty("");
                }}
                style={[styles.selectChip, selected && styles.selectChipActive]}
              >
                <Text style={[styles.selectChipText, selected && styles.selectChipTextActive]}>{device.name}</Text>
              </Pressable>
            );
          })}
        </View>

        <Text style={styles.label}>Rule Type</Text>
        <View style={styles.chipRow}>
          {[
            { value: "threshold", label: "Threshold Rule" },
            { value: "time_based", label: "Time-Based Rule" },
          ].map((option) => {
            const selected = option.value === ruleType;
            return (
              <Pressable
                key={option.value}
                onPress={() => setRuleType(option.value as "threshold" | "time_based")}
                style={[styles.selectChip, selected && styles.selectChipActive]}
              >
                <Text style={[styles.selectChipText, selected && styles.selectChipTextActive]}>{option.label}</Text>
              </Pressable>
            );
          })}
        </View>

        <Text style={styles.label}>Property</Text>
        {ruleType === "time_based" ? (
          <View style={styles.readOnlyBox}>
            <Text style={styles.readOnlyText}>Power Status (running)</Text>
          </View>
        ) : fieldsQuery.isLoading ? (
          <SkeletonBox height={44} borderRadius={12} />
        ) : (
          <View style={styles.chipRow}>
            {availableFields.map((field) => {
              const selected = field === property;
              return (
                <Pressable
                  key={field}
                  onPress={() => setProperty(field)}
                  style={[styles.selectChip, selected && styles.selectChipActive]}
                >
                  <Text style={[styles.selectChipText, selected && styles.selectChipTextActive]}>{field}</Text>
                </Pressable>
              );
            })}
          </View>
        )}

        {ruleType === "threshold" ? (
          <>
            <Text style={styles.label}>Condition</Text>
            <View style={styles.chipRow}>
              {CONDITION_OPTIONS.map((item) => {
                const selected = item === condition;
                return (
                  <Pressable
                    key={item}
                    onPress={() => setCondition(item)}
                    style={[styles.miniChip, selected && styles.selectChipActive]}
                  >
                    <Text style={[styles.selectChipText, selected && styles.selectChipTextActive]}>{item}</Text>
                  </Pressable>
                );
              })}
            </View>

            <Text style={styles.label}>Threshold</Text>
            <TextInput
              value={threshold}
              onChangeText={setThreshold}
              placeholder="Enter threshold"
              placeholderTextColor={colors.textSecondary}
              keyboardType="numeric"
              style={styles.input}
            />
          </>
        ) : (
          <>
            <Text style={styles.label}>Time Window Start</Text>
            <TextInput
              value={timeWindowStart}
              onChangeText={setTimeWindowStart}
              placeholder="20:00"
              placeholderTextColor={colors.textSecondary}
              style={styles.input}
            />
            <Text style={styles.label}>Time Window End</Text>
            <TextInput
              value={timeWindowEnd}
              onChangeText={setTimeWindowEnd}
              placeholder="06:00"
              placeholderTextColor={colors.textSecondary}
              style={styles.input}
            />
          </>
        )}

        <Text style={styles.label}>Cooldown</Text>
        <View style={styles.chipRow}>
          {COOLDOWN_OPTIONS.map((item) => {
            const selected = item === cooldown;
            return (
              <Pressable
                key={item}
                onPress={() => setCooldown(item)}
                style={[styles.miniChip, selected && styles.selectChipActive]}
              >
                <Text style={[styles.selectChipText, selected && styles.selectChipTextActive]}>{item}</Text>
              </Pressable>
            );
          })}
        </View>

        <Text style={styles.label}>Notification Channels</Text>
        <View style={styles.chipRow}>
          {["email", "whatsapp", "telegram"].map((channel) => {
            const selected = notificationChannels.includes(channel);
            return (
              <Pressable
                key={channel}
                onPress={() => toggleChannel(channel)}
                style={[styles.selectChip, selected && styles.selectChipActive]}
              >
                <Text style={[styles.selectChipText, selected && styles.selectChipTextActive]}>{channel}</Text>
              </Pressable>
            );
          })}
        </View>

        <View style={styles.switchRow}>
          <Text style={styles.label}>Enabled</Text>
          <Switch
            value={enabled}
            onValueChange={setEnabled}
            trackColor={{ true: colors.primary, false: colors.border }}
            thumbColor={colors.textPrimary}
          />
        </View>
      </View>

      <Pressable style={styles.primaryButton} onPress={submit}>
        <Text style={styles.primaryButtonText}>
          {createMutation.isPending ? "Creating..." : "Create Rule"}
        </Text>
      </Pressable>

      {createMutation.isError ? (
        <ErrorState
          message="Unable to create rule"
          onRetry={() => createMutation.reset()}
        />
      ) : null}
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
  title: {
    color: colors.textPrimary,
    fontSize: 24,
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
  label: {
    color: colors.textPrimary,
    fontSize: 13,
    fontWeight: "700",
  },
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
  chipRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
  },
  selectChip: {
    backgroundColor: colors.background,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 999,
    paddingHorizontal: 12,
    paddingVertical: 8,
  },
  miniChip: {
    backgroundColor: colors.background,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 999,
    paddingHorizontal: 10,
    paddingVertical: 8,
  },
  selectChipActive: {
    backgroundColor: colors.primary,
    borderColor: colors.primary,
  },
  selectChipText: {
    color: colors.textSecondary,
    fontSize: 12,
    fontWeight: "600",
  },
  selectChipTextActive: {
    color: colors.textPrimary,
  },
  readOnlyBox: {
    backgroundColor: colors.background,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 12,
    paddingHorizontal: 14,
    paddingVertical: 12,
  },
  readOnlyText: {
    color: colors.textSecondary,
    fontSize: 14,
  },
  switchRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginTop: 6,
  },
  primaryButton: {
    backgroundColor: colors.primary,
    borderRadius: 12,
    paddingVertical: 14,
    alignItems: "center",
  },
  primaryButtonText: {
    color: colors.textPrimary,
    fontSize: 15,
    fontWeight: "700",
  },
  errorText: {
    color: colors.error,
    fontSize: 13,
  },
});
