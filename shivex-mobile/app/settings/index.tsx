import { useCallback, useEffect, useState } from "react";
import * as SecureStore from "expo-secure-store";
import { useQuery } from "@tanstack/react-query";
import { useFocusEffect } from "@react-navigation/native";
import { router } from "expo-router";
import {
  Pressable,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  View,
} from "react-native";

import { mobileAuthApi } from "../../src/api/authApi";
import { API_CONFIG } from "../../src/constants/api";
import { buildServiceUrl, getBaseHost, readJson } from "../../src/api/base";
import { getDevices } from "../../src/api/devices";
import { getRules } from "../../src/api/rules";
import { colors } from "../../src/constants/colors";
import { ErrorState } from "../../src/components/ErrorState";
import { SkeletonBox } from "../../src/components/SkeletonBox";
import { useUserStore } from "../../src/store/useUserStore";

const NOTIFICATION_KEYS = {
  high: "shivex_notify_high",
  medium: "shivex_notify_medium",
  low: "shivex_notify_low",
  analytics: "shivex_notify_analytics",
} as const;

type NotificationPrefs = {
  high: boolean;
  medium: boolean;
  low: boolean;
  analytics: boolean;
};

type TariffResponse = {
  rate: number | null;
  currency: string;
};

export default function SettingsScreen() {
  const userName = useUserStore((state) => state.userName);
  const userRole = useUserStore((state) => state.userRole);
  const setUser = useUserStore((state) => state.setUser);
  const clearAuth = useUserStore((state) => state.clearAuth);

  const [editableName, setEditableName] = useState(userName ?? "");
  const [prefs, setPrefs] = useState<NotificationPrefs>({
    high: true,
    medium: true,
    low: false,
    analytics: false,
  });
  const [connectionStatus, setConnectionStatus] = useState<"" | "success" | "error">("");

  const platformQuery = useQuery({
    queryKey: ["settings-platform"],
    queryFn: async () => {
      const [tariff, devices, rules] = await Promise.all([
        readJson<TariffResponse>(buildServiceUrl(8085, "/api/v1/settings/tariff")),
        getDevices(),
        getRules(),
      ]);

      if (!tariff || !devices || !rules) {
        throw new Error("Unable to load settings");
      }

      return {
        tariff,
        deviceCount: devices.length,
        activeRulesCount: rules.data.filter((item) => item.status === "active").length,
      };
    },
  });

  useFocusEffect(
    useCallback(() => {
      void platformQuery.refetch();
    }, [platformQuery])
  );

  useEffect(() => {
    setEditableName(userName ?? "");
  }, [userName]);

  useEffect(() => {
    async function loadPrefs() {
      const entries = await Promise.all([
        SecureStore.getItemAsync(NOTIFICATION_KEYS.high),
        SecureStore.getItemAsync(NOTIFICATION_KEYS.medium),
        SecureStore.getItemAsync(NOTIFICATION_KEYS.low),
        SecureStore.getItemAsync(NOTIFICATION_KEYS.analytics),
      ]);

      setPrefs({
        high: entries[0] !== "false",
        medium: entries[1] !== "false",
        low: entries[2] === "true",
        analytics: entries[3] === "true",
      });
    }

    void loadPrefs();
  }, []);

  async function savePref(key: keyof NotificationPrefs, value: boolean) {
    const next = { ...prefs, [key]: value };
    setPrefs(next);
    await SecureStore.setItemAsync(NOTIFICATION_KEYS[key], String(value));
  }

  async function testConnection() {
    const result = await readJson<{ status?: string }>(`${getBaseHost()}:8000/health`);
    setConnectionStatus(result?.status === "healthy" ? "success" : "error");
    setTimeout(() => setConnectionStatus(""), 3000);
  }

  if (platformQuery.isError) {
    return (
      <View style={styles.screen}>
        <ErrorState message="Unable to load settings" onRetry={() => void platformQuery.refetch()} />
      </View>
    );
  }

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      <Text style={styles.title}>Settings</Text>

      <View style={styles.card}>
        <Text style={styles.sectionTitle}>Your Profile</Text>
        <Text style={styles.label}>Name</Text>
        <TextInput
          value={editableName}
          onChangeText={setEditableName}
          onBlur={() => void setUser(editableName, userRole ?? "")}
          placeholder="Your name"
          placeholderTextColor={colors.textSecondary}
          style={styles.input}
        />
        <Text style={styles.label}>Role</Text>
        <Text style={styles.detailText}>{userRole}</Text>
        <Pressable
          onPress={async () => {
            await mobileAuthApi.logout();
            clearAuth();
            router.replace("/login");
          }}
        >
          <Text style={styles.actionText}>Logout</Text>
        </Pressable>
      </View>

      <View style={styles.card}>
        <Text style={styles.sectionTitle}>Platform Info</Text>
        {platformQuery.isLoading || !platformQuery.data ? (
          <>
            <SkeletonBox height={18} />
            <SkeletonBox height={18} />
            <SkeletonBox height={18} />
          </>
        ) : (
          <>
            <Text style={styles.detailText}>
              Tariff: {platformQuery.data.tariff.rate ?? "--"} {platformQuery.data.tariff.currency}
            </Text>
            <Text style={styles.detailText}>Device Count: {platformQuery.data.deviceCount}</Text>
            <Text style={styles.detailText}>Active Rules Count: {platformQuery.data.activeRulesCount}</Text>
          </>
        )}
        <Text style={styles.noteText}>Edit on web platform</Text>
      </View>

      <View style={styles.card}>
        <Text style={styles.sectionTitle}>Notifications</Text>
        {[
          { key: "high", label: "High", locked: true },
          { key: "medium", label: "Medium" },
          { key: "low", label: "Low" },
          { key: "analytics", label: "Analytics" },
        ].map((item) => (
          <View key={item.key} style={styles.switchRow}>
            <Text style={styles.detailText}>{item.label}</Text>
            <Switch
              value={prefs[item.key as keyof NotificationPrefs]}
              onValueChange={(value) => {
                if (!item.locked) {
                  void savePref(item.key as keyof NotificationPrefs, value);
                }
              }}
              disabled={item.locked}
              trackColor={{ true: colors.primary, false: colors.border }}
              thumbColor={colors.textPrimary}
            />
          </View>
        ))}
      </View>

      <View style={styles.card}>
        <Text style={styles.sectionTitle}>Connection</Text>
        <Text style={styles.detailText}>{API_CONFIG.DEVICE_SERVICE.replace(/:\d+$/, "")}</Text>
        <Pressable style={styles.primaryButton} onPress={() => void testConnection()}>
          <Text style={styles.primaryButtonText}>Test Connection</Text>
        </Pressable>
        {connectionStatus === "success" ? <Text style={styles.successText}>✓ Connected</Text> : null}
        {connectionStatus === "error" ? <Text style={styles.errorText}>✗ Not Connected</Text> : null}
      </View>

      <View style={styles.card}>
        <Text style={styles.sectionTitle}>App Info</Text>
        <Text style={styles.detailText}>Shivex</Text>
        <Text style={styles.detailText}>Version 1.0.0</Text>
        <Text style={styles.detailText}>Local Development Build</Text>
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.background },
  content: { padding: 16, gap: 14, paddingBottom: 24 },
  title: { color: colors.textPrimary, fontSize: 24, fontWeight: "700" },
  card: {
    backgroundColor: colors.card,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 14,
    padding: 16,
    gap: 10,
  },
  sectionTitle: { color: colors.textPrimary, fontSize: 18, fontWeight: "700" },
  label: { color: colors.textPrimary, fontSize: 13, fontWeight: "700" },
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
  detailText: { color: colors.textSecondary, fontSize: 14 },
  actionText: { color: colors.primary, fontSize: 14, fontWeight: "700" },
  noteText: { color: colors.textSecondary, fontSize: 12 },
  switchRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  primaryButton: {
    backgroundColor: colors.primary,
    borderRadius: 12,
    paddingVertical: 12,
    alignItems: "center",
  },
  primaryButtonText: { color: colors.textPrimary, fontSize: 14, fontWeight: "700" },
  successText: { color: colors.success, fontSize: 14, fontWeight: "700" },
  errorText: { color: colors.error, fontSize: 14, fontWeight: "700" },
});
