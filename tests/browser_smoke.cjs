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

    assert.deepEqual(pageErrors, [], "browser runtime must not raise uncaught JavaScript errors");
    const cspViolations = await page.evaluate(() => window.__cspViolations || []);
    process.stdout.write("CSP report-only browser violations: " + JSON.stringify(cspViolations) + "\\n");
    await page.locator("#logout-button").click();
    await page.waitForURL("**/login");
    await page.getByLabel("Email address").waitFor();
    const protectedPage = await page.request.get(baseURL + "/");
    assert.equal(protectedPage.status(), 200);
    assert.match(protectedPage.url(), /\/login$/, "logged-out session must be redirected to login");
    process.stdout.write("Browser smoke passed: login, personal API, all admin sections, logout and session invalidation.\n");
  } finally {
    await browser.close();
  }
}

main().catch(error => {
  process.stderr.write((error && error.stack) || String(error));
  process.exitCode = 1;
});
