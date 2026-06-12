/* eslint-disable @typescript-eslint/no-require-imports */

const { expect, test } = require("@playwright/test");

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    window.__factoryopsReloadOverride = () => {
      window.dispatchEvent(new CustomEvent("factoryops-test-reload"));
    };
  });

  await page.route("**/backend/auth/api/v1/auth/refresh", async (route) => {
    await route.fulfill({
      status: 401,
      contentType: "application/json",
      body: JSON.stringify({ detail: "No authenticated session for deploy-recovery harness." }),
    });
  });
});

test("stale deploy mismatch schedules an automatic reload once", async ({ page }) => {
  await page.goto("/test-support/deploy-recovery");

  await page.getByTestId("trigger-rsc-deploy-error").click();

  await expect(page.getByTestId("deploy-recovery-last-action")).toHaveText("reload_scheduled");
  await expect(page.getByTestId("deploy-recovery-reload-count")).toHaveText("1");
  await expect(page.getByTestId("deploy-recovery-banner")).toContainText("Reloading this tab");
});

test("repeat deploy mismatch within cooldown falls back to truthful manual reload UI", async ({ page }) => {
  await page.goto("/test-support/deploy-recovery");

  await page.getByTestId("trigger-server-action-error").click();
  await expect(page.getByTestId("deploy-recovery-reload-count")).toHaveText("1");

  await page.getByTestId("trigger-server-action-error").click();
  await expect(page.getByTestId("deploy-recovery-last-action")).toHaveText("manual_required");
  await expect(page.getByTestId("deploy-recovery-banner")).toContainText("needs a reload");
  const reloadNowButton = page.getByRole("button", { name: "Reload now" });
  await expect(reloadNowButton).toBeVisible();
  await reloadNowButton.click();
  await expect(page.getByTestId("deploy-recovery-reload-count")).toHaveText("2");
});

test("generic runtime errors do not trigger deploy recovery", async ({ page }) => {
  await page.goto("/test-support/deploy-recovery");

  await page.getByTestId("trigger-generic-error").click();

  await expect(page.getByTestId("deploy-recovery-last-action")).toHaveText("idle");
  await expect(page.getByTestId("deploy-recovery-reload-count")).toHaveText("0");
  await expect(page.getByTestId("deploy-recovery-banner")).toHaveCount(0);
});
