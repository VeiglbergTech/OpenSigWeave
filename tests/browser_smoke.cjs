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
  // Bound browser operations so CI reports the stalled action before its outer timeout.
  page.setDefaultTimeout(15000);
  page.setDefaultNavigationTimeout(30000);
  page.on("requestfailed", request => {
    const failure = request.failure();
    // Navigation intentionally aborts in-flight Alpine refresh requests.
    // Keep genuine network failures in CI logs without reporting these as faults.
    if (failure && failure.errorText === "net::ERR_ABORTED") return;
    process.stderr.write("[browser-smoke] request failed: " + request.method() + " " + request.url()
      + (failure ? " (" + failure.errorText + ")" : "") + "\n");
  });
  const checkpoint = name => process.stdout.write("[browser-smoke] " + name + "\n");
  async function assertFloatingDialog(dialog, label) {
    await dialog.waitFor({ state: "visible" });
    const layout = await dialog.evaluate(element => {
      const rect = element.getBoundingClientRect();
      return {
        position: window.getComputedStyle(element).position,
        width: rect.width,
        height: rect.height,
        left: rect.left,
        right: rect.right,
        top: rect.top,
        bottom: rect.bottom,
        viewportWidth: window.innerWidth,
        viewportHeight: window.innerHeight,
      };
    });
    assert.equal(layout.position, "fixed", label + " must be a floating dialog, not an inline form");
    assert.ok(layout.width > 0 && layout.height > 0, label + " must have visible dimensions");
    assert.ok(layout.left >= -1 && layout.right <= layout.viewportWidth + 1,
      label + " must fit horizontally inside the viewport: " + JSON.stringify(layout));
    assert.ok(layout.top >= -1 && layout.bottom <= layout.viewportHeight + 1,
      label + " must fit vertically inside the viewport: " + JSON.stringify(layout));
  }
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
    checkpoint("login: open");
    const loginResponse = await page.goto(baseURL + "/login");
    assert.equal(loginResponse.status(), 200, "login page must load");
    await page.getByLabel("Email address").fill(email);
    await page.getByLabel("Password").fill(password);
    await page.getByRole("button", { name: "Sign in" }).click();
    await page.waitForURL(baseURL + "/");
    await page.locator("#logout-button").waitFor();
    await page.getByRole("button", { name: "Admin", exact: true }).waitFor();

    checkpoint("login: complete");
    // Exercise the self-hosted CKEditor 5 surface and verify synchronization to the source textarea.
    const personalEditor = page.locator("#user-rich-editor .ck-editor__editable");
    await personalEditor.waitFor({ state: "visible" });
    await personalEditor.fill("CSP editor smoke");
    await personalEditor.press("Control+A");
    // Use CKEditor's keyboard shortcut instead of a localized toolbar label.
    // Toolbar aria-labels may differ across CKEditor builds and translations.
    await personalEditor.press("Control+B");
    await personalEditor.press("B");
    await page.waitForFunction(() =>
      document.querySelector("#raw-html-editor").value.includes("<strong>B</strong>")
    );
    assert.match(await personalEditor.innerHTML(), /<strong>B<\/strong>/,
      "CKEditor formatting must remain functional with the current report-only CSP");

    // Verify that the rich editor round-trips common email-signature markup
    // independently of the personal editor's current contents.
    const richMarkupRoundTrip = await page.evaluate(async () => {
      const host = document.createElement("div");
      document.body.appendChild(host);
      let instance;
      try {
        instance = await window.OpenSigWeaveEditor.create(host, {
          initialData:
            '<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">' +
            '<tbody><tr><td valign="top" style="padding:8px;">' +
            '<center><font face="Arial" color="#333333" size="2">' +
            '<small>Small</small> <sub>subscript</sub> <sup>superscript</sup> ' +
            '<code>code</code><pre>preformatted</pre>' +
            '</font></center></td></tr></tbody></table>'
        });
        return instance.getData();
      } finally {
        if (instance) await instance.destroy();
        host.remove();
      }
    });
    for (const markup of ["<table", "<tbody", "<td", "<center", "<font", "<small", "<sub", "<sup", "<code", "<pre"]) {
      assert.ok(richMarkupRoundTrip.includes(markup),
        "rich editor must retain supported email markup: " + markup + "; output=" + richMarkupRoundTrip);
    }
    assert.match(richMarkupRoundTrip, /width="600"/,
      "rich editor must retain supported table dimensions");
    assert.match(richMarkupRoundTrip, /valign="top"/,
      "rich editor must retain supported table-cell alignment");

    checkpoint("personal editor: complete");
    const personal = await page.request.get(baseURL + "/api/my-signature", { timeout: 15000 });
    assert.equal(personal.status(), 200, "signed-in administrator can load personal signature");

    await page.getByRole("button", { name: "Admin", exact: true }).click();
    await page.getByRole("heading", { name: "Managed Domains" }).waitFor();

    checkpoint("admin: open");
    const sections = [
      { tab: "Identity Providers", heading: "Identity Providers" },
      { tab: "Domains", heading: "Managed Domains" },
      { tab: "Signatures", heading: "Signature Catalog" },
      { tab: "Signature Groups", heading: "Signature Groups" },
      { tab: "Rules", heading: "Signature Rules" },
      { tab: "Scheduling", heading: "Signature Scheduling" },
      { tab: "Branding", heading: "Branding" },
      { tab: "Exports", heading: "Configuration exports" },
      { tab: "Imports", heading: "Configuration imports" },
    ];
    for (const section of sections) {
      await page.getByRole("button", { name: section.tab, exact: true }).click();
      await page.getByRole("heading", { name: section.heading }).waitFor();
    }

    // Return to Exports after visiting every administrator section.
    await page.getByRole("button", { name: "Exports", exact: true }).click();

    checkpoint("admin sections: complete");
    // Every create/edit workspace must float above the overview instead of
    // expanding the current page. Also verify the provider dialog can be cancelled.
    await page.getByRole("button", { name: "Identity Providers", exact: true }).click();
    const providerPanel = page.locator('[x-data="identityProviderManager"]');
    await providerPanel.getByRole("button", { name: "+ Add Identity Provider" }).click();
    const providerDialog = providerPanel.locator('[x-show="editing"][role="dialog"]');
    await assertFloatingDialog(providerDialog, "identity provider editor");
    await providerDialog.press("Escape");
    await providerDialog.waitFor({ state: "hidden" });
    await page.getByRole("button", { name: "Exports", exact: true }).click();

    // Exercise the actual administrator export UI and verify a ZIP download is produced.
    const exportPanel = page.locator('[x-data="adminExportManager"]');
    const exportButton = exportPanel.getByRole("button", { name: "Download selected components" });
    assert.equal(await exportButton.isDisabled(), false, "export button must be enabled");
    const exportResponse = page.waitForResponse(response =>
      response.url().includes("/api/admin/exports?")
    );
    const exportDownload = page.waitForEvent("download");
    await exportButton.click();
    const exportResponseResult = await exportResponse;
    if (exportResponseResult.status() !== 200) {
      const failureBody = await exportResponseResult.text();
      throw new Error("Administrator export returned HTTP " + exportResponseResult.status() + ": " + failureBody);
    }
    const exportDownloadResult = await exportDownload;
    assert.match(exportResponseResult.headers()["content-type"], /application\/zip/);
    assert.equal(exportDownloadResult.suggestedFilename(), "opensigweave-component-export.zip");
    const exportPath = await exportDownloadResult.path();
    assert.ok(exportPath, "administrator export must produce a downloadable file");
    const exportBytes = fs.readFileSync(exportPath);
    assert.equal(exportBytes.subarray(0, 4).toString("hex"), "504b0304", "download must be a ZIP archive");

    checkpoint("export: complete");
    // Validate the downloaded archive through the real import UI. Existing matching
    // domains/providers should be reusable and the preview must not write anything.
    await page.getByRole("button", { name: "Imports", exact: true }).click();
    const importPanel = page.locator('[x-data="adminImportManager"]');
    await importPanel.locator("#admin-import-file").setInputFiles(exportPath);
    const importPreviewButton = importPanel.getByRole("button", { name: "Validate and preview" });
    assert.equal(await importPreviewButton.isDisabled(), false, "import preview button must be enabled after selecting a file");
    const importResponsePromise = page.waitForResponse(response =>
      response.url().includes("/api/admin/imports/preview")
    );
    await importPreviewButton.click();
    const importResponse = await importResponsePromise;
    const importResponseBody = await importResponse.text();
    assert.equal(importResponse.status(), 200, "import preview API failed: " + importResponseBody);
    const importPreviewData = JSON.parse(importResponseBody);
    assert.equal(importPreviewData.can_apply, true, JSON.stringify(importPreviewData));
    await importPanel.getByText("Archive validated. No conflicts detected.", { exact: true }).waitFor();
    assert.equal(await importPanel.getByText("New: 0 · Reused: 2").count(), 1,
      "preview should identify both matching domains as reusable");
    assert.equal(await importPanel.getByText("New: 0 · Reused: 1").count(), 1,
      "preview should identify the matching identity-provider metadata as reusable");

    checkpoint("import preview: complete");
    const signaturePanel = page.locator('[x-data="signatureManager"]');
    await page.getByRole("button", { name: "Signatures", exact: true }).click();
    await signaturePanel.getByRole("button", { name: "+ Add Signature" }).click();
    const signatureDialog = signaturePanel.locator('[x-show="editing"][role="dialog"]');
    await assertFloatingDialog(signatureDialog, "signature editor");
    assert.equal(await signatureDialog.getByLabel("Mode").count(), 0,
      "signature editor must not expose the retired mode selector");
    assert.equal(await signatureDialog.getByRole("option", {
      name: new RegExp("^(" + ["AU" + "TO", "INTER" + "NAL", "PROF" + "ESSIONAL"].join("|") + ")$", "i"),
    }).count(), 0, "signature editor must not list retired mode values");
    assert.equal(await signatureDialog.locator('[name="mode"], [name="pool_name"], [name="variants"]').count(), 0,
      "signature editor must not submit retired selector fields");
    await signaturePanel.getByLabel("Name").fill("Browser Smoke Signature");
    await signaturePanel.locator("#signature-template-editor .ck-editor__editable").fill("Browser smoke version 1");
    await signaturePanel.getByRole("button", { name: "Save Signature", exact: true }).click();

    const signatureRow = signaturePanel.locator("tbody tr").filter({ hasText: "Browser Smoke Signature" });
    await signatureRow.waitFor();
    await signatureRow.getByRole("button", { name: "Edit", exact: true }).click();
    const signatureEditEditor = signaturePanel.locator("#signature-template-editor .ck-editor__editable");
    await signatureEditEditor.waitFor({ state: "visible" });
    assert.match(await signatureEditEditor.innerHTML(), /Browser smoke version 1/,
      "editing an existing signature must load the latest stored HTML version");
    const versionSelector = signaturePanel.locator('select[x-model="selectedVersionId"]');
    assert.equal(await versionSelector.locator("option").count(), 1,
      "newly created signature must expose its first version in version history");

    // Clicking the editor must focus the editable surface. A surrounding label used
    // to redirect clicks to the version selector, making typing possible only after tabbing.
    const selectedVersionBeforeEditorClick = await versionSelector.inputValue();
    await signatureEditEditor.click();
    assert.equal(await signatureEditEditor.evaluate(element =>
      element === document.activeElement || element.contains(document.activeElement)
    ), true, "clicking the signature editor must focus the editor instead of version history");
    assert.equal(await versionSelector.inputValue(), selectedVersionBeforeEditorClick,
      "clicking the signature editor must not activate or change version history");

    // The global preview overlay must be above the floating signature editor. A
    // lower stacking layer previously exposed editor/version controls underneath.
    const signaturePreviewModal = page.locator('[x-data="previewModal"]');
    await page.evaluate(() => {
      window.dispatchEvent(new CustomEvent("open-preview", {
        detail: { html: "<p>Browser smoke preview overlay</p>" }
      }));
    });
    await signaturePreviewModal.waitFor({ state: "visible" });
    const previewLayering = await page.evaluate(() => {
      const preview = document.querySelector('[x-data="previewModal"]');
      const editor = document.querySelector('[x-data="signatureManager"] [x-show="editing"][role="dialog"]');
      return {
        preview: Number.parseInt(getComputedStyle(preview).zIndex || "0", 10),
        editor: Number.parseInt(getComputedStyle(editor).zIndex || "0", 10),
        srcdoc: document.querySelector("#signature-preview-frame")?.getAttribute("srcdoc") || ""
      };
    });
    assert.ok(previewLayering.preview > previewLayering.editor,
      "signature preview must be layered above the floating signature editor: " + JSON.stringify(previewLayering));
    assert.match(previewLayering.srcdoc, /Browser smoke preview overlay/,
      "signature preview iframe must receive the requested HTML");
    await page.keyboard.press("Escape");
    await signaturePreviewModal.waitFor({ state: "hidden" });
    await signatureEditEditor.click();
    assert.equal(await signatureEditEditor.evaluate(element =>
      element === document.activeElement || element.contains(document.activeElement)
    ), true, "editor must regain focus directly after closing the preview");

    // Exercise the real CKEditor upload paths, not only the backend endpoint.
    // These fixtures are valid 1x1 images so Chromium and CKEditor both process them as images.
    const imageFixtures = {
      png: {
        name: "browser-smoke-drag.png",
        type: "image/png",
        base64: "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC",
      },
      jpeg: {
        name: "browser-smoke-paste.jpg",
        type: "image/jpeg",
        base64: "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDi6KKK+ZP3E//Z",
      },
      webp: {
        name: "browser-smoke-toolbar.webp",
        type: "image/webp",
        base64: "UklGRjwAAABXRUJQVlA4IDAAAADQAQCdASoBAAEAAUAmJaACdLoB+AADsAD+8ut//NgVzXPv9//S4P0uD9Lg/9KQAAA=",
      },
    };
    const waitForImageUpload = () => page.waitForResponse(response =>
      response.request().method() === "POST"
      && response.url().endsWith("/api/admin/signature-assets")
      && response.status() === 201
    );

    // 1) Emulate an external file-manager drop through Chromium's drag protocol.
    // Browsers deliberately strip script-injected Files from a DOM-source drag,
    // so dragTo() cannot test a real external file drop. CDP dispatches trusted
    // OS-style drag events containing the on-disk PNG file instead.
    const dragFixtureDirectory = fs.mkdtempSync("/tmp/opensigweave-file-drop-");
    const dragFixturePath = dragFixtureDirectory + "/" + imageFixtures.png.name;
    fs.writeFileSync(dragFixturePath, Buffer.from(imageFixtures.png.base64, "base64"));
    await signatureEditEditor.evaluate(element => {
      element.addEventListener("drop", event => {
        window.__signatureNativeFileDrop = {
          isTrusted: event.isTrusted,
          fileNames: Array.from(event.dataTransfer.files, file => file.name),
        };
      }, { capture: true, once: true });
    });
    const dropBounds = await signatureEditEditor.boundingBox();
    assert.ok(dropBounds, "signature editor must have a visible drop surface");
    const dragPoint = {
      x: Math.round(dropBounds.x + Math.min(80, dropBounds.width / 2)),
      y: Math.round(dropBounds.y + Math.min(40, dropBounds.height / 2)),
    };
    const dragData = {
      items: [],
      files: [dragFixturePath],
      dragOperationsMask: 1, // copy
    };
    const dragClient = await page.context().newCDPSession(page);
    let dragAssetPath;
    try {
      const dragUpload = waitForImageUpload();
      // Retain the response waiter without masking a native-drop assertion.
      void dragUpload.catch(() => {});
      await dragClient.send("Input.dispatchDragEvent", {
        type: "dragEnter", ...dragPoint, data: dragData,
      });
      await dragClient.send("Input.dispatchDragEvent", {
        type: "dragOver", ...dragPoint, data: dragData,
      });
      await dragClient.send("Input.dispatchDragEvent", {
        type: "drop", ...dragPoint, data: dragData,
      });
      const nativeDrop = await page.evaluate(() => window.__signatureNativeFileDrop);
      assert.equal(nativeDrop?.isTrusted, true,
        "OS-style file drop must reach CKEditor as a browser-trusted drop");
      assert.deepEqual(nativeDrop.fileNames, [imageFixtures.png.name],
        "OS-style file drop must deliver the real PNG file to CKEditor");
      await dragUpload;
      // CDP's external-file dispatch can evict the response body from the
      // inspector cache. Check the persisted URL in CKEditor's rendered DOM
      // instead of depending on Playwright's Network.getResponseBody().
      const insertedImage = signatureEditEditor.locator('img[src*="/signature-assets/"]').first();
      await insertedImage.waitFor({ state: "visible" });
      dragAssetPath = new URL(await insertedImage.getAttribute("src"), baseURL).pathname;
    } finally {
      await dragClient.detach();
      fs.rmSync(dragFixtureDirectory, { recursive: true, force: true });
    }
    await page.waitForFunction(path =>
      document.querySelector("#signature-html-source").value.includes(path), dragAssetPath);
    await signatureEditEditor.click();
    await page.keyboard.press("Control+End");
    await page.keyboard.type(" ");

    // 2) Clipboard image paste. CKEditor routes clipboard image files through the same upload adapter.
    const pasteUpload = waitForImageUpload();
    await signatureEditEditor.evaluate((element, fixture) => {
      const binary = atob(fixture.base64);
      const bytes = Uint8Array.from(binary, character => character.charCodeAt(0));
      const transfer = new DataTransfer();
      transfer.items.add(new File([bytes], fixture.name, { type: fixture.type }));
      element.dispatchEvent(new ClipboardEvent("paste", {
        bubbles: true,
        cancelable: true,
        clipboardData: transfer,
      }));
    }, imageFixtures.jpeg);
    const pasteUploadResponse = await pasteUpload;
    const pasteAssetPath = (await pasteUploadResponse.json()).url;
    await page.waitForFunction(path =>
      document.querySelector("#signature-html-source").value.includes(path), pasteAssetPath);
    await signatureEditEditor.click();
    await page.keyboard.press("Control+End");
    await page.keyboard.type(" ");

    // 3) The normal CKEditor upload-image control. Exercise the actual browser file
    // chooser instead of depending on CKEditor's private DOM classes for its hidden input.
    const signatureToolbar = signaturePanel.locator("#signature-template-editor .ck-toolbar");
    const toolbarUploadButton = signatureToolbar.getByRole("button", { name: /image/i }).first();
    await toolbarUploadButton.waitFor({ state: "visible" });
    // Put the caret after the previously pasted image. Otherwise CKEditor can keep
    // the image object selected and a subsequent image action may operate on that
    // selection instead of inserting at a fresh text position.
    await signatureEditEditor.click();
    await page.keyboard.press("Control+End");
    const fileChooserPromise = page.waitForEvent("filechooser");
    const toolbarUpload = waitForImageUpload();
    await toolbarUploadButton.click();
    const fileChooser = await fileChooserPromise;
    await fileChooser.setFiles({
      name: imageFixtures.webp.name,
      mimeType: imageFixtures.webp.type,
      buffer: Buffer.from(imageFixtures.webp.base64, "base64"),
    });
    const toolbarUploadResponse = await toolbarUpload;
    const toolbarAssetPath = (await toolbarUploadResponse.json()).url;
    await page.waitForFunction(path =>
      document.querySelector("#signature-html-source").value.includes(path)
    , toolbarAssetPath);

    const assetPaths = [dragAssetPath, pasteAssetPath, toolbarAssetPath];
    assert.equal(new Set(assetPaths).size, 3, "three distinct images must produce three asset URLs");
    await signatureEditEditor.locator('img[src*="/signature-assets/"]').nth(2).waitFor();
    assert.equal(await signatureEditEditor.locator('img[src*="/signature-assets/"]').count(), 3,
      "drag, paste and toolbar upload must all insert images into the signature");

    await page.waitForFunction(paths => {
      const value = document.querySelector("#signature-html-source").value;
      return paths.every(path => value.includes(path));
    }, assetPaths);
    const uploadedSourceHtml = await signaturePanel.locator("#signature-html-source").inputValue();
    for (const assetPath of assetPaths) {
      assert.match(assetPath, /^\/signature-assets\/[0-9a-f]{64}\.(?:png|jpg|webp)$/);
      assert.ok(uploadedSourceHtml.includes(baseURL + assetPath),
        "saved editor HTML must use an absolute OpenSigWeave asset URL: " + assetPath);
    }
    assert.doesNotMatch(uploadedSourceHtml, /src=["'](?:data:|blob:)/i,
      "signature HTML must not persist data: or blob: image URLs");

    // Save the image-bearing HTML as version 2 and then reopen it from storage.
    await signaturePanel.getByRole("button", { name: "Save Signature", exact: true }).click();
    await page.waitForFunction(() => {
      const row = [...document.querySelectorAll('[x-data="signatureManager"] tbody tr')]
        .find(item => item.textContent.includes("Browser Smoke Signature"));
      return row && row.children[2].textContent.trim() === "2";
    });
    await signatureRow.getByRole("button", { name: "Edit", exact: true }).click();
    const persistedImageEditor = signaturePanel.locator("#signature-template-editor .ck-editor__editable");
    await persistedImageEditor.locator('img[src*="/signature-assets/"]').nth(2).waitFor();
    const persistedImageSrcs = await persistedImageEditor.locator('img[src*="/signature-assets/"]').evaluateAll(
      images => images.map(image => image.getAttribute("src"))
    );
    assert.equal(persistedImageSrcs.length, 3, "all uploaded images must survive save and reopen");
    for (const src of persistedImageSrcs) {
      assert.ok(src.startsWith(baseURL + "/signature-assets/"), "persisted image URL must remain absolute");
      assert.doesNotMatch(src, /^(?:data:|blob:)/i);
    }

    // Mail clients do not have an administrator session, so every persisted asset must
    // remain fetchable from an anonymous browser context.
    const anonymousContext = await browser.newContext();
    try {
      const anonymousPage = await anonymousContext.newPage();
      for (const src of persistedImageSrcs) {
        const response = await anonymousPage.request.get(src);
        assert.equal(response.status(), 200, "signature asset must be public without an admin session");
        assert.match(response.headers()["content-type"] || "", /^image\/(?:png|jpeg|webp)$/);
        assert.match(response.headers()["cache-control"] || "", /public/);
      }
    } finally {
      await anonymousContext.close();
    }

    const csrfToken = await page.locator("body").getAttribute("data-csrf-token");
    assert.ok(csrfToken, "administrator page must expose a CSRF token");
    const signatureCatalog = await (await page.request.get(baseURL + "/api/admin/signatures")).json();
    const imageSignature = signatureCatalog.find(item => item.name === "Browser Smoke Signature");
    assert.ok(imageSignature, "image-bearing signature must exist in the catalog");

    // A second signature may reuse an immutable asset. Removing image references from
    // the first signature must never delete that shared file.
    const sharedCreateResponse = await page.request.post(baseURL + "/api/admin/signatures", {
      headers: { "X-CSRF-Token": csrfToken },
      data: {
        name: "Browser Smoke Shared Asset",
        domain_ids: [],
        is_active: true,
        html_content: '<p>Shared asset <img src="' + persistedImageSrcs[0] + '" alt="Shared asset"></p>',
      },
    });
    assert.equal(sharedCreateResponse.status(), 201, await sharedCreateResponse.text());
    const sharedSignature = await sharedCreateResponse.json();

    await persistedImageEditor.fill("Browser smoke version 3 without image references");
    await signaturePanel.getByRole("button", { name: "Save Signature", exact: true }).click();
    await page.waitForFunction(() => {
      const row = [...document.querySelectorAll('[x-data="signatureManager"] tbody tr')]
        .find(item => item.textContent.includes("Browser Smoke Signature"));
      return row && row.children[2].textContent.trim() === "3";
    });

    const stillPublic = await page.request.get(persistedImageSrcs[0]);
    assert.equal(stillPublic.status(), 200,
      "removing an image reference must not delete an immutable shared asset");
    const sharedVersionResponse = await page.request.get(
      baseURL + "/api/admin/signatures/" + encodeURIComponent(sharedSignature.id)
      + "/versions/" + encodeURIComponent(sharedSignature.version_id)
    );
    assert.equal(sharedVersionResponse.status(), 200);
    assert.ok((await sharedVersionResponse.json()).html_content.includes(persistedImageSrcs[0]),
      "another signature must keep its shared image reference intact");

    // Older versions must still render their images after a newer version removes them.
    await signatureRow.getByRole("button", { name: "Edit", exact: true }).click();
    const historicalEditor = signaturePanel.locator("#signature-template-editor .ck-editor__editable");
    const historicalVersionSelector = signaturePanel.locator('select[x-model="selectedVersionId"]');
    await page.waitForFunction(() =>
      document.querySelectorAll('[x-data="signatureManager"] select[x-model="selectedVersionId"] option').length === 3
    );
    assert.equal(await historicalVersionSelector.locator("option").count(), 3,
      "signature must expose all three stored versions");
    const versionTwoId = await historicalVersionSelector.locator("option").evaluateAll(options => {
      const option = options.find(item => (item.textContent || "").trim().startsWith("v2"));
      return option ? option.value : "";
    });
    assert.ok(versionTwoId, "image-bearing version 2 must remain available");
    await historicalVersionSelector.selectOption(versionTwoId);
    await historicalEditor.locator('img[src*="/signature-assets/"]').nth(2).waitFor();
    assert.equal(await historicalEditor.locator('img[src*="/signature-assets/"]').count(), 3,
      "historical image-bearing version must still display all uploaded images");

    checkpoint("signature image upload/persistence: complete");
    checkpoint("signature create/update: complete");

    // The image test leaves version 2 in history and creates version 3 without image
    // references. Close its editor before starting the independent history/restore flow.
    await signatureDialog.getByRole("button", { name: "Cancel", exact: true }).click();
    await signatureDialog.waitFor({ state: "hidden" });

    // Re-open the signature and exercise immutable history, historical preview and restore.
    await signatureRow.getByRole("button", { name: "Edit", exact: true }).click();
    await page.waitForFunction(() =>
      document.querySelectorAll('[x-data="signatureManager"] select[x-model="selectedVersionId"] option').length === 3
    );
    assert.equal(await versionSelector.locator("option").count(), 3,
      "image persistence workflow must retain versions 1, 2 and current version 3");
    await signatureDialog.getByText("Current version:", { exact: false }).waitFor();
    assert.match(await signatureDialog.getByText("Current version:", { exact: false }).innerText(), /v3/,
      "the current signature version must be clearly identified");

    const versionOneOption = versionSelector.locator("option").filter({ hasText: /^v1(?:\s|·|$)/ }).first();
    const versionOneId = await versionOneOption.getAttribute("value");
    assert.ok(versionOneId, "version 1 must remain selectable after creating version 2");
    await versionSelector.selectOption(versionOneId);
    await page.waitForFunction(() =>
      document.querySelector("#signature-template-editor .ck-editor__editable")?.innerHTML.includes("Browser smoke version 1")
    );
    assert.match(await signatureEditEditor.innerHTML(), /Browser smoke version 1/,
      "selecting an old version must load its immutable HTML snapshot");
    assert.equal(await signatureEditEditor.getAttribute("contenteditable"), "false",
      "previewing an old version must lock the editor against accidental edits");
    assert.equal(await signatureDialog.locator("#signature-html-source").isDisabled(), true,
      "historical source HTML must also be locked");
    await signatureDialog.getByText("You are viewing", { exact: false }).waitFor();
    assert.match(await signatureDialog.getByText("You are viewing", { exact: false }).innerText(), /current version remains\s+v3/i,
      "previewing an old version must state that the current version is unchanged");

    let versionHistory = await (await page.request.get(
      baseURL + "/api/admin/signatures/" + encodeURIComponent((await (await page.request.get(baseURL + "/api/admin/signatures")).json())
        .find(item => item.name === "Browser Smoke Signature").id) + "/versions"
    )).json();
    assert.deepEqual(versionHistory.map(item => item.version_number), [3, 2, 1]);
    assert.deepEqual(versionHistory.map(item => item.is_current), [true, false, false],
      "previewing version 1 must not change the current version");

    // Saving metadata while previewing old HTML may not implicitly restore it.
    await signatureDialog.getByRole("button", { name: "Save Signature", exact: true }).click();
    await signatureDialog.waitFor({ state: "hidden" });
    await signatureRow.getByRole("button", { name: "Edit", exact: true }).click();
    await page.waitForFunction(() =>
      document.querySelectorAll('[x-data="signatureManager"] select[x-model="selectedVersionId"] option').length === 3
    );
    versionHistory = await (await page.request.get(
      baseURL + "/api/admin/signatures/" + encodeURIComponent((await (await page.request.get(baseURL + "/api/admin/signatures")).json())
        .find(item => item.name === "Browser Smoke Signature").id) + "/versions"
    )).json();
    assert.deepEqual(versionHistory.map(item => item.version_number), [3, 2, 1],
      "saving after historical preview must not create or activate another version");
    assert.deepEqual(versionHistory.map(item => item.is_current), [true, false, false]);
    await versionSelector.selectOption(versionOneId);

    const restoreResponse = page.waitForResponse(response =>
      response.request().method() === "POST"
      && response.url().endsWith("/versions/" + versionOneId + "/restore")
      && response.status() === 201
    );
    await signatureDialog.getByRole("button", { name: "Restore selected version", exact: true }).click();
    await restoreResponse;
    await page.waitForFunction(() =>
      document.querySelectorAll('[x-data="signatureManager"] select[x-model="selectedVersionId"] option').length === 4
    );
    assert.equal(await versionSelector.locator("option").count(), 4,
      "restoring version 1 must create version 4 and retain versions 1 through 3");
    assert.match(await signatureDialog.getByText("Current version:", { exact: false }).innerText(), /v4/,
      "restored snapshot must become a new current version");
    assert.match(await signatureEditEditor.innerHTML(), /Browser smoke version 1/,
      "restored version 3 must contain the selected historical snapshot");

    const restoredSignatureRecord = (await (await page.request.get(baseURL + "/api/admin/signatures")).json())
      .find(item => item.name === "Browser Smoke Signature");
    versionHistory = await (await page.request.get(
      baseURL + "/api/admin/signatures/" + encodeURIComponent(restoredSignatureRecord.id) + "/versions"
    )).json();
    assert.deepEqual(versionHistory.map(item => item.version_number), [4, 3, 2, 1]);
    assert.deepEqual(versionHistory.map(item => item.is_current), [true, false, false, false]);

    await signatureDialog.getByRole("button", { name: "Cancel", exact: true }).click();
    await page.reload();
    await page.getByRole("button", { name: "Admin", exact: true }).click();
    await page.getByRole("button", { name: "Signatures", exact: true }).click();
    const reloadedSignatureRow = signaturePanel.locator("tbody tr").filter({ hasText: "Browser Smoke Signature" });
    await reloadedSignatureRow.waitFor();
    await reloadedSignatureRow.getByRole("button", { name: "Edit", exact: true }).click();
    await page.waitForFunction(() =>
      document.querySelectorAll('[x-data="signatureManager"] select[x-model="selectedVersionId"] option').length === 4
    );
    assert.equal(await versionSelector.locator("option").count(), 4,
      "signature version history must survive a full page reload");
    assert.match(await signatureDialog.getByText("Current version:", { exact: false }).innerText(), /v4/,
      "the current version marker must survive a full page reload");
    assert.match(await signatureEditEditor.innerHTML(), /Browser smoke version 1/,
      "re-opening after reload must load the restored current snapshot");
    await signatureDialog.getByRole("button", { name: "Cancel", exact: true }).click();

    // HTML comments and other source-only markup must not create phantom
    // revisions when CKEditor normalizes them during opening.
    const sourceOnly = await page.evaluate(async () => {
      const response = await fetch("/api/admin/signatures", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: "Browser Source Only",
          html_content: "<p>Source-only markup</p><!--historical-raw-comment-->"
        })
      });
      return { status: response.status, data: await response.json() };
    });
    assert.equal(sourceOnly.status, 201, JSON.stringify(sourceOnly.data));
    await page.reload();
    await page.getByRole("button", { name: "Admin", exact: true }).click();
    await page.getByRole("button", { name: "Signatures", exact: true }).click();
    const sourceOnlyRow = signaturePanel.locator("tbody tr").filter({ hasText: "Browser Source Only" });
    await sourceOnlyRow.getByRole("button", { name: "Edit", exact: true }).click();
    await signatureEditEditor.waitFor({ state: "visible" });
    await signatureDialog.getByRole("button", { name: "Save Signature", exact: true }).click();
    await signatureDialog.waitFor({ state: "hidden" });
    const sourceOnlyHistoryResponse = await page.request.get(
      baseURL + "/api/admin/signatures/" + sourceOnly.data.id + "/versions"
    );
    assert.equal(sourceOnlyHistoryResponse.status(), 200);
    const sourceOnlyHistory = await sourceOnlyHistoryResponse.json();
    assert.deepEqual(sourceOnlyHistory.map(item => item.version_number), [1],
      "loading source-only markup through CKEditor must not auto-create a version on save");

    checkpoint("signature version history/restore: complete");

    // Exercise scheduling create/update/delete through the real administrator UI.
    await page.getByRole("button", { name: "Scheduling", exact: true }).click();
    const schedulePanel = page.locator('[x-data="scheduleManager"]');
    await schedulePanel.getByRole("button", { name: "+ Add Schedule", exact: true }).click();
    const scheduleDialog = schedulePanel.locator('[x-show="editing"][role="dialog"]');
    await assertFloatingDialog(scheduleDialog, "schedule editor");
    await scheduleDialog.getByLabel("Name").fill("Browser Smoke Schedule");
    const scheduleTargetType = scheduleDialog.locator('select[x-model="form.target_type"]');
    await scheduleTargetType.selectOption("GROUP");
    await scheduleTargetType.selectOption("SIGNATURE");
    const scheduleSignatureSelect = scheduleDialog.locator('select[x-model="form.signature_id"]');
    await scheduleSignatureSelect.waitFor({ state: "visible" });
    await scheduleSignatureSelect.selectOption({ label: "Browser Smoke Signature" });
    const localInputValue = date => {
      const shifted = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
      return shifted.toISOString().slice(0, 16);
    };
    const scheduleNow = new Date();
    await scheduleDialog.getByLabel("Start").fill(localInputValue(new Date(scheduleNow.getTime() - 2 * 60 * 1000)));
    await scheduleDialog.getByLabel("End").fill(localInputValue(new Date(scheduleNow.getTime() + 60 * 60 * 1000)));
    await scheduleDialog.getByLabel("Priority").fill("10");
    await scheduleDialog.getByRole("button", { name: "Save Schedule", exact: true }).click();
    let scheduleRow = schedulePanel.locator("tbody tr").filter({ hasText: "Browser Smoke Schedule" });
    await scheduleRow.waitFor();
    let schedules = await (await page.request.get(baseURL + "/api/admin/schedules")).json();
    let browserSchedule = schedules.find(item => item.name === "Browser Smoke Schedule");
    assert.ok(browserSchedule, "schedule created in the UI must be persisted");
    assert.equal(browserSchedule.target_type, "SIGNATURE");
    assert.equal(browserSchedule.priority, 10);

    await scheduleRow.getByRole("button", { name: "Edit", exact: true }).click();
    await scheduleDialog.getByLabel("Priority").fill("42");
    await scheduleDialog.getByRole("button", { name: "Save Schedule", exact: true }).click();
    await page.waitForFunction(() => {
      const row = [...document.querySelectorAll('[x-data="scheduleManager"] tbody tr')]
        .find(item => item.textContent.includes("Browser Smoke Schedule"));
      return row && row.children[4].textContent.trim() === "42";
    });
    schedules = await (await page.request.get(baseURL + "/api/admin/schedules")).json();
    browserSchedule = schedules.find(item => item.name === "Browser Smoke Schedule");
    assert.equal(browserSchedule.priority, 42, "schedule update must persist");

    scheduleRow = schedulePanel.locator("tbody tr").filter({ hasText: "Browser Smoke Schedule" });
    page.once("dialog", dialog => dialog.accept());
    await scheduleRow.getByRole("button", { name: "Delete", exact: true }).click();
    await scheduleRow.waitFor({ state: "detached" });
    schedules = await (await page.request.get(baseURL + "/api/admin/schedules")).json();
    assert.equal(schedules.some(item => item.name === "Browser Smoke Schedule"), false,
      "deleted schedule must be removed from persistence");

    checkpoint("scheduling lifecycle: complete");

    const uploadedAssetUrl = await page.evaluate(async () => {
      const bytes = new Uint8Array([137,80,78,71,13,10,26,10,1,2,3,4]);
      const response = await fetch("/api/admin/signature-assets", {
        method: "POST",
        headers: { "Content-Type": "image/png" },
        body: bytes
      });
      if (!response.ok) throw new Error("signature asset upload failed: " + await response.text());
      return (await response.json()).url;
    });
    assert.match(uploadedAssetUrl, /^\/signature-assets\/[0-9a-f]{64}\.png$/,
      "signature image upload must return a content-addressed public asset URL");
    const uploadedAsset = await page.request.get(baseURL + uploadedAssetUrl);
    assert.equal(uploadedAsset.status(), 200, "uploaded signature image must be publicly retrievable");

    // Exercise persistent branding and both asset upload paths. Restore defaults
    // afterwards so later smoke checks continue with the normal product identity.
    await page.getByRole("button", { name: "Branding", exact: true }).click();
    let brandingPanel = page.locator('[x-data="brandingManager"]');
    await brandingPanel.getByLabel("Application name").fill("OpenSigWeave Browser Smoke");
    await brandingPanel.locator('input[placeholder="#ff7043"]').fill("#f97316");
    const brandingFiles = brandingPanel.locator('input[type="file"]');
    const brandingLogoBytes = Buffer.from(
      "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl+N8sAAAAASUVORK5CYII=",
      "base64"
    );
    const brandingLogoFile = { name: "logo.png", mimeType: "image/png", buffer: brandingLogoBytes };
    await brandingFiles.nth(0).setInputFiles(brandingLogoFile);
    await brandingFiles.nth(1).setInputFiles(brandingLogoFile);
    await page.waitForFunction(() => {
      const logo = document.querySelector('img[alt="Brand logo preview"]');
      const favicon = document.querySelector('img[alt="Favicon preview"]');
      return logo && favicon
        && logo.getAttribute("src").startsWith("data:image/png;base64,")
        && favicon.getAttribute("src").startsWith("data:image/png;base64,");
    });
    const brandingSave = brandingPanel.getByRole("button", { name: "Save Branding", exact: true });
    await Promise.all([
      page.waitForNavigation({ waitUntil: "domcontentloaded" }),
      brandingSave.click(),
    ]);
    await page.locator(".app-brand").filter({ hasText: "OpenSigWeave Browser Smoke" }).waitFor();
    const customBranding = await (await page.request.get(baseURL + "/api/admin/branding")).json();
    assert.equal(customBranding.app_name, "OpenSigWeave Browser Smoke");
    assert.equal(customBranding.app_color, "#f97316");
    assert.equal(customBranding.has_custom_logo, true);
    assert.equal(customBranding.has_custom_favicon, true);

    await page.getByRole("button", { name: "Admin", exact: true }).click();
    await page.getByRole("button", { name: "Branding", exact: true }).click();
    brandingPanel = page.locator('[x-data="brandingManager"]');
    await brandingPanel.getByLabel("Application name").fill("OpenSigWeave");
    await brandingPanel.locator('input[placeholder="#ff7043"]').fill("#ff7043");
    await brandingPanel.getByRole("button", { name: "Use built-in logo", exact: true }).click();
    await brandingPanel.getByRole("button", { name: "Use built-in fallback", exact: true }).click();
    await Promise.all([
      page.waitForNavigation({ waitUntil: "domcontentloaded" }),
      brandingPanel.getByRole("button", { name: "Save Branding", exact: true }).click(),
    ]);
    await page.locator(".app-brand").filter({ hasText: "OpenSigWeave" }).waitFor();
    const restoredBranding = await (await page.request.get(baseURL + "/api/admin/branding")).json();
    assert.equal(restoredBranding.app_name, "OpenSigWeave");
    assert.equal(restoredBranding.app_color, "#ff7043");
    assert.equal(restoredBranding.has_custom_logo, false);
    assert.equal(restoredBranding.has_custom_favicon, false);
    const bundledLogo = await page.request.get(baseURL + "/branding/logo.png");
    assert.equal(bundledLogo.status(), 200, "built-in logo must remain available after restoring defaults");

    checkpoint("branding lifecycle: complete");

    await page.getByRole("button", { name: "Admin", exact: true }).click();
    const groupPanel = page.locator('[x-data="signatureGroupManager"]');
    await page.getByRole("button", { name: "Signature Groups", exact: true }).click();
    await groupPanel.getByRole("button", { name: "+ Add Group" }).click();
    const groupDialog = groupPanel.locator('[x-show="editing"][role="dialog"]');
    await assertFloatingDialog(groupDialog, "signature group editor");
    await groupPanel.getByLabel("Name").fill("Browser Smoke Group");
    await groupPanel.getByRole("button", { name: "Save Group", exact: true }).click();
    const groupCard = groupPanel.locator("div.bg-gray-800.border.border-gray-700.rounded-lg.p-5.space-y-3").filter({ has: page.getByRole("heading", { name: "Browser Smoke Group", exact: true }) });
    await groupCard.getByRole("heading", { name: "Browser Smoke Group", exact: true }).waitFor();
    await groupPanel.getByRole("button", { name: "+ Add Signature to Group", exact: true }).click();
    const memberDialog = groupPanel.locator('div[x-show="memberEditor"]');
    await assertFloatingDialog(memberDialog, "group membership editor");
    await memberDialog.locator('select[x-model="memberForm.group_id"]').selectOption({ label: "Browser Smoke Group" });
    await memberDialog.locator('select[x-model="memberForm.signature_id"]').selectOption({ label: "Browser Smoke Signature" });
    await memberDialog.getByRole("button", { name: "Add Signature", exact: true }).click();
    await groupCard.locator("span.flex-1.truncate").filter({ hasText: "Browser Smoke Signature" }).waitFor();

    checkpoint("group create/membership: complete");
    const rulesPanel = page.locator('[x-data="ruleManager"]');
    await page.getByRole("button", { name: "Rules", exact: true }).click();
    await rulesPanel.getByRole("button", { name: "+ Add Rule" }).click();
    const ruleDialog = rulesPanel.locator('[x-show="editing"][role="dialog"]');
    await assertFloatingDialog(ruleDialog, "signature rule editor");
    await rulesPanel.getByLabel("Name").fill("Browser Smoke Rule");
    await rulesPanel.getByLabel("Action").selectOption("GROUP");
    // The wrapping label also contains the select's option text, so Playwright's
    // exact accessible-label match is not stable once groups are loaded.
    const ruleGroupSelect = rulesPanel.locator('select[x-model="form.group_id"]');
    await page.waitForFunction(() => {
      const action = document.querySelector('[x-data="ruleManager"] select[x-model="form.action_mode"]');
      return action && action.value === "GROUP";
    });
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

    checkpoint("rule create/update: complete");
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

    checkpoint("group/signature updates: complete");
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

    checkpoint("rule/group deletion: complete");

    // Create a login-capable OIDC provider through the real UI and verify that its
    // non-secret SSO settings survive a reload. Secrets intentionally remain hidden.
    await page.getByRole("button", { name: "Identity Providers", exact: true }).click();
    const ssoProviderPanel = page.locator('[x-data="identityProviderManager"]');
    await ssoProviderPanel.getByRole("button", { name: "+ Add Identity Provider" }).click();
    const ssoProviderDialog = ssoProviderPanel.locator('[x-show="editing"][role="dialog"]');
    await ssoProviderDialog.getByLabel("Folder name / ID").fill("browser-smoke-login-provider");
    await ssoProviderDialog.getByLabel("Display name").fill("Browser Smoke Login Provider");
    await ssoProviderDialog.getByLabel("Provider type").selectOption("oidc");
    const oidcFields = ssoProviderDialog.locator('div[x-show="form.provider_type === \'oidc\'"]');
    await oidcFields.getByLabel("OIDC discovery URL").fill("https://idp.example.org/.well-known/openid-configuration");
    await oidcFields.getByLabel("OIDC client ID").fill("browser-smoke-client");
    await oidcFields.getByLabel("OIDC client secret").fill("browser-smoke-secret");
    await oidcFields.getByLabel("OIDC scopes").fill("openid email profile groups");
    await oidcFields.getByLabel("OIDC callback / redirect URI").fill("https://signatures.example.org/auth/browser-smoke-login-provider");
    const createSsoProvider = page.waitForResponse(response =>
      response.request().method() === "POST"
      && response.url().endsWith("/api/admin/identity-providers")
      && response.status() === 201
    );
    await ssoProviderDialog.getByRole("button", { name: "Save", exact: true }).click();
    await createSsoProvider;
    const ssoProviderRow = ssoProviderPanel.locator("tbody tr").filter({ hasText: "Browser Smoke Login Provider" });
    await ssoProviderRow.waitFor();

    await page.reload();
    await page.getByRole("heading", { name: "Identity Providers" }).waitFor({ state: "visible" });

    // Verify persistence at the API boundary first. This distinguishes an actual
    // storage regression from a browser timing issue in the async edit dialog.
    const persistedProviders = await (await page.request.get(baseURL + "/api/admin/identity-providers")).json();
    const persistedSsoProvider = persistedProviders.find(item => item.id === "browser-smoke-login-provider");
    assert.ok(persistedSsoProvider, "saved OIDC provider must still exist after reload");
    assert.equal(persistedSsoProvider.settings.OIDC_DISCOVERY_URL,
      "https://idp.example.org/.well-known/openid-configuration");
    assert.equal(persistedSsoProvider.settings.OIDC_CLIENT_ID, "browser-smoke-client");
    assert.equal(persistedSsoProvider.settings.OIDC_REDIRECT_URI,
      "https://signatures.example.org/auth/browser-smoke-login-provider");
    assert.equal(Object.prototype.hasOwnProperty.call(persistedSsoProvider.settings, "OIDC_CLIENT_SECRET"), false,
      "OIDC client secret must remain write-only");

    const reloadedSsoProviderRow = page.locator('[x-data="identityProviderManager"] tbody tr')
      .filter({ hasText: "Browser Smoke Login Provider" });
    await reloadedSsoProviderRow.getByRole("button", { name: "Edit", exact: true }).click();
    const reloadedSsoDialog = page.locator('[x-data="identityProviderManager"] [x-show="editing"][role="dialog"]');
    await reloadedSsoDialog.waitFor({ state: "visible" });
    const reloadedOidcFields = reloadedSsoDialog.locator('div[x-show="form.provider_type === \'oidc\'"]');
    await reloadedOidcFields.waitFor({ state: "visible" });
    assert.equal(await reloadedOidcFields.getByLabel("OIDC discovery URL").inputValue(),
      "https://idp.example.org/.well-known/openid-configuration");
    assert.equal(await reloadedOidcFields.getByLabel("OIDC client ID").inputValue(), "browser-smoke-client");
    assert.equal(await reloadedOidcFields.getByLabel("OIDC callback / redirect URI").inputValue(),
      "https://signatures.example.org/auth/browser-smoke-login-provider");
    assert.equal(await reloadedOidcFields.getByLabel("OIDC client secret").inputValue(), "",
      "saved OIDC secrets must remain write-only in the browser");

    // Updating an existing OIDC provider intentionally leaves the write-only
    // client-secret input blank. The existing credential must be retained before
    // validation while changed non-secret settings persist across another reload.
    await reloadedOidcFields.getByLabel("OIDC scopes").fill("openid email profile groups offline_access");
    const updateSsoProvider = page.waitForResponse(response =>
      response.request().method() === "PUT"
      && response.url().endsWith("/api/admin/identity-providers/browser-smoke-login-provider")
      && response.status() === 200
    );
    await reloadedSsoDialog.getByRole("button", { name: "Save", exact: true }).click();
    await updateSsoProvider;
    await page.reload();
    await page.getByRole("heading", { name: "Identity Providers" }).waitFor({ state: "visible" });

    const updatedProviders = await (await page.request.get(baseURL + "/api/admin/identity-providers")).json();
    const updatedSsoProvider = updatedProviders.find(item => item.id === "browser-smoke-login-provider");
    assert.ok(updatedSsoProvider, "updated OIDC provider must still exist after reload");
    assert.equal(updatedSsoProvider.settings.OIDC_SCOPES, "openid email profile groups offline_access",
      "OIDC settings edited with a blank secret field must persist");
    const updatedSsoProviderRow = page.locator('[x-data="identityProviderManager"] tbody tr')
      .filter({ hasText: "Browser Smoke Login Provider" });
    await updatedSsoProviderRow.getByRole("button", { name: "Edit", exact: true }).click();
    const updatedSsoDialog = page.locator('[x-data="identityProviderManager"] [x-show="editing"][role="dialog"]');
    await updatedSsoDialog.waitFor({ state: "visible" });
    const updatedOidcFields = updatedSsoDialog.locator('div[x-show="form.provider_type === \'oidc\'"]');
    await updatedOidcFields.waitFor({ state: "visible" });
    assert.equal(await updatedOidcFields.getByLabel("OIDC scopes").inputValue(),
      "openid email profile groups offline_access");
    assert.equal(await updatedOidcFields.getByLabel("OIDC client secret").inputValue(), "",
      "OIDC client secret must remain write-only after update and reload");
    await updatedSsoDialog.press("Escape");
    await updatedSsoDialog.waitFor({ state: "hidden" });

    // Exercise the complete domain lifecycle through the administrator UI.
    await page.getByRole("button", { name: "Domains", exact: true }).click();
    const domainPanel = page.locator('[x-data="domainManager"]');
    await domainPanel.getByRole("heading", { name: "Managed Domains" }).waitFor({ state: "visible" });
    await domainPanel.getByRole("button", { name: "+ Add Domain" }).click();
    const addDomainDialog = page.locator('[x-data="addDomainModal"]');
    await addDomainDialog.locator('input[placeholder="e.g., hatsthings.com"]').fill("browser-smoke-domain.test");
    const createDomainResponse = page.waitForResponse(response =>
      response.request().method() === "POST"
      && response.url().endsWith("/domains/")
      && response.status() === 200
    );
    await addDomainDialog.getByRole("button", { name: "Register Domain", exact: true }).click();
    await createDomainResponse;
    let domainRow = domainPanel.locator("tbody tr").filter({ hasText: "browser-smoke-domain.test" });
    await domainRow.waitFor();
    let domainRecords = await (await page.request.get(baseURL + "/domains/", { timeout: 15000 })).json();
    let createdDomain = domainRecords.find(item => item.domain_name === "browser-smoke-domain.test");
    assert.ok(createdDomain, "new domain must be persisted by the create workflow");

    checkpoint("domain create: complete");
    // A profile provider is deliberately available without a configured login provider.
    const profileProviderSelect = domainRow.locator("select").nth(0);
    const providerAssignmentResponse = page.waitForResponse(response =>
      response.request().method() === "PUT"
      && response.url().endsWith("/domains/" + createdDomain.id + "/identity-provider")
    );
    await profileProviderSelect.selectOption("browser-smoke-profile-provider");
    const providerAssignment = await providerAssignmentResponse;
    assert.equal(providerAssignment.status(), 200, "profile provider assignment request must succeed");

    const loginProviderSelect = domainRow.locator("select").nth(1);
    await page.waitForFunction(() => {
      const row = [...document.querySelectorAll('[x-data="domainManager"] tbody tr')]
        .find(item => item.textContent.includes("browser-smoke-domain.test"));
      const select = row ? row.querySelectorAll("select")[1] : null;
      return select && [...select.options].some(option => option.value === "browser-smoke-login-provider");
    });
    const loginProviderAssignmentResponse = page.waitForResponse(response =>
      response.request().method() === "PUT"
      && response.url().endsWith("/domains/" + createdDomain.id + "/authentication-provider")
    );
    await loginProviderSelect.selectOption("browser-smoke-login-provider");
    const loginProviderAssignment = await loginProviderAssignmentResponse;
    assert.equal(loginProviderAssignment.status(), 200, "SSO login provider assignment request must succeed");

    domainRecords = await (await page.request.get(baseURL + "/domains/")).json();
    createdDomain = domainRecords.find(item => item.domain_name === "browser-smoke-domain.test");
    assert.equal(createdDomain.authentication_provider_id, "browser-smoke-login-provider",
      "SSO login provider assignment must be persisted by the domain API");

    // The admin/domain page must remain selected across a reload and both provider
    // assignments must be reconstructed from persisted state.
    await page.reload();
    await page.getByRole("heading", { name: "Managed Domains" }).waitFor({ state: "visible" });
    domainRow = page.locator('[x-data="domainManager"] tbody tr').filter({ hasText: "browser-smoke-domain.test" });
    await domainRow.waitFor();
    assert.equal(await domainRow.locator("select").nth(0).inputValue(),
      "browser-smoke-profile-provider",
      "profile provider assignment must persist independently of the login provider");
    assert.equal(await domainRow.locator("select").nth(1).inputValue(), "browser-smoke-login-provider",
      "SSO login provider assignment must remain selected after page reload");

    checkpoint("provider assignment and persistence: complete");
    // Disable and re-enable the domain through the confirmation dialog.
    const domainStatus = domainRow.locator("td").nth(1).locator("span");
    await domainStatus.waitFor({ state: "visible" });
    await domainStatus.click();
    const confirmDialog = page.locator('[x-data="confirmModal"]');
    const disableDomainResponse = page.waitForResponse(response =>
      response.request().method() === "PUT"
      && response.url().endsWith("/domains/" + createdDomain.id)
      && response.status() === 200
    );
    const disableDomainNavigation = page.waitForNavigation({ waitUntil: "domcontentloaded" });
    await confirmDialog.getByRole("button", { name: "Confirm", exact: true }).click();
    await Promise.all([disableDomainResponse, disableDomainNavigation]);
    await page.getByRole("button", { name: "Admin", exact: true }).click();
    await page.getByRole("button", { name: "Domains", exact: true }).click();
    await domainPanel.getByRole("heading", { name: "Managed Domains" }).waitFor({ state: "visible" });
    domainRow = domainPanel.locator("tbody tr").filter({ hasText: "browser-smoke-domain.test" });
    await domainRow.locator("td").nth(1).locator("span").click();
    const enableDomainResponse = page.waitForResponse(response =>
      response.request().method() === "PUT"
      && response.url().endsWith("/domains/" + createdDomain.id)
      && response.status() === 200
    );
    const enableDomainNavigation = page.waitForNavigation({ waitUntil: "domcontentloaded" });
    await confirmDialog.getByRole("button", { name: "Confirm", exact: true }).click();
    await Promise.all([enableDomainResponse, enableDomainNavigation]);
    await page.getByRole("button", { name: "Admin", exact: true }).click();
    await page.getByRole("button", { name: "Domains", exact: true }).click();
    await domainPanel.getByRole("heading", { name: "Managed Domains" }).waitFor({ state: "visible" });

    checkpoint("domain status lifecycle: complete");
    // Save domain policy settings, reload the UI, and verify persistence.
    domainRow = domainPanel.locator("tbody tr").filter({ hasText: "browser-smoke-domain.test" });
    await domainRow.getByRole("button", { name: "Settings & Users", exact: true }).click();
    const domainSettings = page.locator('[x-data="domainSettingsModal"]');
    // Exercise the domain master-template CKEditor instance and its source synchronization.
    const domainEditor = domainSettings.locator("#domain-rich-editor .ck-editor__editable");
    await domainEditor.waitFor({ state: "visible" });
    await domainEditor.fill("Domain master template");
    await domainEditor.press("Control+A");
    await domainEditor.press("Control+B");
    await domainEditor.press("End");
    await page.waitForFunction(() =>
      document.querySelector("#domain-raw-html-editor").value.includes("<strong>Domain master template</strong>")
    );
    assert.match(await domainEditor.innerHTML(), /<strong>Domain master template<\/strong>/,
      "domain master-template CKEditor must format content and synchronize its source textarea");
    const saveMasterTemplate = page.waitForResponse(response =>
      response.request().method() === "PUT"
      && response.url().endsWith("/domains/" + createdDomain.id)
      && response.status() === 200
    );
    await domainSettings.getByRole("button", { name: "Save Settings", exact: true }).click();
    await saveMasterTemplate;
    domainRecords = await (await page.request.get(baseURL + "/domains/")).json();
    createdDomain = domainRecords.find(item => item.domain_name === "browser-smoke-domain.test");
    assert.match(createdDomain.template_html, /<strong>Domain master template<\/strong>/,
      "domain master-template HTML must persist through the domain API");

    // Exercise the administrator's per-user override editor and save workflow.
    await domainRow.getByRole("button", { name: "Settings & Users", exact: true }).click();
    await domainSettings.getByRole("button", { name: "User Overrides", exact: true }).click();
    await domainSettings.getByPlaceholder("user@domain.com").fill("editor-smoke@browser-smoke-domain.test");
    await domainSettings.getByRole("button", { name: "+ Add / Edit" }).click();
    const adminEditor = domainSettings.locator("#admin-rich-editor .ck-editor__editable");
    await adminEditor.waitFor({ state: "visible" });
    await adminEditor.fill("Administrator user override");
    await adminEditor.press("Control+A");
    await adminEditor.press("Control+B");
    await page.waitForFunction(() =>
      document.querySelector("#admin-raw-html-editor").value.includes("<strong>Administrator user override</strong>")
    );
    assert.match(await adminEditor.innerHTML(), /<strong>Administrator user override<\/strong>/,
      "administrator override CKEditor must format content and synchronize its source textarea");
    const saveAdminOverride = page.waitForResponse(response =>
      response.request().method() === "POST"
      && response.url().endsWith("/api/admin/overrides")
      && response.status() === 200
    );
    await domainSettings.getByRole("button", { name: "Save User", exact: true }).click();
    await saveAdminOverride;
    const adminOverrides = await (await page.request.get(baseURL + "/api/admin/overrides/browser-smoke-domain.test")).json();
    const editorOverride = adminOverrides.find(item => item.user_email === "editor-smoke@browser-smoke-domain.test");
    assert.ok(editorOverride, "administrator override must be persisted and returned by the domain API");
    assert.match(editorOverride.html_content, /<strong>Administrator user override<\/strong>/,
      "administrator override API must return the formatted HTML");
    await domainSettings.getByRole("button", { name: "Close Manager", exact: true }).click();
    await domainRow.getByRole("button", { name: "Settings & Users", exact: true }).click();
    const allowOverridesLabel = domainSettings.locator("label").filter({ hasText: "Allow Users to Override Signature" });
    await allowOverridesLabel.click();
    const replyInjectionLabel = domainSettings.locator("label").filter({ hasText: "Enable Experimental Reply Injection" });
    await replyInjectionLabel.click();
    const saveDomainSettingsResponse = page.waitForResponse(response =>
      response.request().method() === "PUT"
      && response.url().endsWith("/domains/" + createdDomain.id)
      && response.status() === 200
    );
    await domainSettings.getByRole("button", { name: "Save Settings", exact: true }).click();
    await saveDomainSettingsResponse;
    domainRecords = await (await page.request.get(baseURL + "/domains/")).json();
    createdDomain = domainRecords.find(item => item.domain_name === "browser-smoke-domain.test");
    assert.equal(createdDomain.allow_overrides, false);
    assert.equal(createdDomain.inject_on_replies, true);
    assert.equal(createdDomain.identity_provider_id, "browser-smoke-profile-provider",
      "saving domain settings must preserve the profile provider assignment");
    assert.equal(createdDomain.authentication_provider_id, "browser-smoke-login-provider",
      "saving domain settings must preserve the SSO login provider assignment");

    await page.reload();
    await page.getByRole("heading", { name: "Managed Domains" }).waitFor({ state: "visible" });
    domainRow = page.locator('[x-data="domainManager"] tbody tr').filter({ hasText: "browser-smoke-domain.test" });
    await domainRow.waitFor();
    assert.equal(await domainRow.locator("select").nth(0).inputValue(), "browser-smoke-profile-provider");
    assert.equal(await domainRow.locator("select").nth(1).inputValue(), "browser-smoke-login-provider",
      "login provider must remain selected after page reload");
    await domainRow.getByRole("button", { name: "Settings & Users", exact: true }).click();
    const reloadedSettings = page.locator('[x-data="domainSettingsModal"]');
    await reloadedSettings.locator("label").filter({ hasText: "Allow Users to Override Signature" })
      .locator('input[type="checkbox"]').waitFor();
    assert.equal(await reloadedSettings.locator("label").filter({ hasText: "Allow Users to Override Signature" })
      .locator('input[type="checkbox"]').isChecked(), false);
    assert.equal(await reloadedSettings.locator("label").filter({ hasText: "Enable Experimental Reply Injection" })
      .locator('input[type="checkbox"]').isChecked(), true);
    await reloadedSettings.getByRole("button", { name: "Close", exact: true }).click();

    checkpoint("domain settings: complete");
    // Delete the test domain and verify it is removed from persistence and the UI.
    await domainRow.getByRole("button", { name: "Delete", exact: true }).click();
    const deleteDomainResponse = page.waitForResponse(response =>
      response.request().method() === "DELETE"
      && response.url().endsWith("/domains/" + createdDomain.id)
      && response.status() === 200
    );
    const deleteDomainNavigation = page.waitForNavigation({ waitUntil: "domcontentloaded" });
    await confirmDialog.getByRole("button", { name: "Confirm", exact: true }).click();
    await Promise.all([deleteDomainResponse, deleteDomainNavigation]);
    await page.getByRole("button", { name: "Admin", exact: true }).click();
    await page.getByRole("button", { name: "Domains", exact: true }).click();
    await domainPanel.getByRole("heading", { name: "Managed Domains" }).waitFor({ state: "visible" });
    assert.equal(await domainPanel.locator("tbody tr").filter({ hasText: "browser-smoke-domain.test" }).count(), 0,
      "deleted domain must disappear from the administrator UI");
    assert.equal((await (await page.request.get(baseURL + "/domains/")).json())
      .some(item => item.domain_name === "browser-smoke-domain.test"), false,
      "deleted domain must no longer be returned by the API");

    const deleteSsoProvider = await page.evaluate(async () => {
      const response = await fetch("/api/admin/identity-providers/browser-smoke-login-provider", { method: "DELETE" });
      return { status: response.status, body: await response.text() };
    });
    assert.equal(deleteSsoProvider.status, 200,
      "browser smoke SSO provider cleanup failed: " + deleteSsoProvider.body);

    checkpoint("domain lifecycle: complete");
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

    checkpoint("normal-user authorization: complete");
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
    assert.deepEqual(cspViolations, [],
      "The exercised portal and CKEditor workflows must not produce CSP violation events: " + JSON.stringify(cspViolations));
    const inlineStyleElements = await page.locator("[style]").evaluateAll(elements => elements.map(element => ({ tag: element.tagName, className: element.className, style: element.getAttribute("style") })).slice(0, 30));
    process.stdout.write("CSP report-only browser violations during exercised portal/editor workflows: " + JSON.stringify(cspViolations) + "\\n");
    process.stdout.write("Inline style elements: " + JSON.stringify(inlineStyleElements) + "\\n");
    await page.locator("#logout-button").click();
    await page.waitForURL("**/login");
    await page.getByLabel("Email address").waitFor();
    const protectedPage = await page.request.get(baseURL + "/", { timeout: 15000 });
    assert.equal(protectedPage.status(), 200);
    assert.match(protectedPage.url(), /\/login$/, "logged-out session must be redirected to login");
    process.stdout.write("Browser smoke passed: administrator login, personal API, all admin sections, branding upload/reset, scheduling create/update/delete, export ZIP download and import preview, signature create/update/versioning, group create/update/member-weight update/membership removal/group delete, GROUP rule create/update/delete, regular-user authorization, domain override allow/deny policies, logout and session invalidation.\n");
  } finally {
    await browser.close();
  }
}

main().catch(error => {
  process.stderr.write((error && error.stack) || String(error));
  process.exitCode = 1;
});
