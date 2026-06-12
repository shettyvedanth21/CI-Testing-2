import { useCallback, useEffect, useRef, useState } from "react";
import { useFocusEffect } from "@react-navigation/native";
import { Animated, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";

import { getCopilotHealth, sendMessage, type CopilotHistoryTurn } from "../../src/api/copilot";
import { colors } from "../../src/constants/colors";
import { ErrorState } from "../../src/components/ErrorState";
import { SkeletonBox } from "../../src/components/SkeletonBox";

type UiMessage = {
  role: "user" | "assistant";
  content: string;
  followUps?: string[];
};

const QUICK_QUESTIONS = [
  "Summarize today's factory performance",
  "Which machine consumed the most power today?",
  "Show recent alerts today",
  "What is today's idle running cost?",
];

function TypingDots() {
  const opacity = useRef(new Animated.Value(0.4)).current;

  useEffect(() => {
    const animation = Animated.loop(
      Animated.sequence([
        Animated.timing(opacity, { toValue: 1, duration: 500, useNativeDriver: true }),
        Animated.timing(opacity, { toValue: 0.4, duration: 500, useNativeDriver: true }),
      ])
    );

    animation.start();
    return () => animation.stop();
  }, [opacity]);

  return (
    <Animated.View style={[styles.typingDots, { opacity }]}>
      <Text style={styles.typingText}>...</Text>
    </Animated.View>
  );
}

export default function CopilotScreen() {
  const [messages, setMessages] = useState<UiMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [available, setAvailable] = useState(false);
  const [backendError, setBackendError] = useState("");
  const [error, setError] = useState("");

  const checkStatus = useCallback(async () => {
    const health = await getCopilotHealth();
    if (!health) {
      setBackendError("Unable to connect to Copilot backend");
      setAvailable(false);
    } else if (health.provider_configured === false || health.status !== "ok") {
      setBackendError("");
      setAvailable(false);
    } else {
      setBackendError("");
      setAvailable(true);
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    void checkStatus();
  }, [checkStatus]);

  useFocusEffect(
    useCallback(() => {
      void checkStatus();
    }, [checkStatus])
  );

  async function ask(question: string) {
    const trimmed = question.trim();
    if (!trimmed || sending || !available) {
      return;
    }

    const nextMessages = [...messages, { role: "user" as const, content: trimmed }];
    setMessages(nextMessages);
    setInput("");
    setSending(true);
    setError("");

    const history: CopilotHistoryTurn[] = nextMessages.slice(-5).map((item) => ({
      role: item.role,
      content: item.content,
    }));

    const response = await sendMessage(trimmed, history);

    if (!response) {
      setBackendError("Unable to connect to Copilot backend");
      setSending(false);
      return;
    }

    if (response.errorCode === "NOT_CONFIGURED" || response.errorCode === "AI_UNAVAILABLE") {
      setAvailable(false);
      setSending(false);
      return;
    }

    if (response.errorCode === "QUERY_BLOCKED") {
      setError("Copilot blocked that query. Try a narrower question.");
      setSending(false);
      return;
    }

    setMessages((current) => [
      ...current,
      {
        role: "assistant",
        content: response.answer,
        followUps: response.followUpSuggestions,
      },
    ]);
    setSending(false);
  }

  if (loading) {
    return (
      <View style={styles.screen}>
        <View style={styles.content}>
          <SkeletonBox height={240} borderRadius={14} />
        </View>
      </View>
    );
  }

  if (backendError) {
    return (
      <View style={styles.screen}>
        <ErrorState message={backendError} onRetry={() => void checkStatus()} />
      </View>
    );
  }

  if (!available) {
    return (
      <View style={styles.screen}>
        <View style={styles.centeredCard}>
          <Text style={styles.emptyTitle}>Copilot is not configured.</Text>
          <Text style={styles.emptyText}>
            Add an AI provider key in the web platform settings.
          </Text>
        </View>
      </View>
    );
  }

  return (
    <View style={styles.screen}>
      <View style={styles.header}>
        <Text style={styles.title}>Copilot</Text>
        <Pressable onPress={() => {
          setMessages([]);
          setError("");
        }}>
          <Text style={styles.newChatText}>New Chat</Text>
        </Pressable>
      </View>

      <View style={styles.quickRow}>
        {QUICK_QUESTIONS.map((question) => (
          <Pressable key={question} style={styles.quickChip} onPress={() => void ask(question)}>
            <Text style={styles.quickChipText}>{question}</Text>
          </Pressable>
        ))}
      </View>

      {error ? <ErrorState message={error} onRetry={() => setError("")} /> : null}

      <ScrollView style={styles.chatArea} contentContainerStyle={styles.chatContent}>
        {messages.length === 0 ? (
          <View style={styles.centeredCard}>
            <Text style={styles.emptyTitle}>Factory Copilot</Text>
            <Text style={styles.emptyText}>Ask about machines, energy, alerts, waste, and trends.</Text>
          </View>
        ) : null}

        {messages.map((message, index) => (
          <View
            key={`${message.role}-${index}`}
            style={[styles.bubble, message.role === "user" ? styles.userBubble : styles.assistantBubble]}
          >
            <Text style={styles.bubbleText}>{message.content}</Text>
            {message.role === "assistant" && message.followUps?.length ? (
              <View style={styles.followUpRow}>
                {message.followUps.map((item) => (
                  <Pressable key={item} style={styles.followUpChip} onPress={() => void ask(item)}>
                    <Text style={styles.followUpText}>{item}</Text>
                  </Pressable>
                ))}
              </View>
            ) : null}
          </View>
        ))}

        {sending ? <TypingDots /> : null}
      </ScrollView>

      <View style={styles.inputRow}>
        <TextInput
          value={input}
          onChangeText={setInput}
          placeholder="Ask Copilot"
          placeholderTextColor={colors.textSecondary}
          style={styles.input}
        />
        <Pressable style={styles.sendButton} onPress={() => void ask(input)}>
          <Text style={styles.sendButtonText}>Send</Text>
        </Pressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.background, padding: 16, gap: 12 },
  content: { gap: 14 },
  header: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  title: { color: colors.textPrimary, fontSize: 24, fontWeight: "700" },
  newChatText: { color: colors.primary, fontSize: 14, fontWeight: "700" },
  quickRow: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  quickChip: {
    backgroundColor: colors.card,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 999,
    paddingHorizontal: 12,
    paddingVertical: 8,
  },
  quickChipText: { color: colors.textSecondary, fontSize: 12, fontWeight: "600" },
  chatArea: { flex: 1 },
  chatContent: { gap: 12, paddingBottom: 12 },
  centeredCard: {
    backgroundColor: colors.card,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 14,
    padding: 18,
    gap: 8,
    alignItems: "center",
  },
  emptyTitle: { color: colors.textPrimary, fontSize: 18, fontWeight: "700", textAlign: "center" },
  emptyText: { color: colors.textSecondary, fontSize: 14, textAlign: "center" },
  bubble: { maxWidth: "85%", borderRadius: 16, padding: 14, gap: 10 },
  userBubble: { alignSelf: "flex-end", backgroundColor: colors.primary },
  assistantBubble: {
    alignSelf: "flex-start",
    backgroundColor: colors.card,
    borderColor: colors.border,
    borderWidth: 1,
  },
  bubbleText: { color: colors.textPrimary, fontSize: 14 },
  followUpRow: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  followUpChip: {
    backgroundColor: colors.background,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 999,
    paddingHorizontal: 10,
    paddingVertical: 6,
  },
  followUpText: { color: colors.primary, fontSize: 12, fontWeight: "600" },
  typingDots: {
    alignSelf: "flex-start",
    backgroundColor: colors.card,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 16,
    paddingHorizontal: 16,
    paddingVertical: 12,
  },
  typingText: { color: colors.textSecondary, fontSize: 18, fontWeight: "700" },
  inputRow: { flexDirection: "row", gap: 10, alignItems: "center" },
  input: {
    flex: 1,
    backgroundColor: colors.card,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 12,
    color: colors.textPrimary,
    paddingHorizontal: 14,
    paddingVertical: 12,
    fontSize: 14,
  },
  sendButton: {
    backgroundColor: colors.primary,
    borderRadius: 12,
    paddingHorizontal: 16,
    paddingVertical: 12,
  },
  sendButtonText: { color: colors.textPrimary, fontSize: 14, fontWeight: "700" },
});
