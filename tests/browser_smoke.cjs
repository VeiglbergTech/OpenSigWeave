const assert = require("node:assert/strict");
const { chromium } = require("playwright");
const fs = require("node:fs");

const baseURL = "http://localhost:8085";
const email = process.env.BROWSER_TEST_EMAIL;
const password = process.env.BROWSER_TEST_PASSWORD;

async function main() {
  assert.ok(email, "BROWSER_TEST_EMAIL must be set");
  assert.ok(password, "BROWSER_TEST_PASSWORD must be set");
  assert.ok(process.env.BROWSER_TEST_NORMAL_USER_SESSION, "BROWSER_TEST_NORMAL_USER_SESSION must be set");
  assert.ok(process.env.BROWSER_TEST_RESTRICTED_USER_SESSION, "BROWSER_TEST_RESTRICTED_USER_SESSION must be set");

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

    // Exercise the actual administrator export UI and verify a ZIP download is produced.
    const exportPanel = page.locator('[x-data="adminExportManager"]');
    const exportResponse = page.waitForResponse(response =>
      response.url().includes("/api/admin/exports?")
      && response.status() === 200
    );
    const exportDownload = page.waitForEvent("download");
    await exportPanel.getByRole("button", { name: "Download selected components" }).click();
    const [exportResponseResult, exportDownloadResult] = await Promise.all([exportResponse, exportDownload]);
    assert.match(exportResponseResult.headers()["content-type"], /application\/zip/);
    assert.equal(exportDownloadResult.suggestedFilename(), "opensigweave-component-export.zip");
    const exportPath = await exportDownloadResult.path();
    assert.ok(exportPath, "administrator export must produce a downloadable file");
    const exportBytes = fs.readFileSync(exportPath);
    assert.equal(exportBytes.subarray(0, 4).toString("hex"), "504b0304", "download must be a ZIP archive");

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
    const groupCard = groupPanel.locator("div.bg-gray-800.border.border-gray-700.rounded-lg.p-5.space-y-3").filter({ has: page.getByRole("heading", { name: "Browser Smoke Group", exact: true }) });
    await groupCard.getByRole("heading", { name: "Browser Smoke Group", exact: true }).waitFor();
    const memberSelectors = groupPanel.locator("select");
    await memberSelectors.nth(1).selectOption({ label: "Browser Smoke Group" });
    await memberSelectors.nth(2).selectOption({ label: "Browser Smoke Signature" });
    await groupPanel.getByRole("button", { name: "Add", exact: true }).click();
    await groupCard.locator("span.flex-1.truncate").filter({ hasText: "Browser Smoke Signature" }).waitFor();

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

    // Return to the group tab before interacting with its hidden Alpine panel.
    await page.getByRole("button", { name: "Signature Groups", exact: true }).click();

    // Update the group metadata and verify the existing membership survives.
    await groupPanel.getByRole("heading", { name: "Browser Smoke Group", exact: true }).waitFor({ state: "visible" });
    await groupCard.getByRole("button", { name: "Edit", exact: true }).click();
    const groupForm = groupPanel.locator("div[x-show='editing']");
    await groupForm.getByLabel("Name").fill("Browser Smoke Group Updated");
    await groupForm.getByLabel("Slug").fill("browser-smoke-group-updated");
    const groupUpdateResponse = page.waitForResponse(response =>
      response.request().method() === "PUT"
      && response.url().includes("/api/admin/signature-groups/")
      && response.status() === 200
    );
    await groupForm.getByRole("button", { name: "Save Group", exact: true }).click();
    await groupUpdateResponse;
    const updatedGroupCard = groupPanel.locator("div.grid.grid-cols-1.xl\\:grid-cols-2 > div")
      .filter({ hasText: "Browser Smoke Group Updated" });
    await updatedGroupCard.getByText("Browser Smoke Group Updated", { exact: true }).waitFor();
    const updatedMemberLabel = updatedGroupCard.locator("span.flex-1.truncate").filter({ hasText: "Browser Smoke Signature" });
    await updatedMemberLabel.waitFor();

    // Update a group member's weight and verify the UI remains operational.
    const memberWeight = updatedGroupCard.locator('input[type="number"]').first();
    await memberWeight.fill("4");
    await memberWeight.dispatchEvent("change");

    // Exercise explicit membership removal independently from group deletion.
    const updatedMemberRow = updatedMemberLabel.locator("xpath=..");
    page.once("dialog", dialog => dialog.accept());
    await updatedMemberRow.getByRole("button", { name: "Remove", exact: true }).click();
    await updatedMemberLabel.waitFor({ state: "detached" });

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
    await updatedGroupCard.getByRole("button", { name: "Delete", exact: true }).click();
    await groupPanel.getByRole("heading", { name: "Browser Smoke Group Updated", exact: true }).waitFor({ state: "detached" });

    await page.getByRole("button", { name: "Signatures", exact: true }).click();
    await signaturePanel.getByText("Browser Smoke Signature Updated", { exact: true }).waitFor();

    // Exercise normal-user authorization with signed, non-admin browser sessions.
    // The CI workflow signs these sessions with the same secret used by SessionMiddleware.
    const normalContext = await browser.newContext();
    await normalContext.addCookies([{
      name: "opensigweave-session",
      value: process.env.BROWSER_TEST_NORMAL_USER_SESSION,
      domain: 'localhost',
      path: '/',
      httpOnly: true,
      secure: false,
      sameSite: "Lax",
    }]);
    const normalPage = await normalContext.newPage();
    const normalHome = await normalPage.goto(baseURL + "/");
    assert.equal(normalHome.status(), 200, "regular signature user can open the portal");
    await normalPage.locator("#logout-button").waitFor();
    assert.equal(await normalPage.getByRole("button", { name: "Admin", exact: true }).count(), 0,
      "regular users must not see administrator navigation");
    const normalSignature = await normalPage.request.get(baseURL + "/api/my-signature");
    assert.equal(normalSignature.status(), 200, "regular user can read only their personal signature");
    assert.equal((await normalSignature.json()).html_content, "");
    const deniedAdminApi = await normalPage.request.get(baseURL + "/api/admin/signatures");
    assert.equal(deniedAdminApi.status(), 403, "regular user must be denied administrator APIs");
    const savedPersonalSignature = await normalPage.request.post(baseURL + "/api/my-signature", {
      headers: { "X-CSRF-Token": "ci-normal-user-csrf-token" },
      data: { html_content: "<p>Normal user browser signature</p>" },
    });
    assert.equal(savedPersonalSignature.status(), 200,
      "regular user can save a personal override when the assigned domain permits it");
    const savedSignature = await normalPage.request.get(baseURL + "/api/my-signature");
    assert.equal((await savedSignature.json()).html_content, "<p>Normal user browser signature</p>");
    await normalContext.close();

    const restrictedContext = await browser.newContext();
    await restrictedContext.addCookies([{
      name: "opensigweave-session",
      value: process.env.BROWSER_TEST_RESTRICTED_USER_SESSION,
      domain: 'localhost',
      path: '/',
      httpOnly: true,
      secure: false,
      sameSite: "Lax",
    }]);
    const restrictedPage = await restrictedContext.newPage();
    const restrictedHome = await restrictedPage.goto(baseURL + "/");
    assert.equal(restrictedHome.status(), 200, "restricted-domain user can open the portal");
    await restrictedPage.getByRole("heading", { name: "Editing Disabled" }).waitFor();
    const disabledSaveButton = restrictedPage.locator("#save-user-signature");
    assert.equal(await disabledSaveButton.isDisabled(), true,
      "domain override policy must disable personal signature editing in the UI");
    const deniedOverrideSave = await restrictedPage.request.post(baseURL + "/api/my-signature", {
      headers: { "X-CSRF-Token": "ci-restricted-user-csrf-token" },
      data: { html_content: "<p>Must be rejected</p>" },
    });
    assert.equal(deniedOverrideSave.status(), 403,
      "domain override policy must also be enforced by the API");
    await restrictedContext.close();

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
    process.stdout.write("Browser smoke passed: administrator login, personal API, all admin sections, export ZIP download, signature create/update/versioning, group create/update/member-weight update/membership removal/group delete, GROUP rule create/update/delete, regular-user authorization, domain override allow/deny policies, logout and session invalidation.\n");
  } finally {
    await browser.close();
  }
}

main().catch(error => {
  process.stderr.write((error && error.stack) || String(error));
  process.exitCode = 1;
});
