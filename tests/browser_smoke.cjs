const assert = require("node:assert/strict");
const { chromium } = require("playwright");

const baseURL = "http://localhost:8085";
const email = process.env.BROWSER_TEST_EMAIL;
const password = process.env.BROWSER_TEST_PASSWORD;

async function main() {
  assert.ok(email, "BROWSER_TEST_EMAIL must be set");
  assert.ok(password, "BROWSER_TEST_PASSWORD must be set");

  const browser = await chromium.launch({
    headless: true,
    args: ["--no-sandbox"],
  });
  const page = await browser.newPage();
  await page.addInitScript(() => {
    window.__cspViolations = [];
    window.addEventListener("securitypolicyviolation", event => {
      window.__cspViolations.push({
        directive: event.violatedDirective,
        blockedURI: event.blockedURI,
        sourceFile: event.sourceFile,
        lineNumber: event.lineNumber,
        disposition: event.disposition,
        targetTag: event.target && event.target.tagName,
        targetClass: event.target && event.target.className,
        inlineStyle: event.target && event.target.getAttribute && event.target.getAttribute("style"),
      });
    });
  });
  const pageErrors = [];
  page.on("pageerror", error => pageErrors.push({ message: error.message, stack: error.stack }));

  try {
    const loginResponse = await page.goto(baseURL + "/login");
    assert.equal(loginResponse.status(), 200, "login page must load");
    await page.getByLabel("Email address").fill(email);
    await page.getByLabel("Password").fill(password);
    await page.getByRole("button", { name: "Sign in" }).click();
    await page.waitForURL(baseURL + "/");
    await page.locator("#logout-button").waitFor();
    await page.getByRole("button", { name: "Admin", exact: true }).waitFor();

    const personal = await page.request.get(baseURL + "/api/my-signature");
    assert.equal(personal.status(), 200, "signed-in administrator can load personal signature");

    await page.getByRole("button", { name: "Admin", exact: true }).click();
    await page.getByRole("heading", { name: "Managed Domains" }).waitFor();

    const sections = [
      { tab: "Identity Providers", heading: "Identity Providers" },
      { tab: "Domains", heading: "Managed Domains" },
      { tab: "Signatures", heading: "Signature Catalog" },
      { tab: "Signature Groups", heading: "Signature Groups" },
      { tab: "Rules", heading: "Signature Rules" },
      { tab: "Exports", heading: "Configuration exports" },
    ];
    for (const section of sections) {
      await page.getByRole("button", { name: section.tab, exact: true }).click();
      await page.getByRole("heading", { name: section.heading }).waitFor();
    }

    const signaturePanel = page.locator('[x-data="signatureManager"]');
    await page.getByRole("button", { name: "Signatures", exact: true }).click();
    await signaturePanel.getByRole("button", { name: "+ Add Signature" }).click();
    await signaturePanel.getByLabel("Name").fill("Browser Smoke Signature");
    await signaturePanel.locator('textarea[placeholder="Enter signature HTML"]').fill("<p>Browser smoke version 1</p>");
    await signaturePanel.getByRole("button", { name: "Save Signature", exact: true }).click();

    const signatureRow = signaturePanel.locator("tbody tr").filter({ hasText: "Browser Smoke Signature" });
    await signatureRow.waitFor();
    await signatureRow.getByRole("button", { name: "Edit", exact: true }).click();
    await signaturePanel.locator('textarea[placeholder="Enter signature HTML"]').fill("<p>Browser smoke version 2</p>");
    await signaturePanel.getByRole("button", { name: "Save Signature", exact: true }).click();
    await page.waitForFunction(() => {
      const row = [...document.querySelectorAll('[x-data="signatureManager"] tbody tr')]
        .find(item => item.textContent.includes("Browser Smoke Signature"));
      return row && row.children[3].textContent.trim() === "2";
    });

    const groupPanel = page.locator('[x-data="signatureGroupManager"]');
    await page.getByRole("button", { name: "Signature Groups", exact: true }).click();
    await groupPanel.getByRole("button", { name: "+ Add Group" }).click();
    await groupPanel.getByLabel("Name").fill("Browser Smoke Group");
    await groupPanel.getByRole("button", { name: "Save Group", exact: true }).click();
    const groupCard = groupPanel.locator("div.bg-gray-800.border.border-gray-700.rounded-lg.p-5.space-y-3").first();
    await groupCard.getByRole("heading", { name: "Browser Smoke Group", exact: true }).waitFor();
    const memberSelectors = groupPanel.locator("select");
    await memberSelectors.nth(1).selectOption({ label: "Browser Smoke Group" });
    await memberSelectors.nth(2).selectOption({ label: "Browser Smoke Signature" });
    await groupPanel.getByRole("button", { name: "Add", exact: true }).click();
    await groupCard.getByText("Browser Smoke Signature", { exact: true }).waitFor();

    const rulesPanel = page.locator('[x-data="ruleManager"]');
    await page.getByRole("button", { name: "Rules", exact: true }).click();
    await rulesPanel.getByRole("button", { name: "+ Add Rule" }).click();
    await rulesPanel.getByLabel("Name").fill("Browser Smoke Rule");
    await rulesPanel.getByLabel("Action").selectOption("GROUP");
    const ruleGroupSelect = rulesPanel.getByLabel("Signature Group");
    await ruleGroupSelect.waitFor({ state: "visible" });
    await page.waitForFunction(() => {
      const select = document.querySelector('[x-data="ruleManager"] select[x-model="form.group_id"]');
      return select && [...select.options].some(option => option.textContent.trim() === "Browser Smoke Group");
    });
    await ruleGroupSelect.selectOption({ label: "Browser Smoke Group" });
    await rulesPanel.getByRole("button", { name: "Save Rule", exact: true }).click();
    await rulesPanel.getByText("Browser Smoke Rule", { exact: true }).waitFor();

    // Exercise update and delete paths as well as the initial create flows.
    const createdRuleRow = rulesPanel.locator("tbody tr").filter({ hasText: "Browser Smoke Rule" });
    await createdRuleRow.getByRole("button", { name: "Edit", exact: true }).click();
    await rulesPanel.getByLabel("Priority").fill("25");
    await rulesPanel.getByRole("button", { name: "+ Add condition" }).click();
    const conditionRows = rulesPanel.locator("div.grid.grid-cols-1.md\\:grid-cols-12");
    await conditionRows.last().locator('input[placeholder="Value"]').fill("example.test");
    await rulesPanel.getByRole("button", { name: "Save Rule", exact: true }).click();
    await page.waitForFunction(() => {
      const row = [...document.querySelectorAll('[x-data="ruleManager"] tbody tr')]
        .find(item => item.textContent.includes("Browser Smoke Rule"));
      return row && row.children[1].textContent.trim() === "25"
        && row.children[3].textContent.includes("example.test");
    });

    // Update the group metadata and verify the existing membership survives.
    await groupPanel.getByRole("heading", { name: "Browser Smoke Group", exact: true }).waitFor({ state: "visible" });
    await groupPanel.getByRole("button", { name: "Edit", exact: true }).click();
    const groupForm = groupPanel.locator("div[x-show='editing']");
    await groupForm.getByLabel("Name").fill("Browser Smoke Group Updated");
    await groupForm.getByLabel("Slug").fill("browser-smoke-group-updated");
    await groupForm.getByRole("button", { name: "Save Group", exact: true }).click();
    await groupPanel.getByRole("heading", { name: "Browser Smoke Group Updated", exact: true }).waitFor();
    await groupPanel.getByText("Browser Smoke Signature", { exact: true }).waitFor();

    // Update a group member's weight and verify the UI remains operational.
    const memberWeight = groupPanel.locator('input[type="number"]').first();
    await memberWeight.fill("4");
    await memberWeight.dispatchEvent("change");

    // Update the signature metadata without creating an extra HTML version.
    await page.getByRole("button", { name: "Signatures", exact: true }).click();
    const updatedSignatureRow = signaturePanel.locator("tbody tr").filter({ hasText: "Browser Smoke Signature" });
    await updatedSignatureRow.getByRole("button", { name: "Edit", exact: true }).click();
    await signaturePanel.getByLabel("Name").fill("Browser Smoke Signature Updated");
    await signaturePanel.getByRole("button", { name: "Save Signature", exact: true }).click();
    await signaturePanel.getByText("Browser Smoke Signature Updated", { exact: true }).waitFor();

    // Remove the rule and group again so this test also covers destructive UI paths.
    await page.getByRole("button", { name: "Rules", exact: true }).click();
    page.once("dialog", dialog => dialog.accept());
    const updatedRuleRow = rulesPanel.locator("tbody tr").filter({ hasText: "Browser Smoke Rule" });
    await updatedRuleRow.getByRole("button", { name: "Delete", exact: true }).click();
    await updatedRuleRow.waitFor({ state: "detached" });

    await page.getByRole("button", { name: "Signature Groups", exact: true }).click();
    page.once("dialog", dialog => dialog.accept());
    await groupPanel.getByRole("button", { name: "Delete", exact: true }).click();
    await groupPanel.getByRole("heading", { name: "Browser Smoke Group Updated", exact: true }).waitFor({ state: "detached" });

    await page.getByRole("button", { name: "Signatures", exact: true }).click();
    await signaturePanel.getByText("Browser Smoke Signature Updated", { exact: true }).waitFor();

    assert.deepEqual(pageErrors, [], "browser runtime must not raise uncaught JavaScript errors");
    const cspViolations = await page.evaluate(() => window.__cspViolations || []);
    const inlineStyleElements = await page.locator("[style]").evaluateAll(elements => elements.map(element => ({ tag: element.tagName, className: element.className, style: element.getAttribute("style") })).slice(0, 30));
    process.stdout.write("CSP report-only browser violations: " + JSON.stringify(cspViolations) + "\\n");
    process.stdout.write("Inline style elements: " + JSON.stringify(inlineStyleElements) + "\\n");
    await page.locator("#logout-button").click();
    await page.waitForURL("**/login");
    await page.getByLabel("Email address").waitFor();
    const protectedPage = await page.request.get(baseURL + "/");
    assert.equal(protectedPage.status(), 200);
    assert.match(protectedPage.url(), /\/login$/, "logged-out session must be redirected to login");
    process.stdout.write("Browser smoke passed: login, personal API, all admin sections, signature creation/version update, group creation/membership, GROUP rule creation, logout and session invalidation.\n");
  } finally {
    await browser.close();
  }
}

main().catch(error => {
  process.stderr.write((error && error.stack) || String(error));
  process.exitCode = 1;
});
