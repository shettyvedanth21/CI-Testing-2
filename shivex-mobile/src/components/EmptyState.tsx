import { Feather } from "@expo/vector-icons";
import { StyleSheet, Text, View } from "react-native";

import { colors } from "../constants/colors";

type EmptyStateProps = {
  message: string;
  icon?: string;
};

export function EmptyState({ message, icon = "inbox" }: EmptyStateProps) {
  return (
    <View style={styles.container}>
      <Feather name={icon as never} size={28} color={colors.textSecondary} />
      <Text style={styles.message}>{message}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    alignItems: "center",
    justifyContent: "center",
    gap: 10,
    paddingVertical: 28,
  },
  message: {
    color: colors.textSecondary,
    fontSize: 14,
    textAlign: "center",
  },
});
