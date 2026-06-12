import { useEffect, useState } from "react";
import { GestureHandlerRootView } from "react-native-gesture-handler";
import { SafeAreaProvider } from "react-native-safe-area-context";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Stack, useRootNavigationState, useRouter, useSegments } from "expo-router";
import { ActivityIndicator, View, StyleSheet } from "react-native";

import { colors } from "../src/constants/colors";
import { useUserStore } from "../src/store/useUserStore";

export default function RootLayout() {
  const router = useRouter();
  const segments = useSegments();
  const navigationState = useRootNavigationState();
  const isAuthenticated = useUserStore((state) => state.isAuthenticated);
  const isHydrating = useUserStore((state) => state.isHydrating);
  const hydrateFromStorage = useUserStore((state) => state.hydrateFromStorage);
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 30_000,
            retry: 2,
          },
          mutations: {
            retry: 2,
          },
        },
      })
  );

  useEffect(() => {
    void hydrateFromStorage();
  }, [hydrateFromStorage]);

  useEffect(() => {
    if (!navigationState?.key || isHydrating) {
      return;
    }

    const rootSegment = segments[0];
    const onLogin = rootSegment === "login";
    const onTabs = rootSegment === "(tabs)";

    if (!isAuthenticated && !onLogin) {
      router.replace("/login");
      return;
    }

    if (isAuthenticated && !onTabs) {
      router.replace("/(tabs)");
    }
  }, [isAuthenticated, isHydrating, navigationState?.key, router, segments]);

  return (
    <GestureHandlerRootView style={styles.root}>
      <SafeAreaProvider>
        <QueryClientProvider client={queryClient}>
          <Stack screenOptions={{ headerShown: false }} />
          {isHydrating && (
            <View style={styles.loading}>
              <ActivityIndicator size="large" color={colors.primary} />
            </View>
          )}
        </QueryClientProvider>
      </SafeAreaProvider>
    </GestureHandlerRootView>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: colors.background,
  },
  loading: {
    ...StyleSheet.absoluteFillObject,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.background,
  },
});
