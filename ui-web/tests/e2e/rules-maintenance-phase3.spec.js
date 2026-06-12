/* eslint-disable @typescript-eslint/no-require-imports */

const { expect, test } = require("@playwright/test");
const { installJourneyHappyPathHarness } = require("./support/journeyHappyPathHarness.js");

function modalFieldSelect(modal, label) {
  return modal.locator("label", { hasText: label }).locator("xpath=following-sibling::select[1]");
}

test("machine rules support edit, delete, and truthful out-of-scope mutation denial", async ({ page }) => {
  const harness = await installJourneyHappyPathHarness(page);

  await page.goto("/login");
  await page.getByLabel("Email").fill("ops@example.com");
  await page.getByLabel("Password").fill("FactoryOps#123");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/machines$/);

  await page.getByRole("button", { name: "+ Add Device" }).click();
  const createModal = page.locator("form").filter({ has: page.getByRole("button", { name: "Add Device", exact: true }) });
  await createModal.locator('input[placeholder="e.g. Compressor Line A"]').fill("Compressor Line A");
  await modalFieldSelect(createModal, "Plant").selectOption("plant-1");
  await createModal.locator('input[placeholder="e.g. Compressor, Chiller, Motor"]').fill("compressor");
  await modalFieldSelect(createModal, "Device ID Class").selectOption("active");
  await createModal.locator('input[placeholder="e.g. Atlas Copco"]').fill("Atlas Copco");
  await createModal.locator('input[placeholder="e.g. GA37"]').fill("GA37");
  await createModal.locator('input[placeholder="e.g. Building A, Floor 1"]').fill("Line 1");
  await page.getByRole("button", { name: "Add Device", exact: true }).click();
  await page.getByRole("button", { name: "Done" }).click();

  await page.getByRole("link", { name: /Compressor Line A AD00000001/ }).click();
  await expect(page).toHaveURL(/\/machines\/AD00000001$/);

  await page.getByRole("button", { name: "Configure Rules", exact: true }).click();
  await page.getByRole("button", { name: "Add Rule" }).click();
  await page.getByLabel("Rule Name").fill("Idle longer than 45 minutes");
  await page.getByLabel("Rule Type").selectOption("continuous_idle_duration");
  await page.getByLabel("Duration (minutes)").fill("45");
  await page.getByRole("checkbox", { name: "Email" }).check();
  await page.getByLabel("Email Recipients").fill("alerts@factoryops.example");
  await page.getByRole("button", { name: "Add Email" }).click();
  await page.getByRole("button", { name: "Create Rule" }).click();
  await expect(page.getByText("Idle longer than 45 minutes")).toBeVisible();

  const ruleRow = page.getByRole("row").filter({ hasText: "Idle longer than 45 minutes" }).first();
  await ruleRow.getByRole("button", { name: "Edit" }).click();
  await page.getByLabel("Rule Name").fill("Idle longer than 60 minutes");
  await page.getByLabel("Duration (minutes)").fill("60");
  await page.getByRole("button", { name: "Update Rule" }).click();
  await expect(page.getByText("Idle longer than 60 minutes")).toBeVisible();

  harness.denyNextRuleMutation();
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("row").filter({ hasText: "Idle longer than 60 minutes" }).first().getByRole("button", { name: "Delete" }).click();
  await expect(page.getByText("Forbidden: you cannot modify rules outside your scope.")).toBeVisible();
  await expect(page.getByText("Idle longer than 60 minutes")).toBeVisible();

  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("row").filter({ hasText: "Idle longer than 60 minutes" }).first().getByRole("button", { name: "Delete" }).click();
  await expect(page.getByText("Idle longer than 60 minutes")).toHaveCount(0);
});

test("maintenance mutation flow is deterministic and missing-record delete errors stay truthful", async ({ page }) => {
  const harness = await installJourneyHappyPathHarness(page);

  await page.goto("/login");
  await page.getByLabel("Email").fill("ops@example.com");
  await page.getByLabel("Password").fill("FactoryOps#123");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/machines$/);

  await page.getByRole("button", { name: "+ Add Device" }).click();
  const maintenanceModal = page.locator("form").filter({ has: page.getByRole("button", { name: "Add Device", exact: true }) });
  await maintenanceModal.locator('input[placeholder="e.g. Compressor Line A"]').fill("Maintenance Line");
  await modalFieldSelect(maintenanceModal, "Plant").selectOption("plant-1");
  await maintenanceModal.locator('input[placeholder="e.g. Compressor, Chiller, Motor"]').fill("compressor");
  await modalFieldSelect(maintenanceModal, "Device ID Class").selectOption("active");
  await maintenanceModal.locator('input[placeholder="e.g. Atlas Copco"]').fill("Atlas Copco");
  await maintenanceModal.locator('input[placeholder="e.g. GA37"]').fill("GA37");
  await maintenanceModal.locator('input[placeholder="e.g. Building A, Floor 1"]').fill("Line 2");
  await page.getByRole("button", { name: "Add Device", exact: true }).click();
  await page.getByRole("button", { name: "Done" }).click();

  await page.getByRole("link", { name: /Maintenance Line AD00000001/ }).click();
  await expect(page).toHaveURL(/\/machines\/AD00000001$/);

  await page.getByRole("button", { name: "Maintenance Log", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Maintenance History" })).toBeVisible();
  await expect(page.getByText("No maintenance records yet")).toBeVisible();

  await page.getByRole("button", { name: "Add Maintenance" }).click();
  let maintenanceDialog = page.getByRole("dialog").filter({ has: page.getByRole("heading", { name: "Add Maintenance" }) });
  await maintenanceDialog.getByLabel(/Maintenance date/i).fill("2026-05-03");
  await maintenanceDialog.getByLabel(/Cost/i).fill("2200");
  await maintenanceDialog.getByLabel(/Issue title/i).fill("Bearing lubrication");
  await maintenanceDialog.getByLabel(/Notes/i).fill("Lubricated bearings and verified noise reduction.");
  await maintenanceDialog.getByLabel(/Performed by/i).fill("Ajay");
  await maintenanceDialog.getByLabel(/Status/i).selectOption("completed");
  await maintenanceDialog.getByLabel(/Next due date/i).fill("2026-06-03");
  await maintenanceDialog.getByRole("button", { name: "Add Maintenance", exact: true }).click();
  await expect(page.getByText("Maintenance record added.")).toBeVisible();
  await expect(page.getByText("Bearing lubrication")).toBeVisible();

  const addedRecord = page.locator("div").filter({ hasText: "Bearing lubrication" }).first();
  await addedRecord.getByRole("button", { name: "Edit" }).click();
  maintenanceDialog = page.getByRole("dialog").filter({ has: page.getByRole("heading", { name: "Edit Maintenance Record" }) });
  await maintenanceDialog.getByLabel(/Issue title/i).fill("Bearing lubrication follow-up");
  await maintenanceDialog.getByLabel(/Cost/i).fill("2450");
  await maintenanceDialog.getByRole("button", { name: "Save Changes", exact: true }).click();
  await expect(page.getByText("Maintenance record updated.")).toBeVisible();
  await expect(page.getByText("Bearing lubrication follow-up")).toBeVisible();

  harness.failNextMaintenanceDeleteAsMissing();
  const updatedRecord = page.locator("div").filter({ hasText: "Bearing lubrication follow-up" }).first();
  await updatedRecord.getByRole("button", { name: "Delete" }).click();
  let deleteDialog = page.getByRole("dialog").filter({ has: page.getByRole("heading", { name: "Delete Maintenance Record" }) });
  await deleteDialog.getByRole("button", { name: "Delete Record", exact: true }).click();
  await expect(page.getByText("This maintenance record is no longer available. Please refresh the list.")).toBeVisible();
  await deleteDialog.getByRole("button", { name: "Cancel", exact: true }).click();

  await updatedRecord.getByRole("button", { name: "Delete" }).click();
  deleteDialog = page.getByRole("dialog").filter({ has: page.getByRole("heading", { name: "Delete Maintenance Record" }) });
  await deleteDialog.getByRole("button", { name: "Delete Record", exact: true }).click();
  await expect(page.getByText("Maintenance record deleted.")).toBeVisible();
  await expect(page.getByText("Bearing lubrication follow-up")).toHaveCount(0);
});
