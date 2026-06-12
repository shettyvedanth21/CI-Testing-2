/* eslint-disable @typescript-eslint/no-require-imports */

const { expect, test } = require("@playwright/test");
const { installPhase3Harness } = require("./support/phase3Harness.js");

async function signIn(page, email, password = "FactoryOps#123") {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
}

test("invalid credentials and deactivated users see truthful login errors", async ({ page }) => {
  await installPhase3Harness(page);

  await signIn(page, "ops@example.com", "wrong-password");
  await expect(page.getByText("Invalid email or password")).toBeVisible();

  await page.getByLabel("Email").fill("disabled@example.com");
  await page.getByLabel("Password").fill("FactoryOps#123");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByText("Account is disabled")).toBeVisible();
});

test("login form rejects empty email and password with native browser validation", async ({ page }) => {
  await installPhase3Harness(page);

  await page.goto("/login");
  await page.locator("form").evaluate((form) => {
    if (form instanceof HTMLFormElement) {
      form.reportValidity();
    }
  });

  await expect
    .poll(async () =>
      page.getByLabel("Email").evaluate((element) =>
        element instanceof HTMLInputElement ? element.validationMessage : "",
      ),
    )
    .not.toBe("");

  await page.getByLabel("Email").fill("ops@example.com");
  await page.locator("form").evaluate((form) => {
    if (form instanceof HTMLFormElement) {
      form.reportValidity();
    }
  });

  await expect
    .poll(async () =>
      page.getByLabel("Password").evaluate((element) =>
        element instanceof HTMLInputElement ? element.validationMessage : "",
      ),
    )
    .not.toBe("");
});

test("forgot and reset password flows keep success, failure, and mismatch contracts truthful", async ({ page }) => {
  await installPhase3Harness(page);

  await page.goto("/forgot-password");
  await page.getByLabel("Email").fill("ops@example.com");
  await page.getByRole("button", { name: "Send reset link" }).click();
  await expect(page.getByText("If that email is registered, a password reset link has been sent.")).toBeVisible();

  await page.goto("/forgot-password");
  await page.getByLabel("Email").fill("mailer-fail@example.com");
  await page.getByRole("button", { name: "Send reset link" }).click();
  await expect(page.getByText("Email service is unavailable right now.")).toBeVisible();

  await page.goto("/reset-password?token=reset-valid");
  await expect(page.getByLabel("New password")).toBeVisible();
  await page.getByLabel("New password").fill("FactoryOps#999");
  await page.getByLabel("Confirm password").fill("FactoryOps#998");
  await page.getByRole("button", { name: "Reset password" }).click();
  await expect(page.getByText("Passwords do not match")).toBeVisible();
});

test("accept invite keeps valid and invalid token paths stable and supports follow-up sign in", async ({ page }) => {
  const harness = await installPhase3Harness(page);
  const inviteToken = harness.getInviteTokenForEmail("seeded-admin@example.com") || "invite-valid-seeded";

  await page.goto("/accept-invite?token=invalid-token");
  await expect(page.getByText("This invite link is invalid.")).toBeVisible();

  await page.goto(`/accept-invite?token=${inviteToken}`);
  await expect(page.getByLabel("New password")).toBeVisible();
  await page.getByLabel("New password").fill("SeededAdmin#123");
  await page.getByLabel("Confirm password").fill("SeededAdmin#123");
  await page.getByRole("button", { name: "Set password" }).click();
  await expect(page.getByText("Password set successfully. Redirecting to sign in...")).toBeVisible();
  await expect(page).toHaveURL(/\/login$/);

  await page.getByLabel("Email").fill("seeded-admin@example.com");
  await page.getByLabel("Password").fill("SeededAdmin#123");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/machines$/);
});

test("logout clears session and protected routes return to login", async ({ page }) => {
  await installPhase3Harness(page);

  await signIn(page, "ops@example.com");
  await expect(page).toHaveURL(/\/machines$/);

  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login$/);

  await page.goto("/machines");
  await expect(page).toHaveURL(/\/login$/);
});
