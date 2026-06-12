import test from "node:test";
import assert from "node:assert/strict";

import {
  dedupeRuleRecipientEmails,
  isValidRuleRecipientEmail,
  isValidRuleRecipientPhone,
  normalizeRuleRecipientEmail,
  normalizeRuleRecipientPhone,
  normalizeRuleRecipientPhoneInput,
} from "../../lib/ruleRecipients.ts";

test("dedupeRuleRecipientEmails normalizes and dedupes recipient emails", () => {
  assert.deepEqual(
    dedupeRuleRecipientEmails([
      " OPS@PlantA.com ",
      "ops@planta.com",
      "guard@planta.com",
    ]),
    ["ops@planta.com", "guard@planta.com"],
  );
});

test("isValidRuleRecipientEmail validates recipient email format", () => {
  assert.equal(isValidRuleRecipientEmail("alerts@planta.com"), true);
  assert.equal(isValidRuleRecipientEmail("invalid-email"), false);
});

test("normalizeRuleRecipientEmail normalizes recipient emails consistently", () => {
  assert.equal(normalizeRuleRecipientEmail(" Guard@PlantA.com "), "guard@planta.com");
});

test("normalizeRuleRecipientPhone defaults 10-digit Indian numbers to +91", () => {
  assert.equal(normalizeRuleRecipientPhone("9876543210"), "+919876543210");
  assert.equal(normalizeRuleRecipientPhone("+919876543210"), "+919876543210");
  assert.equal(normalizeRuleRecipientPhone("919876543210"), "+919876543210");
});

test("normalizeRuleRecipientPhoneInput keeps the editable value as a local 10-digit number", () => {
  assert.equal(normalizeRuleRecipientPhoneInput("9876543210"), "9876543210");
  assert.equal(normalizeRuleRecipientPhoneInput("+91 98765 43210"), "9876543210");
  assert.equal(normalizeRuleRecipientPhoneInput("919876543210"), "9876543210");
});

test("isValidRuleRecipientPhone accepts Indian 10-digit entries after normalization", () => {
  assert.equal(isValidRuleRecipientPhone("9876543210"), true);
  assert.equal(isValidRuleRecipientPhone("12345"), false);
});
