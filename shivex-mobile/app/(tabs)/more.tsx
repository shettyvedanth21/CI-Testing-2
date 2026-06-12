import { Feather } from "@expo/vector-icons";
import { useRouter } from "expo-router";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";

import { colors } from "../../src/constants/colors";

const ITEMS = [
  { label: "Rules", href: "/rules" },
  { label: "Waste Analysis", href: "/waste" },
  { label: "Copilot", href: "/copilot" },
  { label: "Settings", href: "/settings" },
];

export default function MoreTab() {
  const router = useRouter();

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      {ITEMS.map((item) => (
        <Pressable
          key={item.href}
          onPress={() => router.push(item.href)}
          style={({ pressed }) => [styles.row, pressed && styles.rowPressed]}
        >
          <Text style={styles.label}>{item.label}</Text>
          <Feather name="chevron-right" size={20} color={colors.textSecondary} />
        </Pressable>
      ))}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: colors.background,
  },
  content: {
    padding: 20,
    gap: 12,
  },
  row: {
    width: "100%",
    backgroundColor: colors.card,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 12,
    paddingHorizontal: 16,
    paddingVertical: 18,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  rowPressed: {
    opacity: 0.85,
  },
  label: {
    color: colors.textPrimary,
    fontSize: 16,
    fontWeight: "600",
  },
});
