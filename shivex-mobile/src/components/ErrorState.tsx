import { Feather } from "@expo/vector-icons";
import { Pressable, StyleSheet, Text, View } from "react-native";

import { colors } from "../constants/colors";

type ErrorStateProps = {
  message: string;
  onRetry: () => void;
};

export function ErrorState({ message, onRetry }: ErrorStateProps) {
  return (
    <View style={styles.container}>
      <Feather name="alert-circle" size={28} color={colors.error} />
      <Text style={styles.message}>{message}</Text>
      <Pressable onPress={onRetry} style={({ pressed }) => [styles.button, pressed && styles.buttonPressed]}>
        <Text style={styles.buttonText}>Retry</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    alignItems: "center",
    justifyContent: "center",
    gap: 12,
    paddingVertical: 28,
  },
  message: {
    color: colors.textPrimary,
    fontSize: 14,
    textAlign: "center",
  },
  button: {
    backgroundColor: colors.primary,
    borderRadius: 8,
    paddingHorizontal: 16,
    paddingVertical: 10,
  },
  buttonPressed: {
    opacity: 0.9,
  },
  buttonText: {
    color: colors.textPrimary,
    fontSize: 14,
    fontWeight: "700",
  },
});
