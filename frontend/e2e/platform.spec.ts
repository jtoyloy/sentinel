import { test, expect, Page } from "@playwright/test";
import { readFileSync, existsSync } from "node:fs";
const credentialPath =
  process.env.SENTINEL_TEST_USERS || "../.runtime/platform-users.json";
const available = existsSync(credentialPath);
const users = available ? JSON.parse(readFileSync(credentialPath, "utf8")) : {};
test.skip(
  !available,
  "Provide provisioned synthetic accounts via SENTINEL_TEST_USERS (JSON keyed ADMIN/ANALYST/VIEWER).",
);
const seed = 730000 + (Math.floor(Date.now() / 1000) % 100000);
async function login(page: Page, role = "ADMIN") {
  await page.goto("/");
  await page.getByLabel("Username", { exact: true }).fill(users[role].username);
  await page.getByLabel("Password", { exact: true }).fill(users[role].password);
  await page.getByRole("button", { name: "Sign in →", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Compliance overview", exact: true }),
  ).toBeVisible();
}
async function nav(page: Page, name: string) {
  console.log(`Validate view: ${name}`);
  await page
    .getByRole("navigation")
    .getByRole("link", { name, exact: true })
    .click();
  await expect(page.getByRole("heading", { name, exact: true })).toBeVisible();
}
function panel(page: Page, title: string) {
  return page
    .locator("section.panel")
    .filter({ has: page.getByRole("heading", { name: title, exact: true }) });
}
test("authenticated institutional workflows persist HR, messages, checks, cases, reconciliation and exports", async ({
  page,
  browser,
}) => {
  const browserErrors: string[] = [];
  page.on("pageerror", (e) => {
    browserErrors.push(e.message);
    console.error("Browser runtime error:", e.message);
  });
  await login(page);
  await nav(page, "Employee management");
  const hr = panel(page, "Synthetic HR feed");
  await hr.getByLabel("Deterministic seed").fill(String(seed));
  await hr.getByLabel("Employee count").fill("3");
  await hr.getByRole("button", { name: "Import HR profiles" }).click();
  await expect(hr.getByRole("status")).toContainText("completed successfully");
  await page.getByPlaceholder("Search employees…").fill(`EMP-${seed}-0000`);
  await expect(panel(page, "Employee directory")).toContainText(
    `EMP-${seed}-0000`,
  );
  await page.getByRole("button", { name: "Inspect ↗" }).first().click();
  await expect(page.getByRole("dialog")).toContainText(
    "Trading account mappings",
  );
  await page.getByRole("button", { name: "Close detail", exact: true }).click();
  await nav(page, "Communications");
  const messages = panel(page, "Synthetic email and chat feed");
  await messages.getByLabel("Deterministic seed").fill(String(seed));
  await messages
    .getByRole("button", { name: "Generate communications" })
    .click();
  await expect(messages.getByRole("status")).toContainText(
    "completed successfully",
  );
  await page.getByRole("button", { name: "Inspect ↗" }).first().click();
  await expect(page.getByRole("dialog")).toContainText(
    "SHA256 integrity verified",
  );
  await page.getByRole("button", { name: "Close detail", exact: true }).click();
  await nav(page, "Trading restrictions");
  const policy = panel(page, "Publish a policy version");
  await policy.getByLabel("Policy name").fill(`UI-synthetic-policy-${seed}`);
  await policy.getByLabel("Instrument (uppercase symbol)").fill("AAPL");
  await policy
    .getByLabel("Policy rationale")
    .fill("Synthetic end-to-end compliance validation");
  await policy
    .getByLabel("Effective from")
    .fill(new Date(Date.now() - 60000).toISOString());
  await policy
    .getByRole("button", { name: "Publish policy", exact: true })
    .click();
  await expect(policy.getByRole("status")).toContainText(
    "completed successfully",
  );
  const check = panel(page, "Pre-trade permission check");
  await check
    .getByLabel("Employee ID", { exact: true })
    .fill(`EMP-${seed}-0000`);
  await check.getByLabel("Instrument (uppercase symbol)").fill("AAPL");
  await check
    .getByRole("button", { name: "Check permission", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toContainText("block");
  await page.getByRole("button", { name: "Close detail", exact: true }).click();
  const analystContext = await browser.newContext();
  const analyst = await analystContext.newPage();
  await login(analyst, "ANALYST");
  await nav(analyst, "Trading restrictions");
  const exemption = panel(analyst, "Request an exemption");
  await exemption
    .getByLabel("Employee ID", { exact: true })
    .fill(`EMP-${seed}-0000`);
  await exemption.getByLabel("Instrument (uppercase symbol)").fill("AAPL");
  await exemption
    .getByLabel("Business justification")
    .fill("Synthetic independent approval validation.");
  await exemption
    .getByLabel("Expiry (ISO with timezone)")
    .fill(new Date(Date.now() + 3600000).toISOString());
  await exemption
    .getByRole("button", { name: "Request approval", exact: true })
    .click();
  await expect(exemption.getByRole("status")).toContainText(
    "completed successfully",
  );
  await expect(
    analyst.getByRole("button", { name: "Review approval" }),
  ).toHaveCount(0);
  await analystContext.close();
  await panel(page, "Exemption approvals")
    .getByRole("button", { name: "Refresh", exact: true })
    .click();
  const approvalRow = panel(page, "Exemption approvals")
    .getByRole("row")
    .filter({ hasText: `EMP-${seed}-0000` });
  await approvalRow.getByRole("button", { name: "Review approval" }).click();
  await page
    .getByRole("dialog")
    .getByLabel("Approval rationale")
    .fill("Independent administrator reviewed synthetic employee and policy.");
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Approve exemption", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(approvalRow).toContainText("approved");
  await nav(page, "Investigations");
  const create = panel(page, "Open an investigation");
  await create.getByLabel("Case title").fill(`Synthetic browser case ${seed}`);
  await create
    .getByLabel("Investigation summary")
    .fill("Review persisted policy decision with linked signals.");
  const signalOptions = create
    .getByLabel("Linked compliance signals")
    .locator("option");
  await expect(signalOptions.first()).toBeAttached();
  await create
    .getByLabel("Linked compliance signals")
    .selectOption((await signalOptions.first().getAttribute("value")) || "");
  await create
    .getByRole("button", { name: "Create investigation", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toContainText(
    `Synthetic browser case ${seed}`,
  );
  const action = panel(page, "Record investigation action");
  await action.getByLabel("Action", { exact: true }).selectOption("ESCALATE");
  await action
    .getByLabel("Evidence reviewed and rationale")
    .fill("Synthetic case escalation from browser validation.");
  await action
    .getByRole("button", { name: "Save action", exact: true })
    .click();
  await expect(page.getByRole("dialog").locator(".badge").first()).toHaveText(
    "escalated",
  );
  await page.getByRole("button", { name: "Close detail", exact: true }).click();
  await nav(page, "Data & reconciliation");
  const broker = panel(page, "Simulated broker feeds");
  await broker.getByLabel("Seed", { exact: true }).fill(String(seed));
  await broker.getByRole("button", { name: "Normalize and ingest" }).click();
  await expect(broker.getByRole("status")).toContainText(
    "completed successfully",
  );
  await broker.getByLabel("Broker source").selectOption("BETA");
  await broker.getByRole("button", { name: "Normalize and ingest" }).click();
  await expect(broker.getByRole("status")).toContainText(
    "completed successfully",
  );
  await panel(page, "Reconcile broker executions")
    .getByRole("button", { name: "Run reconciliation" })
    .click();
  await expect(page.getByRole("dialog")).toContainText("compared");
  await page.getByRole("button", { name: "Close detail", exact: true }).click();
  await nav(page, "Regulatory reporting");
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download audited CSV" }).click();
  expect((await download).suggestedFilename()).toBe(
    "sentinel-synthetic-trades.csv",
  );
  await expect(panel(page, "Report export audit")).toContainText(
    users.ADMIN.username,
  );
  await nav(page, "Pipeline health");
  await expect(panel(page, "Pipeline state")).toContainText("outbox pending");
  const recover = panel(page, "Pipeline recovery");
  await recover.getByLabel("Type RECOVER to execute").fill("RECOVER");
  await recover
    .getByRole("button", { name: "Run recovery", exact: true })
    .click();
  await expect(recover.getByRole("status")).toContainText(
    "completed successfully",
  );
  await nav(page, "Trade surveillance");
  await expect(
    page.getByRole("heading", { name: "Alert queue LIVE" }),
  ).toBeVisible();
  await page.getByLabel("Scenario", { exact: true }).selectOption("normal");
  await page.getByRole("button", { name: "▶ Run replay" }).click();
  await expect(page.getByRole("status")).toContainText("events persisted");
  expect(browserErrors).toEqual([]);
});
test("viewer can inspect all domains but cannot see privileged mutations", async ({
  page,
}) => {
  await login(page, "VIEWER");
  for (const name of [
    "Employee management",
    "Communications",
    "Trading restrictions",
    "Investigations",
    "Data & reconciliation",
    "Regulatory reporting",
    "Pipeline health",
    "Trade surveillance",
  ]) {
    await nav(page, name);
    for (const action of [
      "Import HR profiles",
      "Generate communications",
      "Publish policy",
      "Check permission",
      "Create investigation",
      "Normalize and ingest",
      "Download audited CSV",
      "Run recovery",
      "▶ Run replay",
    ])
      await expect(
        page.getByRole("button", { name: action, exact: true }),
      ).toHaveCount(0);
    await expect(page.locator('[role="alert"]')).toHaveCount(0);
  }
  await page.getByRole("button", { name: "Sign out ↗" }).click();
  await expect(
    page.getByRole("heading", { name: "Sign in to Sentinel" }),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "Sign in to Sentinel" }),
  ).toBeVisible();
});
test("responsive navigation remains usable on mobile", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page);
  await nav(page, "Communications");
  await expect(
    page.getByRole("heading", { name: "Communication archive" }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});
