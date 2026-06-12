import { useEffect, useState } from "react";
import { ActivityIndicator, View } from "react-native";
import { WebView } from "react-native-webview";
import * as SecureStore from "expo-secure-store";
import { router } from "expo-router";

export default function Index() {
  const [ready, setReady] = useState(false);
  const [authed, setAuthed] = useState(false);

  useEffect(() => {
    SecureStore.getItemAsync("shivex_user_role").then((role) => {
      if (!role) {
        router.replace("/role-select");
      } else {
        setAuthed(true);
      }
      setReady(true);
    });
  }, []);

  if (!ready) return (
    <View style={{ flex: 1, backgroundColor: "#080a0e", justifyContent: "center", alignItems: "center" }}>
      <ActivityIndicator color="#3b82f6" size="large" />
    </View>
  );

  if (!authed) return null;

  return (
    <WebView
      source={{ uri: "https://shivex.ai" }}
      style={{ flex: 1 }}
      javaScriptEnabled
      domStorageEnabled
      startInLoadingState
      renderLoading={() => (
        <View style={{ flex: 1, backgroundColor: "#080a0e", justifyContent: "center", alignItems: "center" }}>
          <ActivityIndicator color="#3b82f6" size="large" />
        </View>
      )}
    />
  );
}