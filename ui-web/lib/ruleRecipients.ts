const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const PHONE_PATTERN = /^\+?[1-9]\d{7,14}$/;
const INDIA_COUNTRY_CODE = "+91";
const INDIA_COUNTRY_DIGITS = "91";
const INDIA_LOCAL_NUMBER_LENGTH = 10;

export function normalizeRuleRecipientEmail(email: string): string {
  return email.trim().toLowerCase();
}

export function isValidRuleRecipientEmail(email: string): boolean {
  return EMAIL_PATTERN.test(normalizeRuleRecipientEmail(email));
}

export function dedupeRuleRecipientEmails(emails: string[]): string[] {
  const seen = new Set<string>();
  const ordered: string[] = [];
  for (const email of emails) {
    const normalized = normalizeRuleRecipientEmail(email);
    if (!normalized || seen.has(normalized)) {
      continue;
    }
    seen.add(normalized);
    ordered.push(normalized);
  }
  return ordered;
}

export function normalizeRuleRecipientPhone(phone: string): string {
  const cleaned = phone.trim().replace(/[^\d+]/g, "");
  if (!cleaned) {
    return "";
  }

  if (cleaned.startsWith("00")) {
    return normalizeRuleRecipientPhone(`+${cleaned.slice(2)}`);
  }

  if (cleaned.startsWith("+")) {
    const digits = cleaned.slice(1).replace(/\D/g, "");
    return digits ? `+${digits}` : "";
  }

  const digits = cleaned.replace(/\D/g, "");
  if (!digits) {
    return "";
  }

  if (digits.length === INDIA_LOCAL_NUMBER_LENGTH) {
    return `${INDIA_COUNTRY_CODE}${digits}`;
  }

  if (digits.length === INDIA_LOCAL_NUMBER_LENGTH + INDIA_COUNTRY_DIGITS.length && digits.startsWith(INDIA_COUNTRY_DIGITS)) {
    return `+${digits}`;
  }

  return `+${digits}`;
}

export function normalizeRuleRecipientPhoneInput(phone: string): string {
  let digits = phone.replace(/\D/g, "");
  if (digits.startsWith(INDIA_COUNTRY_DIGITS) && digits.length > INDIA_LOCAL_NUMBER_LENGTH) {
    digits = digits.slice(INDIA_COUNTRY_DIGITS.length);
  }
  return digits.slice(0, INDIA_LOCAL_NUMBER_LENGTH);
}

export function isValidRuleRecipientPhone(phone: string): boolean {
  const normalized = normalizeRuleRecipientPhone(phone);
  return PHONE_PATTERN.test(normalized);
}

export function dedupeRuleRecipientPhones(phones: string[]): string[] {
  const seen = new Set<string>();
  const ordered: string[] = [];
  for (const phone of phones) {
    const normalized = normalizeRuleRecipientPhone(phone);
    if (!normalized || seen.has(normalized)) {
      continue;
    }
    if (!isValidRuleRecipientPhone(normalized)) {
      continue;
    }
    seen.add(normalized);
    ordered.push(normalized);
  }
  return ordered;
}
