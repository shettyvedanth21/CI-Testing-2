import { useEffect, useState } from "react";
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { useRouter } from "expo-router";

import { mobileAuthApi } from "../src/api/authApi";
import { colors } from "../src/constants/colors";
import { useUserStore } from "../src/store/useUserStore";

export default function LoginScreen() {
  const router = useRouter();
  const isAuthenticated = useUserStore((state) => state.isAuthenticated);
  const setMe = useUserStore((state) => state.setMe);

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (isAuthenticated) {
      router.replace("/(tabs)");
    }
  }, [isAuthenticated, router]);

  async function handleSignIn() {
    setLoading(true);
    setError(null);

    try {
      const me = await mobileAuthApi.login(email.trim(), password);
      setMe(me);
      router.replace("/(tabs)");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to sign in.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <KeyboardAvoidingView
      behavior={Platform.OS === "ios" ? "padding" : undefined}
      style={styles.screen}
    >
      <View style={styles.glowTop} />
      <View style={styles.glowBottom} />
      <View style={styles.container}>
        <View style={styles.brandRow}>
          <View style={styles.logoWrap}>
            <Text style={styles.logoMark}>△</Text>
          </View>
          <View>
            <Text style={styles.eyebrow}>FactoryOPS</Text>
            <Text style={styles.title}>Mobile Access</Text>
          </View>
        </View>

        <View style={styles.card}>
          <Text style={styles.cardTitle}>Sign in</Text>
          <Text style={styles.cardSubtitle}>
            Access machine monitoring, alerts, analytics, and plant operations.
          </Text>

          {error ? (
            <View style={styles.errorBox}>
              <Text style={styles.errorText}>{error}</Text>
            </View>
          ) : null}

          <View style={styles.fieldGroup}>
            <Text style={styles.label}>Email</Text>
            <TextInput
              value={email}
              onChangeText={setEmail}
              keyboardType="email-address"
              autoCapitalize="none"
              autoCorrect={false}
              placeholder="manash.ray@cittagent.com"
              placeholderTextColor={colors.textSecondary}
              style={styles.input}
            />
          </View>

          <View style={styles.fieldGroup}>
            <Text style={styles.label}>Password</Text>
            <TextInput
              value={password}
              onChangeText={setPassword}
              secureTextEntry
              autoCapitalize="none"
              autoCorrect={false}
              placeholder="Enter password"
              placeholderTextColor={colors.textSecondary}
              style={styles.input}
            />
          </View>

          <Pressable
            disabled={loading}
            onPress={() => void handleSignIn()}
            style={({ pressed }) => [
              styles.button,
              loading && styles.buttonDisabled,
              pressed && !loading ? styles.buttonPressed : null,
            ]}
          >
            {loading ? (
              <ActivityIndicator color={colors.textPrimary} />
            ) : (
              <Text style={styles.buttonText}>Sign in</Text>
            )}
          </Pressable>
        </View>
      </View>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: colors.background,
  },
  glowTop: {
    position: "absolute",
    top: -100,
    right: -40,
    width: 220,
    height: 220,
    borderRadius: 220,
    backgroundColor: "rgba(59,130,246,0.18)",
  },
  glowBottom: {
    position: "absolute",
    bottom: -80,
    left: -20,
    width: 180,
    height: 180,
    borderRadius: 180,
    backgroundColor: "rgba(245,158,11,0.12)",
  },
  container: {
    flex: 1,
    justifyContent: "center",
    paddingHorizontal: 22,
  },
  brandRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 14,
    marginBottom: 26,
  },
  logoWrap: {
    width: 54,
    height: 54,
    borderRadius: 18,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: "#0b1220",
    borderWidth: 1,
    borderColor: "rgba(59,130,246,0.35)",
  },
  logoMark: {
    color: "#f59e0b",
    fontSize: 24,
    fontWeight: "800",
  },
  eyebrow: {
    color: "#7dd3fc",
    textTransform: "uppercase",
    letterSpacing: 2,
    fontSize: 11,
    fontWeight: "700",
  },
  title: {
    color: colors.textPrimary,
    fontSize: 30,
    fontWeight: "800",
  },
  card: {
    backgroundColor: colors.card,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 18,
    padding: 20,
    gap: 14,
  },
  cardTitle: {
    color: colors.textPrimary,
    fontSize: 24,
    fontWeight: "800",
  },
  cardSubtitle: {
    color: colors.textSecondary,
    fontSize: 14,
    lineHeight: 20,
  },
  errorBox: {
    borderWidth: 1,
    borderColor: "rgba(239,68,68,0.35)",
    backgroundColor: "rgba(239,68,68,0.12)",
    borderRadius: 12,
    paddingHorizontal: 14,
    paddingVertical: 12,
  },
  errorText: {
    color: "#fca5a5",
    fontSize: 14,
  },
  fieldGroup: {
    gap: 8,
  },
  label: {
    color: colors.textPrimary,
    fontSize: 13,
    fontWeight: "700",
  },
  input: {
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 14,
    backgroundColor: "#0b0e14",
    color: colors.textPrimary,
    paddingHorizontal: 14,
    paddingVertical: 14,
    fontSize: 15,
  },
  button: {
    marginTop: 6,
    minHeight: 52,
    borderRadius: 14,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.primary,
  },
  buttonDisabled: {
    opacity: 0.7,
  },
  buttonPressed: {
    opacity: 0.9,
  },
  buttonText: {
    color: colors.textPrimary,
    fontSize: 15,
    fontWeight: "800",
  },
});
