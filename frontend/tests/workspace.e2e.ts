import { test, expect } from "@playwright/test";
import fs from "node:fs";

test("signup, real sample posting, evidence, exports, upload, rules and mobile", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Welcome back." }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Create an account" }).click();
  await page.getByLabel("Your name", { exact: true }).fill("Aarav Mehta");
  await page
    .getByLabel("Email address", { exact: true })
    .fill(`fictional-e2e-${Date.now()}@example.com`);
  await page
    .getByLabel("Password", { exact: true })
    .fill("Fictional-e2e-password-123");
  await page
    .getByLabel("Confirm password", { exact: true })
    .fill("Fictional-e2e-password-123");
  await page.getByRole("button", { name: "Create workspace" }).click();
  await expect(
    page.getByRole("heading", { name: "Keep your way back in." }),
  ).toBeVisible();
  await page.getByLabel("I have saved my recovery code.").check();
  await page.getByRole("button", { name: "Open my workspace" }).click();
  await expect(
    page.getByRole("heading", {
      name: "A little clarity. A better perspective.",
    }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Explore with fictional sample data" })
    .click();
  await expect(page.getByText("80 records", { exact: true })).toBeVisible();
  await page.getByRole("checkbox", { name: "Select this page" }).check();
  await page.getByRole("button", { name: "Post selected · 10" }).click();
  await expect(page.getByText("70 records", { exact: true })).toBeVisible();
  const pending = await (await page.request.get("/api/pending")).json();
  const approval = await page.request.post("/api/approve", {
    headers: { "X-Goldilocks": "1" },
    data: { ids: pending.rows.map((r: { id: string }) => r.id) },
  });
  expect(approval.ok()).toBeTruthy();
  await page.reload();
  await expect(
    page.getByText("80 approved transactions", { exact: true }),
  ).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  fs.mkdirSync("../docs/screenshots", { recursive: true });
  await page.screenshot({
    path: "../docs/screenshots/dashboard.png",
    fullPage: true,
  });
  const nav = (name: string) =>
    page.getByRole("navigation").getByRole("button", { name, exact: true });
  await nav("Ledger").click();
  await expect(page.getByText("80 records", { exact: true })).toBeVisible();
  await page
    .getByPlaceholder("Search a transaction, category or account…")
    .fill("SWIGGY");
  await expect(page.getByText("10 records", { exact: true })).toBeVisible();
  await page
    .getByRole("button", { name: /Inspect SWIGGY/ })
    .first()
    .click();
  await expect(
    page.getByRole("heading", { name: "Double-entry journal" }),
  ).toBeVisible();
  await expect(
    page.getByText("fictional-statement.csv", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Close dialog" }).click();
  await nav("Reports").click();
  await expect(
    page.getByText("Period surplus / deficit", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Balance verification" }).click();
  await expect(
    page.getByText("Journal debits equal credits · 0 missing journals"),
  ).toBeVisible();
  const dp = page.waitForEvent("download");
  await page.getByRole("link", { name: "PDF", exact: true }).click();
  expect((await dp).suggestedFilename()).toBe("goldilocks-statements.pdf");
  await nav("Reconciliation").click();
  await expect(page.getByText("Balances match", { exact: true })).toBeVisible();
  await page
    .getByRole("button", { name: "Import statement", exact: true })
    .first()
    .click();
  await page
    .getByLabel("Statement file")
    .setInputFiles("../samples/statement-template.csv");
  await page.getByLabel(/Account label/).fill("UI import account");
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Import statement", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "2 entries ready to review." }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Back to workspace" }).click();
  await nav("Personal rules").click();
  await page.getByLabel("Narration text").fill("FICTIONAL RECURRING COFFEE");
  await page.getByLabel("Ledger category").selectOption("exp_food");
  await page.getByRole("button", { name: "Save rule", exact: true }).click();
  await expect(
    page.getByText("FICTIONAL RECURRING COFFEE", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Workspace settings", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Opening positions" }),
  ).toBeVisible();
  await nav("Overview").click();
  if (await page.getByRole("button", { name: "Dismiss notification" }).count())
    await page.getByRole("button", { name: "Dismiss notification" }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(
    page.getByRole("button", { name: "Open navigation" }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({
    path: "../docs/screenshots/mobile.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Open navigation" }).click();
  await nav("Statements").click();
  await expect(
    page.getByRole("heading", { name: "Your document collection." }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Open navigation" }).click();
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Welcome back." }),
  ).toBeVisible();
  expect(errors).toEqual([]);
});
