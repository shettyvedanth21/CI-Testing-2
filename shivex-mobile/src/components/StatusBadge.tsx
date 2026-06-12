import { StyleSheet, Text, View } from "react-native";

import { colors } from "../constants/colors";

type BadgeStatus = "RUNNING" | "IDLE" | "STOPPED" | "OFFLINE" | string;

const STATUS_COLORS: Record<string, string> = {
  RUNNING: colors.running,
  IDLE: colors.idle,
  STOPPED: colors.stopped,
  OFFLINE: colors.offline,
};

export function StatusBadge({ status }: { status: BadgeStatus }) {
  const normalized = status.toUpperCase();
  const chipColor = STATUS_COLORS[normalized] ?? colors.textSecondary;

  return (
    <View style={[styles.badge, { borderColor: chipColor }]}>
      <Text style={[styles.text, { color: chipColor }]}>{normalized}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  badge: {
    alignSelf: "flex-start",
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 99,
    borderWidth: 1,
    backgroundColor: colors.card,
  },
  text: {
    fontSize: 11,
    fontWeight: "700",
    letterSpacing: 0.2,
  },
});
