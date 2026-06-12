import { useCallback } from "react";
import { ActivityIndicator, StyleSheet, View } from "react-native";
import { WebView } from "react-native-webview";
import { useFocusEffect } from "@react-navigation/native";

import { colors } from "../../src/constants/colors";

const WEB_APP_URL = "https://shivex.ai";
const injectedJavaScript = `
(function() {
  var body = document.querySelector('body');
  if (body) {
    body.style.overflow = 'auto';
  }

  var meta = document.querySelector('meta[name="viewport"]');
  if (!meta) {
    meta = document.createElement('meta');
    meta.name = 'viewport';
    document.head.appendChild(meta);
  }

  meta.setAttribute('content', 'width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no');
})();
true;
`;

export default function WebAppScreen() {
  useFocusEffect(
    useCallback(() => {
      return () => {};
    }, [])
  );

  return (
    <View style={styles.screen}>
      <WebView
        source={{ uri: WEB_APP_URL }}
        style={styles.webview}
        javaScriptEnabled
        domStorageEnabled
        scalesPageToFit={false}
        startInLoadingState
        injectedJavaScript={injectedJavaScript}
        renderLoading={() => (
          <View style={styles.loading}>
            <ActivityIndicator size="large" color={colors.primary} />
          </View>
        )}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: colors.background,
  },
  webview: {
    flex: 1,
    backgroundColor: colors.background,
  },
  loading: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.background,
  },
});
