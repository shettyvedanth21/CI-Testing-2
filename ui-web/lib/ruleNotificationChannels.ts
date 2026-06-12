import type { MeResponse } from "@/lib/authApi";
import type { PremiumOrgGrantKey } from "@/lib/orgFeatureEntitlements";

export type RuleNotificationChannel = "email" | "sms" | "whatsapp";

export interface RuleNotificationChannelState {
  channel: RuleNotificationChannel;
  label: string;
  checked: boolean;
  available: boolean;
  disabled: boolean;
  legacyUnavailable: boolean;
  helperText: string;
}

const CHANNEL_LABELS: Record<RuleNotificationChannel, string> = {
  email: "Email",
  sms: "SMS",
  whatsapp: "WhatsApp",
};

const CHANNEL_GRANTS: Record<Exclude<RuleNotificationChannel, "email">, PremiumOrgGrantKey> = {
  sms: "notification_sms",
  whatsapp: "notification_whatsapp",
};

function hasPremiumGrant(me: MeResponse | null, grant: PremiumOrgGrantKey): boolean {
  return Boolean(me?.entitlements?.premium_feature_grants?.includes(grant));
}

function buildUnavailableHelper(channel: Exclude<RuleNotificationChannel, "email">): string {
  return channel === "sms"
    ? "Enable SMS alerts in organisation settings to use this channel."
    : "Enable WhatsApp alerts in organisation settings to use this channel.";
}

function buildLegacyHelper(channel: Exclude<RuleNotificationChannel, "email">): string {
  return channel === "sms"
    ? "This rule still uses SMS, but your organisation no longer has access. Remove it or ask a super admin to re-enable SMS alerts in organisation settings."
    : "This rule still uses WhatsApp, but your organisation no longer has access. Remove it or ask a super admin to re-enable WhatsApp alerts in organisation settings.";
}

export function getRuleNotificationChannelStates(
  me: MeResponse | null,
  selectedChannels: readonly string[],
): RuleNotificationChannelState[] {
  const selected = new Set(selectedChannels);

  return (["email", "sms", "whatsapp"] as RuleNotificationChannel[]).map((channel) => {
    const checked = selected.has(channel);

    if (channel === "email") {
      return {
        channel,
        label: CHANNEL_LABELS[channel],
        checked,
        available: true,
        disabled: false,
        legacyUnavailable: false,
        helperText: "Email alerts are included by default.",
      };
    }

    const grant = CHANNEL_GRANTS[channel];
    const available = hasPremiumGrant(me, grant);
    const legacyUnavailable = checked && !available;

    return {
      channel,
      label: CHANNEL_LABELS[channel],
      checked,
      available,
      disabled: !available && !checked,
      legacyUnavailable,
      helperText: legacyUnavailable
        ? buildLegacyHelper(channel)
        : available
          ? "Available with premium notification alerts."
          : buildUnavailableHelper(channel),
    };
  });
}

export function buildUnavailableSelectedChannelMessage(states: readonly RuleNotificationChannelState[]): string | null {
  const legacyUnavailable = states.filter((state) => state.legacyUnavailable);
  if (legacyUnavailable.length === 0) {
    return null;
  }

  const labels = legacyUnavailable.map((state) => state.label);
  if (labels.length === 1) {
    return `${labels[0]} alerts are no longer enabled for this organisation. Remove this channel or ask a super admin to re-enable it in organisation settings.`;
  }
  return `${labels.join(" and ")} alerts are no longer enabled for this organisation. Remove those channels or ask a super admin to re-enable them in organisation settings.`;
}
