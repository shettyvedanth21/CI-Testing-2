import { useState } from "react";
import { Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { useRouter } from "expo-router";
import { Feather } from "@expo/vector-icons";

import { colors } from "../src/constants/colors";
import { useUserStore } from "../src/store/user";

const ROLES = [
  { label: "Owner", value: "Owner", icon: "briefcase", subtitle: "Costs & reports" },
  { label: "Supervisor", value: "Supervisor", icon: "monitor", subtitle: "OEE & shifts" },
  { label: "Operator", value: "Operator", icon: "tool", subtitle: "Machines & downtime" },
  { label: "Technician", value: "Technician", icon: "activity", subtitle: "Health & analytics" },
] as const;

export default function RoleSelectScreen() {
  const router = useRouter();
  const setUser = useUserStore((state) => state.setUser);
  const [name, setName] = useState("");
  const [selectedRole, setSelectedRole] = useState<string>("");
  const [error, setError] = useState<string>("");

  const handleSelect = async (role: string) => {
    const trimmed = name.trim();
    if (!trimmed) {
      setError("Please enter your name first.");
      return;
    }

    setError("");
    setSelectedRole(role);
    await setUser(trimmed, role);
    router.replace("/");
  };

  return (
    <View style={styles.screen}>
      <View style={styles.inner}>
        <Text style={styles.title}>Shivex</Text>
        <Text style={styles.subtitle}>Select your role to continue</Text>

        <View style={styles.form}>
          <TextInput
            value={name}
            onChangeText={(value) => {
              setName(value);
              if (error) setError("");
            }}
            placeholder="Your name"
            placeholderTextColor={colors.textSecondary}
            style={styles.input}
          />
          {error ? <Text style={styles.errorText}>{error}</Text> : null}
        </View>

        <View style={styles.grid}>
          {ROLES.map((role) => {
            const active = selectedRole === role.value;
            return (
              <Pressable
                key={role.value}
                onPress={() => void handleSelect(role.value)}
                style={({ pressed }) => [
                  styles.card,
                  active && styles.cardActive,
                  pressed && styles.cardPressed,
                ]}
              >
                <Feather
                  name={role.icon as never}
                  size={22}
                  color={active ? colors.primary : colors.textPrimary}
                />
                <Text style={styles.cardTitle}>{role.label}</Text>
                <Text style={styles.cardSubtitle}>{role.subtitle}</Text>
              </Pressable>
            );
          })}
        </View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: colors.background,
  },
  inner: {
    flex: 1,
    paddingHorizontal: 20,
    paddingTop: 72,
  },
  title: {
    color: colors.textPrimary,
    fontSize: 42,
    fontWeight: "800",
    textAlign: "center",
    letterSpacing: 0.5,
  },
  subtitle: {
    color: colors.textSecondary,
    fontSize: 16,
    textAlign: "center",
    marginTop: 10,
    marginBottom: 28,
  },
  form: {
    marginBottom: 22,
  },
  input: {
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 12,
    backgroundColor: colors.card,
    color: colors.textPrimary,
    paddingHorizontal: 16,
    paddingVertical: 14,
    fontSize: 16,
  },
  errorText: {
    color: colors.error,
    marginTop: 8,
    fontSize: 13,
  },
  grid: {
    flexDirection: "row",
    flexWrap: "wrap",
    justifyContent: "space-between",
    gap: 12,
  },
  card: {
    width: "48%",
    minHeight: 128,
    backgroundColor: colors.card,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 12,
    padding: 16,
    justifyContent: "space-between",
  },
  cardActive: {
    borderColor: colors.primary,
    backgroundColor: "#11182a",
  },
  cardPressed: {
    opacity: 0.85,
  },
  cardTitle: {
    color: colors.textPrimary,
    fontSize: 18,
    fontWeight: "700",
    marginTop: 14,
  },
  cardSubtitle: {
    color: colors.textSecondary,
    fontSize: 13,
    marginTop: 6,
  },
});
