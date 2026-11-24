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
  page.on("pageerror", error => process.stderr.write("[browser-smoke] pageerror: " + error.message + "\\n"));
  page.on("console", message => {
    if (message.type() === "error" || message.type() === "warning")
      process.stderr.write("[browser-smoke] console " + message.type() + ": " + message.text() + "\\n");
  });
  page.on("requestfailed", request => {
    const failure = request.failure();
    // Navigation intentionally aborts in-flight Alpine refresh requests.
    // Keep genuine network failures in CI logs without reporting these as faults.
    if (failure && failure.errorText === "net::ERR_ABORTED") return;
    process.stderr.write("[browser-smoke] request failed: " + request.method() + " " + request.url()
      + (failure ? " (" + failure.errorText + ")" : "") + "\n");
  });
  const checkpoint = name => process.stdout.write("[browser-smoke] " + name + "\n");
  // Assert computed surface hierarchy rather than duplicating CSS tokens in a test.
  // Screenshots are uploaded for review; these assertions protect contrast and theme structure.
  async function assertThemePalette(label) {
    // The legacy .transition utilities animate colors for ~150ms after
    // switching themes. Verify settled colors, not intermediate frames.
    await page.waitForTimeout(300);
    const state = await page.evaluate(() => {
      const root = document.documentElement;
      const tokens = getComputedStyle(root);
      const style = selector => {
        const element = [...document.querySelectorAll(selector)].find(node => node.getClientRects().length > 0);
        if (!element) return null;
        const s = getComputedStyle(element);
        return { background: s.backgroundColor, color: s.color, border: s.borderTopColor };
      };
      const rgb = value => (value.match(/[\d.]+/g) || []).slice(0, 3).map(Number);
      const luminance = value => rgb(value).reduce((total, channel, index) => {
        const value = channel / 255;
        const linear = value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4;
        return total + linear * [.2126, .7152, .0722][index];
      }, 0);
      const contrast = (foreground, background) => {
        const a = luminance(foreground), b = luminance(background);
        return (Math.max(a, b) + .05) / (Math.min(a, b) + .05);
      };
      return {
        mode: root.dataset.theme,
        hasDarkClass: root.classList.contains("dark"),
        body: style("body"),
        navigation: style("nav"),
        card: style(".bg-gray-800.rounded-lg, .bg-gray-800.rounded-xl"),
        field: style("input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=file]):not([type=color]), select, textarea"),
        primary: style("button.bg-blue-600"),
        tableHead: style(".osw-table-scroll thead"),
        editor: style("#user-rich-editor .ck-editor__editable"),
        toolbar: style("#user-rich-editor .ck-toolbar"),
        panelToken: tokens.getPropertyValue("--osw-panel").trim(),
        modeScheme: tokens.colorScheme,
        bodyRatio: contrast(getComputedStyle(document.body).color, getComputedStyle(document.body).backgroundColor),
        fieldRatio: (() => {
          const field = [...document.querySelectorAll("input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=file]):not([type=color]), select, textarea")]
            .find(node => node.getClientRects().length && getComputedStyle(node).visibility === "visible"
              && Number(getComputedStyle(node).opacity) > .5);
          return field ? contrast(getComputedStyle(field).color, getComputedStyle(field).backgroundColor) : null;
        })(),
        primaryRatio: (() => {
          const button = [...document.querySelectorAll("button.bg-blue-600")].find(node =>
            node.getClientRects().length && !node.disabled);
          return button ? contrast(getComputedStyle(button).color, getComputedStyle(button).backgroundColor) : null;
        })(),
      };
    });
    assert.equal(state.hasDarkClass, state.mode === "dark", label + " dark class must match appearance");
    assert.ok(state.bodyRatio >= 4.5, label + " body contrast: " + JSON.stringify(state));
    if (state.fieldRatio !== null) assert.ok(state.fieldRatio >= 4.5, label + " input contrast: " + JSON.stringify(state));
    if (state.primaryRatio !== null) assert.ok(state.primaryRatio >= 4.5, label + " primary button contrast: " + JSON.stringify(state));
    if (state.navigation) assert.notEqual(state.body.background, state.navigation.background,
      label + " navigation must be distinct from canvas");
    if (state.card) assert.notEqual(state.card.background, state.body.background, label + " cards must be distinct from canvas");
    if (state.mode === "light") {
      assert.match(state.modeScheme, /light/, label + " should use light native controls");
      assert.equal(state.body.background, "rgb(242, 245, 249)", label + " must use the light canvas token");
      if (state.navigation) assert.equal(state.navigation.background, "rgb(255, 255, 255)",
        label + " navigation is a light surface");
      if (state.tableHead) assert.notEqual(state.tableHead.background, state.card?.background || state.body.background,
        label + " table header should be distinguished from the card");
    }
    return state;
  }
  async function captureTheme(name) {
    fs.mkdirSync("test-artifacts/themes", { recursive: true });
    await page.screenshot({ path: "test-artifacts/themes/" + name + ".png", fullPage: false });
  }

  async function assertFloatingDialog(dialog, label) {
    await dialog.waitFor({ state: "visible" });
    const layout = await dialog.evaluate(element => {
      const rect = element.getBoundingClientRect();
      return {
        position: window.getComputedStyle(element.closest(".osw-modal-root")).position,
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
    assert.equal(layout.position, "fixed", label + " must be hosted in the same fixed overlay as Domain Management");
    assert.ok(layout.width > 0 && layout.height > 0, label + " must have visible dimensions");
    assert.ok(layout.left >= -1 && layout.right <= layout.viewportWidth + 1,
      label + " must fit horizontally inside the viewport: " + JSON.stringify(layout));
    assert.ok(layout.top >= -1 && layout.bottom <= layout.viewportHeight + 1,
      label + " must fit vertically inside the viewport: " + JSON.stringify(layout));
  }


  async function assertAdminSectionLayout(section, label) {
    await section.waitFor({ state: "visible" });
    const badButtons = await section.evaluate(root => {
      const rect = root.getBoundingClientRect();
      return [...root.querySelectorAll("button")]
        .filter(button => button.getClientRects().length && !button.closest(".osw-modal-root")
          && !button.closest(".osw-table-scroll"))
        .filter(button => {
          const box = button.getBoundingClientRect();
          return box.left < rect.left - 2 || box.right > rect.right + 2
            || button.scrollWidth > button.clientWidth + 3
            || button.scrollHeight > button.clientHeight + 3;
        }).map(button => button.textContent.trim().slice(0, 80));
    });
    assert.deepEqual(badButtons, [], label + " action labels must fit their buttons");
    const tables = section.locator(".osw-table-scroll");
    for (let i = 0; i < await tables.count(); i++) {
      const layout = await tables.nth(i).evaluate(container => {
        container.scrollLeft = container.scrollWidth;
        const table = container.querySelector("table");
        const last = table && table.querySelector("thead th:last-child");
        const outer = container.getBoundingClientRect();
        const edge = last && last.getBoundingClientRect();
        return {
          overflowX: getComputedStyle(container).overflowX,
          containerWidth: container.clientWidth,
          tableWidth: table?.getBoundingClientRect().width,
          lastActionVisible: !edge || (edge.left >= outer.left - 2 && edge.right <= outer.right + 2),
        };
      });
      assert.equal(layout.overflowX, "auto", label + " table must support horizontal scrolling");
      assert.ok(layout.tableWidth >= layout.containerWidth - 2, label + " table width: " + JSON.stringify(layout));
      assert.ok(layout.lastActionVisible, label + " Actions column must be reachable: " + JSON.stringify(layout));
    }
  }

  async function assertCompactBrandingPreviews(panel, label) {
    const previews = await panel.evaluate(root => ["Brand logo preview", "Dark mode logo preview"].map(alt => {
      const image = root.querySelector('img[alt="' + alt + '"]');
      const container = image.parentElement;
      const img = image.getBoundingClientRect();
      const frame = container.getBoundingClientRect();
      return { alt, imageWidth: img.width, imageHeight: img.height, frameWidth: frame.width,
        frameHeight: frame.height, left: img.left, right: img.right,
        frameLeft: frame.left, frameRight: frame.right,
        naturalWidth: image.naturalWidth, naturalHeight: image.naturalHeight };
    }));
    assert.ok(Math.abs(previews[0].frameHeight - previews[1].frameHeight) <= 1,
      label + " preview containers must have equal heights");
    for (const item of previews) {
      assert.ok(item.frameHeight >= 70 && item.frameHeight <= 90,
        label + " frame must be compact: " + JSON.stringify(item));
      assert.ok(item.imageWidth <= Math.min(item.frameWidth, 208) + 2 && item.imageHeight <= 62,
        label + " preview logo must fit the intended size: " + JSON.stringify(item));
      assert.ok(item.left >= item.frameLeft - 2 && item.right <= item.frameRight + 2,
        label + " logo must stay inside preview: " + JSON.stringify(item));
      if (item.naturalWidth && item.naturalHeight && item.imageHeight) {
        assert.ok(Math.abs(item.imageWidth / item.imageHeight
          - item.naturalWidth / item.naturalHeight) < .05,
          label + " logo must retain intrinsic aspect ratio: " + JSON.stringify(item));
      }
    }
  }

  const artifactsDir = "test-artifacts/dialogs";
  async function assertModalLayout(testPage, dialog, label, capture = false) {
    await dialog.waitFor({ state: "visible" });
    await testPage.waitForFunction(() => document.body.classList.contains("osw-overlay-open"));
    const metrics = await dialog.evaluate(shell => {
      const rect = node => {
        const r = node.getBoundingClientRect();
        return { left: r.left, top: r.top, right: r.right, bottom: r.bottom, width: r.width, height: r.height };
      };
      const header = shell.querySelector(":scope > .osw-modal-header");
      const body = shell.querySelector(":scope > .osw-modal-body");
      const footers = [...shell.querySelectorAll(":scope > .osw-modal-footer")];
      const footer = footers.find(el => el.getClientRects().length > 0);
      const root = shell.closest(".osw-modal-root");
      const reference = document.querySelector('[x-data="addDomainModal"] .osw-modal-shell');
      const design = dialog => {
        const style = node => {
          const css = getComputedStyle(node);
          return { padding: css.padding, background: css.backgroundColor,
            borderTop: css.borderTopColor, borderBottom: css.borderBottomColor,
            radius: css.borderRadius, overflowY: css.overflowY };
        };
        return { shell: style(dialog), header: style(dialog.querySelector(":scope > .osw-modal-header")),
          body: style(dialog.querySelector(":scope > .osw-modal-body")),
          footer: style(dialog.querySelector(":scope > .osw-modal-footer")) };
      };
      const inputs = [...shell.querySelectorAll("input, select, textarea")]
        .filter(el => el.getClientRects().length > 0);
      return {
        viewport: [window.innerWidth, window.innerHeight],
        rootPosition: root && getComputedStyle(root).position,
        design: design(shell), referenceDesign: design(reference),
        shell: rect(shell),
        header: header && rect(header),
        body: body && rect(body),
        footer: footer && rect(footer),
        badInputs: inputs.filter(el => {
          const r = el.getBoundingClientRect(), parent = body.getBoundingClientRect();
          return r.left < parent.left - 2 || r.right > parent.right + 2;
        }).map(el => el.outerHTML.slice(0, 160)),
        badButtons: [...shell.querySelectorAll(":scope > .osw-modal-footer button")]
          .filter(el => el.getClientRects().length > 0)
          .filter(el => {
            const r = el.getBoundingClientRect();
            return r.left < -2 || r.right > innerWidth + 2 || r.top < -2 || r.bottom > innerHeight + 2
              || el.scrollWidth > el.clientWidth + 3 || el.scrollHeight > el.clientHeight + 3;
          }).map(el => el.textContent.trim()),
        backdropCount: document.querySelectorAll("#osw-modal-backdrop").length,
        backdropVisible: document.querySelector("#osw-modal-backdrop").getClientRects().length > 0,
        bodyScrollHeight: body && body.scrollHeight,
        bodyClientHeight: body && body.clientHeight,
      };
    });
    assert.equal(metrics.rootPosition, "fixed", label + " must use the domain modal fixed root");
    assert.deepEqual(metrics.design, metrics.referenceDesign,
      label + " must use the same shell/header/body/footer styling as the Add Domain reference");
    assert.deepEqual(metrics.badButtons, [], label + " actions must remain visible in the viewport");
    assert.equal(metrics.backdropCount, 1, label + " must use one backdrop");
    assert.equal(metrics.backdropVisible, true, label + " backdrop must be visible");
    assert.ok(metrics.header && metrics.body && metrics.footer, label + " requires header/body/footer: " + JSON.stringify(metrics));
    assert.ok(metrics.shell.left >= -1 && metrics.shell.right <= metrics.viewport[0] + 1 &&
      metrics.shell.top >= -1 && metrics.shell.bottom <= metrics.viewport[1] + 1,
      label + " must fit the viewport: " + JSON.stringify(metrics));
    assert.deepEqual(metrics.badInputs, [], label + " fields must fit within the modal body");
    assert.ok(Math.abs((metrics.shell.left + metrics.shell.right) / 2 - metrics.viewport[0] / 2) <= 2,
      label + " must be centered horizontally like the reference");
    assert.ok(metrics.footer.bottom <= metrics.shell.bottom + 1, label + " footer must fit");
    if (metrics.bodyScrollHeight > metrics.bodyClientHeight + 5) {
      const before = metrics.footer.top;
      await dialog.locator(":scope > .osw-modal-body").evaluate(el => { el.scrollTop = el.scrollHeight; });
      const after = await dialog.locator(":scope > .osw-modal-footer:visible").first()
        .evaluate(el => el.getBoundingClientRect().top);
      assert.ok(Math.abs(before - after) <= 2, label + " footer must stay fixed during body scroll");
    }
    if (capture) {
      fs.mkdirSync(artifactsDir, { recursive: true });
      await testPage.screenshot({ path: artifactsDir + "/" + label + ".png", fullPage: false });
    }
  }

  async function dismissAndDiscard(dialog) {
    await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
    const confirmation = page.locator('[x-data="confirmModal"]');
    if (await confirmation.isVisible()) {
      await confirmation.getByRole("button", { name: "Confirm", exact: true }).click();
    }
    await dialog.waitFor({ state: "hidden" });
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
    await page.locator("#theme-toggle").click();
    await page.waitForFunction(() => document.documentElement.dataset.theme === "light");
    await assertThemePalette("login light");
    await captureTheme("login-light");
    await page.locator("#theme-toggle").click();
    await page.waitForFunction(() => document.documentElement.dataset.theme === "dark");
    await captureTheme("login-dark");
    await page.getByLabel("Email address").fill(email);
    await page.getByLabel("Password").fill(password);
    await page.getByRole("button", { name: "Sign in" }).click();
    await page.waitForURL(url => url.origin === new URL(baseURL).origin && url.pathname === "/");
    await page.locator("#logout-button").waitFor();
    await page.getByRole("button", { name: "Admin", exact: true }).waitFor();

    checkpoint("login: complete");
    // Exercise the self-hosted CKEditor 5 surface and verify synchronization to the source textarea.
    const personalEditor = page.locator("#user-rich-editor .ck-editor__editable");
    await personalEditor.waitFor({ state: "visible" });
    // Appearance is stored in this browser, not with any signature.
    checkpoint("appearance: light/dark and CKEditor dropdown contrast");
    const themeToggle = page.locator("#theme-toggle");
    await themeToggle.click();
    await page.waitForFunction(() => document.documentElement.dataset.theme === "light");
    assert.equal(await page.evaluate(() => localStorage.getItem("opensigweave.theme.v1")), "light");
    await assertThemePalette("personal light");
    await captureTheme("personal-light");
    const lightContrast = await page.evaluate(() => {
      const channels = value => (value.match(/[\d.]+/g) || []).slice(0, 3).map(Number);
      const lum = value => channels(value).reduce((sum, v, i) => {
        const s = v / 255, linear = s <= .04045 ? s / 12.92 : ((s + .055) / 1.055) ** 2.4;
        return sum + linear * [.2126, .7152, .0722][i];
      }, 0);
      const ratio = (a, b) => (Math.max(lum(a), lum(b)) + .05) / (Math.min(lum(a), lum(b)) + .05);
      return ratio(getComputedStyle(document.body).color, getComputedStyle(document.body).backgroundColor);
    });
    assert.ok(lightContrast >= 4.5, "light theme body text must meet WCAG AA contrast");
    const personalFontFamily = page.locator("#user-rich-editor .osw-signature-family-input input");
    const personalFontSize = page.locator("#user-rich-editor .osw-signature-size-input input");
    try {
      await personalFontFamily.waitFor({ state: "visible" });
    } catch (error) {
      const diagnostics = await page.evaluate(() => ({
        CKEDITOR: !!window.CKEDITOR,
        editorApi: !!window.OpenSigWeaveEditor,
        editorHost: document.querySelector("#user-rich-editor")?.outerHTML.slice(0, 6500),
        headings: [...document.querySelectorAll("#user-rich-editor .ck-toolbar button")].map(x => x.getAttribute("aria-label")),
        fallback: document.querySelector("#user-rich-editor .osw-editor-fallback-warning")?.textContent
      }));
      process.stderr.write("[browser-smoke] font widget diagnostics " + JSON.stringify(diagnostics) + "\\n");
      throw error;
    }
    await personalFontSize.waitFor({ state: "visible" });
    const personalFontList = page.locator("#user-rich-editor .osw-font-listbox");
    assert.equal(await page.locator("#user-rich-editor .osw-ckeditor-font-input .ck-label").count(), 0,
      "font fields must not add a floating label over the placeholder");
    assert.equal(await personalFontFamily.getAttribute("aria-label"), "Font family");
    assert.equal(await personalFontSize.getAttribute("aria-label"), "Font size (pt)");
    // Plain input styling prevents CKEditor's labeled-field decoration from
    // painting a second placeholder or a light strip above our own field.
    for (const field of [personalFontFamily, personalFontSize]) {
      assert.equal(await field.evaluate(node => node.classList.contains("ck-input") || node.classList.contains("ck-input-text")), false,
        "custom font fields must not inherit CKEditor native input decoration");
      assert.equal(await field.evaluate(node => getComputedStyle(node, "::before").content), "none",
        "custom font fields must have no decorative pseudo-label");
    }
    const originalPersonalFont = await personalFontFamily.inputValue();
    await personalFontFamily.click();
    await personalFontList.waitFor({ state: "visible" });
    const fontScroll = await personalFontList.evaluate(element => ({
      height: element.clientHeight, content: element.scrollHeight,
      overflow: getComputedStyle(element).overflowY,
    }));
    assert.ok(fontScroll.content > fontScroll.height && fontScroll.overflow === "auto",
      "font family popup must be scrollable: " + JSON.stringify(fontScroll));
    await personalFontList.evaluate(element => { element.scrollTop = element.scrollHeight; });
    assert.ok(await personalFontList.evaluate(element => element.scrollTop) > 0,
      "font list must support scrolling independently of the search input");
    assert.equal(await personalFontFamily.getAttribute("aria-expanded"), "true",
      "scrolling the font choices must not dismiss the searchable dropdown");
    await personalFontFamily.fill("geo");
    assert.deepEqual(await personalFontList.locator('[role="option"]').allTextContents(), ["Georgia"],
      "typing must filter font family choices");
    await personalFontFamily.press("ArrowDown");
    assert.ok(await personalFontFamily.getAttribute("aria-activedescendant"),
      "ArrowDown must set an accessible active option");
    await personalFontFamily.press("Escape");
    assert.equal(await personalFontList.isVisible(), false);
    assert.equal(await personalFontFamily.inputValue(), originalPersonalFont,
      "Escape must restore the current formatting rather than committing a search");
    assert.equal(await page.locator("#user-rich-editor .osw-editor-font-toolbar").count(), 0,
      "font controls must not render above CKEditor");
    assert.equal(await page.locator('#user-rich-editor .osw-signature-family-input [role="option"]').filter({ hasText: "Georgia" }).count(), 1,
      "Font family input must expose its predefined listbox choices");
    const menuContrast = await personalFontFamily.evaluate(button => {
      const channels = value => (value.match(/[\d.]+/g) || []).slice(0, 3).map(Number);
      const lum = value => channels(value).reduce((sum, v, i) => {
        const s = v / 255, linear = s <= .04045 ? s / 12.92 : ((s + .055) / 1.055) ** 2.4;
        return sum + linear * [.2126, .7152, .0722][i];
      }, 0);
      const ratio = (a, b) => (Math.max(lum(a), lum(b)) + .05) / (Math.min(lum(a), lum(b)) + .05);
      return ratio(getComputedStyle(button).color, getComputedStyle(button).backgroundColor);
    });
    assert.ok(menuContrast >= 4.5, "CKEditor font controls must meet WCAG AA contrast");
    assert.equal(await personalEditor.evaluate(el => getComputedStyle(el).backgroundColor),
      "rgb(255, 255, 255)", "email composition canvas stays white in light mode");
    await page.reload();
    await personalEditor.waitFor({ state: "visible" });
    assert.equal(await page.locator("#theme-toggle").getAttribute("aria-pressed"), "true",
      "appearance must be retained across reloads");
    await page.locator("#theme-toggle").click();
    await page.waitForFunction(() => document.documentElement.dataset.theme === "dark");
    await assertThemePalette("personal dark");
    await captureTheme("personal-dark");

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
    for (const markup of ["<table", "<tbody", "<td", "<center", "<small", "<sub", "<sup", "<code", "<pre"]) {
      assert.ok(richMarkupRoundTrip.includes(markup),
        "rich editor must retain supported email markup: " + markup + "; output=" + richMarkupRoundTrip);
    }
    assert.ok(/<font\b|font-family\s*:/.test(richMarkupRoundTrip),
      "legacy font face must survive as a font tag or equivalent inline style");
    assert.match(richMarkupRoundTrip, /width="600"/,
      "rich editor must retain supported table dimensions");
    assert.match(richMarkupRoundTrip, /valign="top"/,
      "rich editor must retain supported table-cell alignment");

    // Opening or reloading raw HTML must not emit changes or discard VML/comments.
    const editorSourceProbe = await page.evaluate(async () => {
      const host = document.createElement("div");
      document.body.appendChild(host);
      const initial = '<p style="font-size:17px">Source</p><!--[if mso]><v:roundrect>VML</v:roundrect><![endif]-->';
      const replacement = '<table role="presentation"><tr><td>Layout</td></tr></table><!--preserve-comment-->';
      let changes = 0;
      let instance;
      try {
        instance = await window.OpenSigWeaveEditor.create(host, {
          initialData: initial, onChange: () => { changes++; }
        });
        const untouched = instance.getData() === initial;
        instance.setData(replacement);
        const reset = instance.getData() === replacement;
        const silentLoad = changes === 0;
        instance.insertText("changed");
        return {
          untouched, reset, silentLoad,
          edited: changes > 0 && instance.getData() !== replacement
            && instance.getData().includes("changed")
        };
      } finally {
        if (instance) await instance.destroy();
        host.remove();
      }
    });
    assert.deepEqual(editorSourceProbe, {
      untouched: true, reset: true, silentLoad: true, edited: true
    }, "source-preserving adapter must distinguish reloads from real CKEditor edits");

    checkpoint("personal editor: complete");
    const personal = await page.request.get(baseURL + "/api/my-signature", { timeout: 15000 });
    assert.equal(personal.status(), 200, "signed-in administrator can load personal signature");

    await page.getByRole("button", { name: "Admin", exact: true }).click();
    assert.equal(await page.locator('div[x-show="tab === \'admin\'"] > div').first().locator('button').first().textContent(), "System",
      "System must be the first administrator navigation item");
    await page.getByRole("heading", { name: "Identity Providers" }).waitFor({ state: "visible" });
    assert.match(page.url(), /[?&]section=identity-providers(?:&|$)/,
      "first administrator opening must use Identity Providers and persist it in the URL");
    await page.reload();
    await page.getByRole("heading", { name: "Identity Providers" }).waitFor({ state: "visible" });
    await page.getByRole("button", { name: "Domains", exact: true }).click();
    await page.getByRole("heading", { name: "Managed Domains" }).waitFor({ state: "visible" });
    await page.reload();
    await page.getByRole("heading", { name: "Managed Domains" }).waitFor({ state: "visible" });
    assert.match(page.url(), /[?&]section=domains(?:&|$)/, "admin tab must survive reload via URL");
    await page.getByRole("button", { name: "My Signature", exact: true }).click();
    await page.getByRole("button", { name: "Admin", exact: true }).click();
    await page.getByRole("heading", { name: "Managed Domains" }).waitFor({ state: "visible" });
    await page.goBack();
    await page.getByRole("heading", { name: "Personal Signature Override" }).waitFor({ state: "visible" });
    await page.goForward();
    await page.getByRole("heading", { name: "Managed Domains" }).waitFor({ state: "visible" });
    await page.goto(baseURL + "/?view=admin&section=system");
    await page.getByRole("heading", { name: "System Status" }).waitFor({ state: "visible" });
    await page.getByRole("button", { name: "Domains", exact: true }).click();
    const systemTabReload = page.waitForResponse(response =>
      response.url().includes("/api/admin/system-status") && response.request().method() === "GET");
    await page.getByRole("button", { name: "System", exact: true }).click();
    assert.equal((await systemTabReload).status(), 200, "revisiting System must request fresh status");
    await page.getByRole("button", { name: "Domains", exact: true }).click();

    checkpoint("admin: open");
    const sections = [
      { tab: "Identity Providers", heading: "Identity Providers" },
      { tab: "Domains", heading: "Managed Domains" },
      { tab: "Signatures", heading: "Signature Catalog" },
      { tab: "Signature Groups", heading: "Signature Groups" },
      { tab: "Rules", heading: "Signature Rules" },
      { tab: "Branding", heading: "Branding" },
      { tab: "Backup", heading: "Backup Management" },
      { tab: "Import / Export", heading: "Import / Export" },
      { tab: "System", heading: "System Status" },
    ];
    // The other tabs keep their relative positions, with Backup immediately before Import / Export.
    const adminNavigation = await page.evaluate(() => [...document.querySelectorAll("button")]
      .filter(button => button.getAttribute("@click")?.startsWith("setAdminTab("))
      .map(button => button.textContent.trim()));
    assert.deepEqual(adminNavigation, [
      "System", "Identity Providers", "Domains", "Signatures", "Signature Groups",
      "Rules", "Branding", "Backup", "Import / Export",
    ]);
    assert.equal(await page.getByRole("button", { name: "Imports", exact: true }).count(), 0);
    assert.equal(await page.getByRole("button", { name: "Exports", exact: true }).count(), 0);
    // Test every administrator tab in each appearance. Capture comparable
    // reference images; CI stores them alongside the dialog snapshots.
    for (const appearance of ["light", "dark"]) {
      await page.locator("#theme-toggle").click();
      await page.waitForFunction(mode => document.documentElement.dataset.theme === mode, appearance);
      for (const section of sections) {
        await page.getByRole("button", { name: section.tab, exact: true }).click();
        await page.getByRole("heading", { name: section.heading }).waitFor();
        await assertThemePalette(appearance + " / " + section.tab);
        await captureTheme(appearance + "-" + section.tab.toLowerCase().replaceAll(" ", "-"));
      }
    }

    // Old bookmarks and browser history must land on the one canonical section.
    for (const legacySection of ["imports", "exports"]) {
      await page.goto(baseURL + "/?view=admin&section=" + legacySection);
      await page.getByRole("heading", { name: "Import / Export", exact: true }).waitFor();
      assert.match(page.url(), /[?&]section=import-export(?:&|$)/,
        "old " + legacySection + " link must be canonicalized");
      await page.getByRole("heading", { name: "Import", exact: true }).waitFor();
      await page.getByRole("heading", { name: "Export", exact: true }).waitFor();
    }
    // Container-native backups must not require a root-side host operator.
    await page.getByRole("button", { name: "Backup", exact: true }).click();
    const backupPanel = page.locator('[x-data="backupManager"]');
    await backupPanel.getByRole("heading", { name: "Backup Locations" }).waitFor();
    await backupPanel.getByRole("heading", { name: "Backup Rules" }).waitFor();
    await backupPanel.getByRole("heading", { name: "Backup History" }).waitFor();
    assert.equal(await backupPanel.getByRole("heading", { name: "Backup Settings" }).count(), 0);
    assert.equal(await backupPanel.getByRole("heading", { name: "Encrypted Backups" }).count(), 0);
    assert.equal(await backupPanel.getByText(/host backup operator is unavailable/i).count(), 0);

    // Create a real local Backup Location and save a disabled rule through
    // the entire dialog, including a footer reachable below the scroll area.
    await backupPanel.getByRole("button", { name: "+ Add Backup Location" }).click();
    const backupLocationDialog = page.getByRole("dialog", { name: "Add Backup Location" });
    await backupLocationDialog.locator('input[x-model="locationForm.name"]').fill("Browser Smoke Backup Location");
    const locationCreated = page.waitForResponse(response =>
      response.url().endsWith("/api/admin/backups/locations") && response.request().method() === "POST");
    await backupLocationDialog.getByRole("button", { name: "Save Location" }).click();
    assert.equal((await locationCreated).status(), 201, "local backup location must save");
    const backupLocationRow = backupPanel.locator("tbody tr").filter({ hasText: "Browser Smoke Backup Location" });
    await backupLocationRow.waitFor({ state: "visible" });

    await backupPanel.getByRole("button", { name: "+ Add Backup Rule" }).click();
    const backupRuleDialog = page.getByRole("dialog", { name: "Add Backup Rule" });
    await backupRuleDialog.locator('input[x-model="ruleForm.name"]').fill("Browser Smoke Backup Rule");
    await backupRuleDialog.locator('input[x-model="ruleForm.enabled"]').uncheck();
    const backupRuleLayout = await backupRuleDialog.evaluate(dialog => {
      const body = dialog.querySelector(".osw-modal-body");
      const footer = dialog.querySelector(".osw-modal-footer");
      const shell = dialog.getBoundingClientRect();
      const actions = footer.getBoundingClientRect();
      return { scrollable: getComputedStyle(body).overflowY === "auto",
        scrollHeight: body.scrollHeight, clientHeight: body.clientHeight,
        footerVisible: actions.bottom <= shell.bottom + 1 && actions.top >= shell.top };
    });
    assert.ok(backupRuleLayout.scrollable && backupRuleLayout.footerVisible,
      "backup rule body must scroll independently of the visible Save Rule footer: " + JSON.stringify(backupRuleLayout));
    const backupRuleCreated = page.waitForResponse(response =>
      response.url().endsWith("/api/admin/backups/rules") && response.request().method() === "POST");
    await backupRuleDialog.getByRole("button", { name: "Save Rule" }).click();
    assert.equal((await backupRuleCreated).status(), 201, "Backup Rule must persist");
    const backupRuleRow = backupPanel.locator("tbody tr").filter({ hasText: "Browser Smoke Backup Rule" });
    await backupRuleRow.waitFor({ state: "visible" });
    await backupRuleRow.getByRole("button", { name: "Delete", exact: true }).click();
    await page.locator('[x-data="confirmModal"]').getByRole("button", { name: "Confirm", exact: true }).click();
    await backupRuleRow.waitFor({ state: "detached" });
    await backupLocationRow.getByRole("button", { name: "Delete", exact: true }).click();
    await page.locator('[x-data="confirmModal"]').getByRole("button", { name: "Confirm", exact: true }).click();
    await backupLocationRow.waitFor({ state: "detached" });
    checkpoint("backup rule save and dialog scrolling: complete");

    // Confirm outdated implementation notes stay absent across all administration tabs and dialogs.
    const uiCopy = await page.locator("body").textContent();
    for (const obsolete of [
      /Your managed signature is selected by rules/i,
      /Domain master templates are no longer used/i,
      /Manage signature assets, immutable versions and domain assignments/i,
      /Settings and uploaded assets are stored in PostgreSQL/i,
      /Encrypted backups, disaster-recovery restore, secret rotation/i,
      /The accent color is applied to the product name/i,
    ]) {
      assert.doesNotMatch(uiCopy, obsolete, "obsolete UI explanation: " + obsolete);
    }
    // Retain required warnings, import limits, version status and priority guidance.
    assert.match(uiCopy, /Historical versions are read-only/);
    assert.match(uiCopy, /HTTPS images only/);
    assert.match(uiCopy, /Imports never overwrite existing records/);
    assert.match(uiCopy, /Outlook-specific conditional comments/);
    assert.match(uiCopy, /External identity providers and mail servers are not checked/);
    const schedulingHelp = page.locator('[x-data="ruleManager"] details').filter({ hasText: "Scheduling priority" });
    assert.equal(await schedulingHelp.count(), 1, "schedule precedence must remain available on demand");
    assert.equal(await schedulingHelp.getAttribute("open"), null, "scheduling help should start collapsed");

    // The backup tab is checked separately; restore System before status assertions.
    await page.getByRole("button", { name: "System", exact: true }).click();
    const systemPanel = page.locator('[x-data="systemStatusManager"]');
    await systemPanel.getByText("Healthy", { exact: true }).waitFor();
    const cardGap = await systemPanel.evaluate(section => {
      const summary = section.querySelector(':scope > .grid');
      const next = section.querySelector(':scope > .bg-gray-800');
      return next.getBoundingClientRect().top - summary.getBoundingClientRect().bottom;
    });
    assert.ok(cardGap >= 20, "separate system context cards need visible spacing: " + cardGap);
    await systemPanel.getByRole("button", { name: "Refresh Status" }).click();
    await systemPanel.getByText("Healthy", { exact: true }).waitFor();
    await page.route("**/api/admin/system-status", route => route.fulfill({
      status: 503, contentType: "application/json", body: JSON.stringify({ detail: "Synthetic dependency outage" }),
    }));
    await systemPanel.getByRole("button", { name: "Refresh Status" }).click();
    await systemPanel.getByRole("alert").waitFor({ state: "visible" });
    assert.match(await systemPanel.getByRole("alert").textContent(), /Synthetic dependency outage/);
    await systemPanel.getByText("Unavailable", { exact: true }).first().waitFor();
    await page.unroute("**/api/admin/system-status");
    await systemPanel.getByRole("button", { name: "Refresh Status" }).click();
    await systemPanel.getByText("Healthy", { exact: true }).waitFor();

    await page.getByRole("button", { name: "Import / Export", exact: true }).click();
    await page.getByRole("button", { name: "Choose ZIP File", exact: true }).waitFor({ state: "visible" });
    await page.locator('[x-data="adminImportManager"]').getByText("No file selected", { exact: true }).waitFor({ state: "visible" });
    await page.getByRole("button", { name: "Rules", exact: true }).click();
    // Return to the shared Import / Export view after visiting other sections.
    await page.getByRole("button", { name: "Import / Export", exact: true }).click();

    checkpoint("admin sections: complete");
    // Every create/edit workspace must float above the overview instead of
    // expanding the current page. Also verify the provider dialog can be cancelled.
    await page.getByRole("button", { name: "Identity Providers", exact: true }).click();
    const providerPanel = page.locator('[x-data="identityProviderManager"]');
    await providerPanel.getByRole("button", { name: "+ Add Identity Provider" }).click();
    const providerDialog = providerPanel.locator('[x-show="editing"][role="dialog"]');
    await assertFloatingDialog(providerDialog, "identity provider editor");
    await page.evaluate(() => document.getElementById("theme-toggle").click());
    await page.waitForFunction(() => document.documentElement.dataset.theme === "light");
    await assertThemePalette("identity provider modal light");
    await captureTheme("modal-provider-light");
    await page.evaluate(() => document.getElementById("theme-toggle").click());
    await page.waitForFunction(() => document.documentElement.dataset.theme === "dark");
    await captureTheme("modal-provider-dark");
    // All admin create/edit dialogs use the same Domains-style header, scrollable body and footer.
    const unifiedDialogs = await page.evaluate(() => [...document.querySelectorAll(".form-dialog")].map(dialog => ({
      header: !!dialog.querySelector(":scope > .osw-modal-header"),
      body: !!dialog.querySelector(":scope > .osw-modal-body"),
      footer: !!dialog.querySelector(":scope > .osw-modal-footer"),
      shell: dialog.classList.contains("osw-modal-shell"),
      root: !!dialog.closest(".osw-modal-root"),
      placement: !!dialog.closest(".osw-modal-placement"),
    })));
    assert.equal(unifiedDialogs.length, 5, "five administrator create/edit dialogs should share one layout after merging schedules into rules");
    for (const item of unifiedDialogs) assert.deepEqual(item, {
      header: true, body: true, footer: true, shell: true, root: true, placement: true,
    }, "each administrator dialog must have the shared Domains-style shell");
    assert.equal(await page.locator('[x-data="addDomainModal"] input[x-model="domainName"]').getAttribute("placeholder"), "example.com",
      "domain placeholder must use the neutral example.com address");
    assert.equal(await providerDialog.locator(".osw-modal-footer").isVisible(), true,
      "identity provider dialog must show a fixed action footer");
    await providerDialog.press("Escape");
    await providerDialog.waitFor({ state: "hidden" });
    await page.getByRole("button", { name: "Import / Export", exact: true }).click();

    // Exercise the actual administrator export UI and verify a ZIP download is produced.
    const exportPanel = page.locator('[x-data="adminExportManager"]');
    const exportButton = exportPanel.getByRole("button", { name: "Download selected components" });
    // Visual switch styling must not turn multi-select export fields into binary ARIA switches.
    const exportGroupSelection = exportPanel.getByRole("checkbox", { name: "Signature groups and memberships" });
    assert.equal(await exportGroupSelection.getAttribute("role"), null);
    assert.equal(await exportGroupSelection.isChecked(), true);
    await exportGroupSelection.uncheck();
    assert.equal(await exportGroupSelection.isChecked(), false);
    await exportGroupSelection.check();
    assert.equal(await exportGroupSelection.isChecked(), true);
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
    await page.getByRole("button", { name: "Import / Export", exact: true }).click();
    const importPanel = page.locator('[x-data="adminImportManager"]');
    assert.equal(await page.locator('[x-show="adminTab === \'import-export\'"] > section').count(), 2,
      "combined tab must contain exactly the Import and Export sections");
    assert.equal(await exportPanel.getByRole("button", { name: "Download selected components" }).isVisible(), true,
      "export must remain available when Import is active");
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

    // Replacing the selection must invalidate the previous preview.
    await importPanel.locator("#admin-import-file").setInputFiles({
      name: "invalid.zip", mimeType: "application/zip", buffer: Buffer.from("not a ZIP archive"),
    });
    assert.equal(await importPanel.getByText("Archive validated. No conflicts detected.").isVisible(), false);
    const invalidResponsePromise = page.waitForResponse(response =>
      response.url().includes("/api/admin/imports/preview")
    );
    await importPreviewButton.click();
    const invalidResponse = await invalidResponsePromise;
    assert.ok(invalidResponse.status() >= 400 && invalidResponse.status() < 500,
      "invalid archive should produce a client error, received " + invalidResponse.status());
    await importPanel.getByRole("alert").waitFor({ state: "visible" });

    await importPanel.locator("#admin-import-file").setInputFiles({
      name: "incomplete.zip", mimeType: "application/zip", buffer: Buffer.from([0x50, 0x4b, 0x03, 0x04]),
    });
    const incompleteResponsePromise = page.waitForResponse(response =>
      response.url().includes("/api/admin/imports/preview")
    );
    await importPreviewButton.click();
    const incompleteResponse = await incompleteResponsePromise;
    assert.ok(incompleteResponse.status() >= 400 && incompleteResponse.status() < 500,
      "incomplete archive should produce a client error, received " + incompleteResponse.status());
    await importPanel.getByRole("alert").waitFor({ state: "visible" });

    await importPanel.locator("#admin-import-file").setInputFiles([]);
    assert.equal(await importPreviewButton.isDisabled(), true, "no archive selected must disable validation");
    await importPanel.getByText("No file selected", { exact: true }).waitFor({ state: "visible" });
    checkpoint("import preview: complete");
    const formattingSample = await page.evaluate(async () => {
      const host = document.createElement("div");
      document.body.appendChild(host);
      let instance;
      try {
        instance = await window.OpenSigWeaveEditor.create(host, {
          initialData: '<p><span style="font-size: 14px; font-family: Arial, Helvetica, sans-serif;">' +
            'Pixel font</span> <span style="font-size: 17px; font-family: Georgia, serif;">Custom</span></p>'
        });
        return instance.getData();
      } finally {
        if (instance) await instance.destroy();
        host.remove();
      }
    });
    assert.match(formattingSample, /font-size:\s*14px/i,
      "font size must be serialized as an inline px style");
    assert.match(formattingSample, /font-size:\s*17px/i,
      "custom px sizes from existing signatures must survive editor loading");
    assert.match(formattingSample, /font-family:\s*Arial/i,
      "email-safe font family must survive editor loading");
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
    const fontToolbar = signatureDialog.locator("#signature-template-editor .ck-toolbar");
    const fontFamilyControl = fontToolbar.locator(".osw-signature-family-input input");
    const fontSizeControl = fontToolbar.locator(".osw-signature-size-input input");
    await fontFamilyControl.waitFor({ state: "visible" });
    await fontSizeControl.waitFor({ state: "visible" });
    assert.equal(await fontToolbar.getByRole("button", { name: /Paragraph|Heading/ }).count(), 0,
      "heading presets must not appear in the signature toolbar");
    assert.equal(await signatureDialog.locator(".osw-editor-font-toolbar").count(), 0,
      "no external font toolbar may remain");
    await page.evaluate(() => document.getElementById("theme-toggle").click());
    await page.waitForFunction(() => document.documentElement.dataset.theme === "light");
    await fontFamilyControl.click();
    const lightFontMenuBg = await fontToolbar.locator(".osw-font-listbox").evaluate(element => getComputedStyle(element).backgroundColor);
    await fontFamilyControl.press("Escape");
    assert.equal(await signatureDialog.isVisible(), true, "Escape in the font popup must not close the editor dialog");
    await captureTheme("signature-editor-light");
    await page.evaluate(() => document.getElementById("theme-toggle").click());
    await page.waitForFunction(() => document.documentElement.dataset.theme === "dark");
    await fontFamilyControl.click();
    const darkFontMenuBg = await fontToolbar.locator(".osw-font-listbox").evaluate(element => getComputedStyle(element).backgroundColor);
    await fontFamilyControl.press("Escape");
    assert.equal(await signatureDialog.isVisible(), true, "Escape must dismiss only the font list, not the dialog");
    await fontFamilyControl.click();
    assert.equal(await fontToolbar.locator(".osw-font-listbox").isVisible(), true,
      "clicking a focused font input must reopen the menu after Escape");
    await fontFamilyControl.press("Escape");
    assert.notEqual(lightFontMenuBg, darkFontMenuBg,
      "font list must use different theme surfaces in light and dark appearance");
    await captureTheme("signature-editor-dark");
    assert.equal(await fontFamilyControl.isEnabled(), true);
    assert.equal(await fontSizeControl.isEnabled(), true);
    for (const input of [fontFamilyControl, fontSizeControl]) {
      assert.equal(await input.locator("xpath=..").locator(".ck-label").count(), 0,
        "neither font control may render overlapping labels");
      assert.equal(await input.evaluate(element => getComputedStyle(element).backgroundColor === "rgba(0, 0, 0, 0)"), false,
        "font inputs must have a themed background");
    }
    for (const name of [
      "Arial", "Arial Black", "Calibri", "Cambria", "Courier New",
      "Garamond", "Georgia", "Helvetica", "Lucida Sans Unicode",
      "Palatino Linotype", "Segoe UI", "Tahoma", "Times New Roman",
      "Trebuchet MS", "Verdana"
    ]) {
      assert.ok((await fontToolbar.locator(".osw-signature-family-input [role='option']").allTextContents()).includes(name),
        "CKEditor must offer email-safe font " + name);
    }
    for (const theme of ["dark", "light"]) {
      const contrast = await fontSizeControl.evaluate((button, themeName) => {
        const root = document.documentElement;
        const previous = root.dataset.theme;
        root.dataset.theme = themeName;
        try {
          const style = getComputedStyle(button);
          const lum = color => (color.match(/[\d.]+/g) || []).slice(0, 3).map(Number)
            .reduce((sum, channel, i) => {
              const s = channel / 255;
              return sum + (s <= .04045 ? s / 12.92 : ((s + .055) / 1.055) ** 2.4)
                * [.2126, .7152, .0722][i];
            }, 0);
          return (Math.max(lum(style.color), lum(style.backgroundColor)) + .05)
            / (Math.min(lum(style.color), lum(style.backgroundColor)) + .05);
        } finally {
          root.dataset.theme = previous;
        }
      }, theme);
      assert.ok(contrast >= 4.5, "font size toolbar contrast in " + theme);
    }
    assert.equal(await signatureDialog.isVisible(), true, "signature dialog must remain open throughout font interactions");
    await signaturePanel.getByLabel("Name").fill("Browser Smoke Signature");
    const signatureCreateEditor = signaturePanel.locator("#signature-template-editor .ck-editor__editable");
    await signatureCreateEditor.fill("Browser smoke version 1");
    await signatureCreateEditor.press("Control+A");
    await fontFamilyControl.fill("Camb");
    assert.deepEqual(await fontToolbar.locator(".osw-font-listbox [role='option']").allTextContents(), ["Cambria"]);
    await fontToolbar.getByRole("option", { name: "Cambria" }).click();
    await fontFamilyControl.click();
    assert.equal(await fontToolbar.locator(".osw-font-listbox [role='option']").count(), 15,
      "opening a field with an existing family must show all choices");
    await fontFamilyControl.press("Escape");
    await signatureCreateEditor.press("Control+A");
    checkpoint("signature editor: before 18pt application");
    await fontSizeControl.fill("18");
    await fontSizeControl.press("Enter");
    await page.waitForFunction(() => /font-size:\s*18pt/i.test(
      document.querySelector("#signature-html-source")?.value || ""
    ));
    await signatureCreateEditor.press("Control+A");
    const customFontInput = fontSizeControl;
    await customFontInput.fill("0");
    await customFontInput.press("Enter");
    assert.notEqual(await customFontInput.evaluate(input => input.validationMessage), "",
      "invalid custom size must be rejected");
    await customFontInput.fill("17,5");
    await customFontInput.press("Enter");
    const completedFontFormatting = await signaturePanel.locator("#signature-html-source").inputValue();
    assert.match(completedFontFormatting, /font-family:\s*Cambria/i,
      "font family must appear in synchronized HTML");
    assert.match(completedFontFormatting, /font-size:\s*17\.5pt/i,
      "custom decimal point sizes must become email-compatible inline styles");
    assert.match(await fontFamilyControl.inputValue(), /Cambria/i,
      "font family input must reflect the selected formatting");
    assert.match(await fontSizeControl.inputValue(), /17\.5/i,
      "plain size input must reflect the selected custom decimal value");
    await signaturePanel.getByRole("button", { name: "Save Signature", exact: true }).click();

    const signatureRow = signaturePanel.locator("tbody tr").filter({ hasText: "Browser Smoke Signature" });
    await signatureRow.waitFor();
    // Filter by search, state and scope without changing the persisted catalog.
    const signatureSearch = signaturePanel.getByRole("searchbox", { name: "Search signatures" });
    const signatureStatusFilter = signaturePanel.getByRole("combobox", { name: "Signature status" });
    const signatureDomainFilter = signaturePanel.getByRole("combobox", { name: "Domain scope" });
    await signatureSearch.fill("not-present-in-catalog");
    await signaturePanel.getByText("No signatures match the selected filters.").waitFor({ state: "visible" });
    await signatureSearch.fill("Browser Smoke Signature");
    await signatureRow.waitFor({ state: "visible" });
    await signatureStatusFilter.selectOption("inactive");
    await signatureRow.waitFor({ state: "hidden" });
    await signatureStatusFilter.selectOption("active");
    await signatureRow.waitFor({ state: "visible" });
    await signatureStatusFilter.selectOption("all");
    await signatureDomainFilter.selectOption("global");
    await signatureRow.waitFor({ state: "visible" });
    await signatureDomainFilter.selectOption("all");
    await signatureSearch.fill("");
    // Preview must retrieve the saved current version without changing it.
    await signatureRow.getByRole("button", { name: "Preview", exact: true }).click();
    const catalogPreview = page.locator('[x-data="previewModal"]');
    await catalogPreview.waitFor({ state: "visible" });
    assert.match(await page.locator("#signature-preview-frame").getAttribute("srcdoc"), /Browser smoke version 1/,
      "catalog preview must show the persisted current signature version");
    await page.keyboard.press("Escape");
    await catalogPreview.waitFor({ state: "hidden" });
    // Status change uses the same confirmation pattern as other administrator actions.
    const signatureBadge = signatureRow.getByRole("button", { name: "Disable signature Browser Smoke Signature" });
    await signatureBadge.click();
    const statusConfirm = page.locator('[x-data="confirmModal"]');
    await statusConfirm.getByRole("button", { name: "Confirm", exact: true }).click();
    await signatureRow.getByRole("button", { name: "Enable signature Browser Smoke Signature" }).waitFor();
    await signatureRow.getByRole("button", { name: "Enable signature Browser Smoke Signature" }).click();
    await statusConfirm.getByRole("button", { name: "Confirm", exact: true }).click();
    await signatureBadge.waitFor({ state: "visible" });
    await signatureRow.getByRole("button", { name: "Edit", exact: true }).click();
    const signatureEditEditor = signaturePanel.locator("#signature-template-editor .ck-editor__editable");
    await signatureEditEditor.waitFor({ state: "visible" });
    // The editor instance stays mounted between dialogs. Do not mistake the
    // previous instance's HTML for the asynchronously retrieved saved version.
    await page.waitForFunction(() => {
      const html = document.querySelector("#signature-html-source")?.value || "";
      return /font-family:\s*Cambria/i.test(html) && /font-size:\s*17\.5pt/i.test(html);
    });
    assert.match(await signatureEditEditor.innerHTML(), /Browser smoke version 1/,
      "editing an existing signature must load the latest stored HTML version");
    const reloadedHtml = await signaturePanel.locator("#signature-html-source").inputValue();
    assert.match(reloadedHtml, /font-family:\s*Cambria/i,
      "the stored font family must survive closing and reopening the signature editor");
    assert.match(reloadedHtml, /font-size:\s*17\.5pt/i,
      "stored decimal pt size must survive closing and reopening");
    await signatureEditEditor.click();
    await signatureEditEditor.press("Control+A");
    assert.match(await fontFamilyControl.inputValue(), /Cambria/i,
      "font family input must reflect a previously stored selection");
    assert.match(await fontSizeControl.inputValue(), /17\.5/i,
      "font size input must reflect a previously stored custom value");
    const versionSelector = signaturePanel.locator('select[x-model="selectedVersionId"]');
    // Versions load from a separate asynchronous API request. The editor
    // could still display data from its previous instance; wait for the
    // actual history response before asserting the exact version count.
    await versionSelector.locator("option").first().waitFor({ state: "attached" });
    assert.equal(await versionSelector.locator("option").count(), 1,
      "newly created signature must expose its first version in version history");
    const storedVersion = await page.evaluate(async () => {
      const state = Alpine.$data(document.querySelector('[x-data="signatureManager"]'));
      const response = await fetch("/api/admin/signatures/" + encodeURIComponent(state.form.id)
        + "/versions/" + encodeURIComponent(state.selectedVersionId));
      return { status: response.status, version: await response.json() };
    });
    assert.equal(storedVersion.status, 200, "saved signature HTML must be retrievable from the backend");
    assert.match(storedVersion.version.html_content, /font-family:\s*Cambria/i);
    assert.match(storedVersion.version.html_content, /font-size:\s*17\.5pt/i);

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
        detail: { html: "<p>Browser smoke preview overlay</p>" + document.querySelector("#signature-html-source").value }
      }));
    });
    await signaturePreviewModal.waitFor({ state: "visible" });
    const previewLayering = await page.evaluate(() => {
      const preview = document.querySelector('[x-data="previewModal"]');
      const editor = document.querySelector('[x-data="signatureManager"] [x-show="editing"][role="dialog"]');
      // All dialogs now use a shared fixed overlay root, so stacking belongs
      // to that root rather than the inner editor shell.
      const editorRoot = editor.closest(".osw-modal-root");
      const topmost = document.elementFromPoint(innerWidth / 2, innerHeight / 2);
      return {
        preview: Number.parseInt(getComputedStyle(preview).zIndex, 10),
        editor: Number.parseInt(getComputedStyle(editorRoot).zIndex, 10),
        previewOnTop: !!topmost && preview.contains(topmost),
        srcdoc: document.querySelector("#signature-preview-frame")?.getAttribute("srcdoc") || ""
      };
    });
    assert.ok(previewLayering.preview > previewLayering.editor,
      "signature preview must be layered above the floating signature editor: " + JSON.stringify(previewLayering));
    assert.equal(previewLayering.previewOnTop, true,
      "preview must be the topmost rendered dialog at the viewport center");
    assert.match(previewLayering.srcdoc, /Browser smoke preview overlay/,
      "signature preview iframe must receive the requested HTML");
    assert.match(previewLayering.srcdoc, /font-family:\s*Cambria/i,
      "signature preview must retain the configured font family");
    assert.match(previewLayering.srcdoc, /font-size:\s*17\.5pt/i,
      "signature preview must retain the custom point size");
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
    // A 1×1-pixel fixture is already selected after insertion; its resize handles
    // cover the entire image and intercept simulated pointer clicks.
    const imageResizeControl = page.getByRole("button", { name: /resize image/i }).first();
    await imageResizeControl.waitFor({ state: "visible" });
    await imageResizeControl.click();
    await page.getByText("120 px", { exact: true }).first().click();
    await page.waitForFunction(() => /120(?:px)?/.test(document.querySelector("#signature-html-source").value));
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

    // Import a managed image through the new CKEditor toolbar action, without
    // relying on public internet access in CI. Backend SSRF checks have unit tests.
    checkpoint("image URL toolbar import: validation, insertion, preview and persistence");
    const urlImportToolbarButton = signatureToolbar.getByRole("button", {
      name: "Import Image from URL", exact: true
    });
    await urlImportToolbarButton.waitFor({ state: "visible" });
    assert.equal(await urlImportToolbarButton.isEnabled(), true);
    const previousViewport = page.viewportSize();
    await page.setViewportSize({ width: 390, height: 780 });
    await urlImportToolbarButton.waitFor({ state: "hidden" });
    const mobileUrlImportButton = signatureDialog.getByRole("button", {
      name: "Import Image from URL", exact: true
    });
    await mobileUrlImportButton.waitFor({ state: "visible" });
    assert.equal(await mobileUrlImportButton.isEnabled(), true,
      "mobile image URL import action must remain enabled");
    await mobileUrlImportButton.scrollIntoViewIfNeeded();
    const buttonFits = await mobileUrlImportButton.evaluate(button => {
      const rect = button.getBoundingClientRect();
      return rect.left >= 0 && rect.right <= window.innerWidth;
    });
    assert.equal(buttonFits, true, "mobile image URL import action must fit narrow screens");
    await page.setViewportSize(previousViewport);
    await urlImportToolbarButton.waitFor({ state: "visible" });

    let remoteImportRequests = 0;
    await page.route("**/api/admin/signature-assets/import-url", async route => {
      remoteImportRequests += 1;
      const { url } = route.request().postDataJSON();
      if (url === "https://example.org/logo.png") {
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ url: dragAssetPath }) });
      } else {
        await route.fulfill({ status: 422, contentType: "application/json", body: JSON.stringify({ detail: "Unsupported remote image type" }) });
      }
    });
    await signatureEditEditor.click();
    await page.keyboard.press("Control+End");
    await page.keyboard.type("BEFOREAFTER");
    for (let index = 0; index < 5; index++) await page.keyboard.press("ArrowLeft");
    await urlImportToolbarButton.click();
    const imageImportDialog = signaturePanel.getByRole("dialog", { name: "Import Image from URL" });
    await assertFloatingDialog(imageImportDialog, "image URL importer");
    await assertModalLayout(page, imageImportDialog, "image-url-import");
    assert.equal(await signatureDialog.locator('input[placeholder="https://example.com/logo.png"]').count(), 0,
      "obsolete URL input must not remain below the editor");
    const imageImportInput = imageImportDialog.getByRole("textbox", { name: "Image URL" });
    await imageImportInput.fill("http://127.0.0.1/internal.png");
    await imageImportDialog.getByRole("button", { name: "Import image" }).click();
    assert.equal(remoteImportRequests, 0, "non-HTTPS images must be rejected before the fetch");
    await imageImportInput.fill("https://example.org/not-an-image.png");
    await imageImportDialog.getByRole("button", { name: "Import image" }).click();
    await page.waitForFunction(() => document.querySelector("#signature-image-import-url")?.disabled === false);
    assert.equal(remoteImportRequests, 1, "invalid image response must reach the importer");
    assert.equal(await signatureEditEditor.locator('img[src*="/signature-assets/"]').count(), 3,
      "failed imports must not add an image");
    await imageImportInput.fill("https://example.org/logo.png");
    await imageImportDialog.getByRole("button", { name: "Import image" }).click();
    await imageImportDialog.waitFor({ state: "hidden" });
    await page.unroute("**/api/admin/signature-assets/import-url");
    assert.equal(remoteImportRequests, 2, "successful URL import must reuse the server endpoint");
    await signatureEditEditor.locator('img[src*="/signature-assets/"]').nth(3).waitFor();
    const urlImportedHtml = await signatureEditEditor.innerHTML();
    assert.ok(urlImportedHtml.lastIndexOf("BEFORE") < urlImportedHtml.lastIndexOf(dragAssetPath)
      && urlImportedHtml.lastIndexOf(dragAssetPath) < urlImportedHtml.lastIndexOf("AFTER"),
      "URL image must be inserted at the previously saved cursor position");
    assert.equal(await signatureEditEditor.locator('img[src*="/signature-assets/"]').count(), 4);
    const urlImportedSource = await signaturePanel.locator("#signature-html-source").inputValue();
    assert.ok(urlImportedSource.includes(baseURL + dragAssetPath),
      "URL-imported image must be persisted as a managed absolute URL");
    await page.evaluate(() => {
      window.dispatchEvent(new CustomEvent("open-preview", {
        detail: { html: document.querySelector("#signature-html-source").value }
      }));
    });
    const importedPreview = page.locator('[x-data="previewModal"]');
    await importedPreview.waitFor({ state: "visible" });
    assert.ok((await page.locator("#signature-preview-frame").getAttribute("srcdoc")).includes(dragAssetPath),
      "URL-imported image must appear in signature preview HTML");
    await page.keyboard.press("Escape");
    await importedPreview.waitFor({ state: "hidden" });

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
    assert.equal(persistedImageSrcs.length, 4, "uploaded and URL-imported images must survive save and reopen");
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
    await historicalEditor.locator('img[src*="/signature-assets/"]').nth(3).waitFor();
    assert.equal(await historicalEditor.locator('img[src*="/signature-assets/"]').count(), 4,
      "historical version must retain both uploaded and URL-imported images");
    assert.equal(await urlImportToolbarButton.isDisabled(), true,
      "image URL import must be disabled when browsing a read-only historical version");

    checkpoint("signature image upload/persistence: complete");
    checkpoint("signature create/update: complete");

    // The image test leaves version 2 in history and creates version 3 without image
    // references. Close its editor before starting the independent history/restore flow.
    await dismissAndDiscard(signatureDialog);
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
    const historicalStatus = signatureDialog.getByText("Previewing", { exact: false });
    await historicalStatus.waitFor();
    assert.match(await historicalStatus.innerText(), /v1\s*\(current:\s*v3\)/i,
      "previewing an old version must identify the selected and current versions");

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

    await dismissAndDiscard(signatureDialog);
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
    await dismissAndDiscard(signatureDialog);

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

    // A timed rule is configured within Rules and overrides any regular rule while active.
    await page.getByRole("button", { name: "Rules", exact: true }).click();
    const timedRulesPanel = page.locator('[x-data="ruleManager"]');
    await timedRulesPanel.getByRole("button", { name: "+ Add Rule", exact: true }).click();
    const timedRuleDialog = timedRulesPanel.locator('[x-show="editing"][role="dialog"]');
    await assertFloatingDialog(timedRuleDialog, "timed rule editor");
    await timedRuleDialog.getByLabel("Name").fill("Browser Smoke Schedule");
    await timedRuleDialog.locator('select[x-model="form.signature_id"]').selectOption({ label: "Browser Smoke Signature" });
    await timedRuleDialog.locator('input[x-model="form.time_limited"]').check();
    const toLocalTimeValue = date => {
      const shifted = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
      return shifted.toISOString().slice(0, 16);
    };
    const scheduleNow = new Date();
    await timedRuleDialog.getByLabel("Start").fill(toLocalTimeValue(new Date(scheduleNow.getTime() - 2 * 60000)));
    await timedRuleDialog.getByLabel("End").fill(toLocalTimeValue(new Date(scheduleNow.getTime() - 3600000)));
    await timedRuleDialog.getByLabel("Priority").fill("10");
    await timedRuleDialog.getByRole("button", { name: "Save Rule" }).click();
    assert.equal(await timedRuleDialog.isVisible(), true, "invalid time ordering must leave rule editor open");
    await timedRuleDialog.getByLabel("End").fill(toLocalTimeValue(new Date(scheduleNow.getTime() + 3600000)));
    await timedRuleDialog.getByRole("button", { name: "Save Rule" }).click();
    let timedRuleRow = timedRulesPanel.locator("tbody tr").filter({ hasText: "Browser Smoke Schedule" });
    await timedRuleRow.waitFor();
    let timedRules = await (await page.request.get(baseURL + "/api/admin/rules")).json();
    let timedRule = timedRules.find(item => item.name === "Browser Smoke Schedule");
    assert.ok(timedRule, "a time-limited rule must persist as a regular rule");
    assert.equal(timedRule.priority, 10);
    assert.equal(timedRule.conditions.filter(item => item.kind === "time_window").length, 1);
    const oldWindow = timedRule.conditions.find(item => item.kind === "time_window");
    await timedRuleRow.getByRole("button", { name: "Edit" }).click();
    await timedRuleDialog.getByLabel("Priority").fill("42");
    await timedRuleDialog.getByRole("button", { name: "Save Rule" }).click();
    await page.waitForFunction(() => {
      const row = [...document.querySelectorAll('[x-data="ruleManager"] tbody tr')]
        .find(item => item.textContent.includes("Browser Smoke Schedule"));
      return row && row.children[1].textContent.trim() === "42";
    });
    timedRules = await (await page.request.get(baseURL + "/api/admin/rules")).json();
    timedRule = timedRules.find(item => item.name === "Browser Smoke Schedule");
    assert.equal(timedRule.priority, 42);
    assert.deepEqual(timedRule.conditions.find(item => item.kind === "time_window"), oldWindow);
    await page.reload();
    await page.getByRole("button", { name: "Rules", exact: true }).click();
    timedRuleRow = timedRulesPanel.locator("tbody tr").filter({ hasText: "Browser Smoke Schedule" });
    await timedRuleRow.waitFor();
    await timedRuleRow.getByRole("button", { name: "Delete" }).click();
    await page.locator('[x-data="confirmModal"]').getByRole("button", { name: "Confirm", exact: true }).click();
    await timedRuleRow.waitFor({ state: "detached" });
    timedRules = await (await page.request.get(baseURL + "/api/admin/rules")).json();
    assert.ok(!timedRules.some(item => item.name === "Browser Smoke Schedule"));

    // Existing schedules must remain visible and convert without creating duplicates.
    const legacyPayload = { name: "Legacy Browser Schedule", target_type: "SIGNATURE",
      signature_id: (await (await page.request.get(baseURL + "/api/admin/signatures")).json())
        .find(item => item.name === "Browser Smoke Signature").id,
      domain_id: null,
      starts_at: new Date(scheduleNow.getTime() - 120000).toISOString(),
      ends_at: new Date(scheduleNow.getTime() + 3600000).toISOString(),
      priority: 17, is_enabled: true };
    const legacyCreate = await page.evaluate(async payload => {
      const response = await fetch("/api/admin/schedules", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      return { status: response.status, body: await response.text() };
    }, legacyPayload);
    assert.equal(legacyCreate.status, 201, legacyCreate.body);
    await page.reload();
    await page.getByRole("button", { name: "Rules", exact: true }).click();
    const legacyRow = timedRulesPanel.locator("tbody tr").filter({ hasText: "Legacy Browser Schedule" });
    await legacyRow.waitFor();
    await legacyRow.getByRole("button", { name: "Edit" }).click();
    const convertedResponse = page.waitForResponse(response =>
      response.request().method() === "POST" && /\/api\/admin\/schedules\/[^/]+\/convert-rule$/.test(new URL(response.url()).pathname));
    await timedRuleDialog.getByRole("button", { name: "Save Rule" }).click();
    assert.equal((await convertedResponse).status(), 201, "legacy conversion must succeed before persistence checks");
    await timedRuleDialog.waitFor({ state: "hidden" });
    await legacyRow.waitFor();
    assert.equal((await (await page.request.get(baseURL + "/api/admin/schedules")).json())
      .some(item => item.name === "Legacy Browser Schedule"), false, "legacy schedule must be replaced atomically");
    assert.equal((await (await page.request.get(baseURL + "/api/admin/rules")).json())
      .filter(item => item.name === "Legacy Browser Schedule").length, 1);
    await legacyRow.getByRole("button", { name: "Delete" }).click();
    await page.locator('[x-data="confirmModal"]').getByRole("button", { name: "Confirm", exact: true }).click();
    await legacyRow.waitFor({ state: "detached" });

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
      "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/iZk9HQAAAABJRU5ErkJggg==",
      "base64"
    );
    const brandingLogoFile = { name: "logo.png", mimeType: "image/png", buffer: brandingLogoBytes };
    const brandingDarkLogoBytes = Buffer.from(
      "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYPj/HwADAgH/5ncLrgAAAABJRU5ErkJggg==",
      "base64"
    );
    const icoDirectory = Buffer.alloc(16);
    icoDirectory[0] = 1;
    icoDirectory[1] = 1;
    icoDirectory.writeUInt16LE(1, 4);
    icoDirectory.writeUInt16LE(32, 6);
    icoDirectory.writeUInt32LE(brandingLogoBytes.length, 8);
    icoDirectory.writeUInt32LE(22, 12);
    const faviconBytes = Buffer.concat([Buffer.from([0, 0, 1, 0, 1, 0]), icoDirectory, brandingLogoBytes]);
    await brandingFiles.nth(0).setInputFiles(brandingLogoFile);
    await brandingFiles.nth(1).setInputFiles({ name: "dark-logo.png", mimeType: "image/png", buffer: brandingDarkLogoBytes });
    await brandingFiles.nth(2).setInputFiles({ name: "favicon.ico", mimeType: "application/octet-stream", buffer: faviconBytes });
    await page.waitForFunction(() => {
      const logo = document.querySelector('img[alt="Brand logo preview"]');
      const darkLogo = document.querySelector('img[alt="Dark mode logo preview"]');
      const favicon = document.querySelector('img[alt="Favicon preview"]');
      return logo && darkLogo && favicon
        && logo.getAttribute("src").startsWith("data:image/png;base64,")
        && darkLogo.getAttribute("src").startsWith("data:image/png;base64,")
        && favicon.getAttribute("src").startsWith("data:image/x-icon;base64,");
    });
    await assertCompactBrandingPreviews(brandingPanel, "uploaded branding previews");
    const brandingSave = brandingPanel.getByRole("button", { name: "Save Changes", exact: true });
    await Promise.all([
      page.waitForNavigation({ waitUntil: "domcontentloaded" }),
      brandingSave.click(),
    ]);
    await page.locator(".app-brand").filter({ hasText: "OpenSigWeave Browser Smoke" }).waitFor();
    const customBranding = await (await page.request.get(baseURL + "/api/admin/branding")).json();
    assert.equal(customBranding.app_name, "OpenSigWeave Browser Smoke");
    assert.equal(customBranding.app_color, "#f97316");
    assert.equal(customBranding.has_custom_logo, true, "branding update must persist the regular logo");
    assert.equal(customBranding.has_custom_dark_logo, true, "branding update must persist the dark mode logo");
    assert.equal(customBranding.has_custom_favicon, true, "branding update must persist the favicon");
    checkpoint("branding: custom assets saved");

    const customLogoResponse = await page.request.get(baseURL + "/branding/logo.png");
    const customDarkLogoResponse = await page.request.get(baseURL + "/branding/logo-dark.png");
    const customFaviconResponse = await page.request.get(baseURL + "/branding/favicon.ico");
    assert.equal(customLogoResponse.status(), 200);
    assert.equal(customDarkLogoResponse.status(), 200);
    assert.equal(customFaviconResponse.status(), 200);
    assert.deepEqual(await customLogoResponse.body(), brandingLogoBytes, "uploaded logo must be served verbatim");
    assert.deepEqual(await customDarkLogoResponse.body(), brandingDarkLogoBytes, "dark mode logo must be served separately");
    assert.deepEqual(await customFaviconResponse.body(), faviconBytes, "uploaded ICO favicon must be served verbatim");
    assert.match(customLogoResponse.headers()["cache-control"], /no-cache/, "branding logo must revalidate after updates");
    assert.match(customFaviconResponse.headers()["cache-control"], /no-cache/, "branding favicon must revalidate after updates");
    assert.match(customFaviconResponse.headers()["content-type"], /^image\/x-icon/);
    // A new src attribute does not mean that Chromium has decoded the image.
    // Wait for both the correct theme asset and natural dimensions before asserting.
    const awaitNavigationLogo = async expected => {
      await page.waitForFunction(src => {
        const img = document.querySelector('nav img[data-osw-brand-logo]');
        return img?.getAttribute('src') === src && img.complete && img.naturalWidth > 0;
      }, expected);
    };
    await awaitNavigationLogo('/branding/logo-dark.png');
    await page.locator('#theme-toggle').click();
    await awaitNavigationLogo('/branding/logo.png');
    assert.equal(await page.evaluate(() => document.documentElement.dataset.theme), 'light');
    await page.locator('#theme-toggle').click();
    await awaitNavigationLogo('/branding/logo-dark.png');
    checkpoint("branding: navigation assets decoded in both themes");
    const appliedColor = await page.locator(".app-brand").first().evaluate(el => getComputedStyle(el).color);
    assert.equal(appliedColor, "rgb(249, 115, 22)", "saved accent color must affect the actual UI");
    const loginBranding = await page.request.get(baseURL + "/login");
    assert.match(await loginBranding.text(), /OpenSigWeave Browser Smoke/);
    assert.match(await loginBranding.text(), /data-osw-brand-logo/, "login page must support mode-aware logos");

    for (const invalid of [
      { name: "truncated PNG", logo_data_url: "data:image/png;base64," + Buffer.from([137,80,78,71,13,10,26,10,1]).toString("base64") },
      { name: "SVG", logo_data_url: "data:image/svg+xml;base64," + Buffer.from("<svg></svg>").toString("base64") },
      { name: "dark SVG", dark_logo_data_url: "data:image/svg+xml;base64," + Buffer.from("<svg></svg>").toString("base64") },
      { name: "dark truncated PNG", dark_logo_data_url: "data:image/png;base64," + Buffer.from([137,80,78,71,13,10,26,10,1]).toString("base64") },
      { name: "oversized PNG", logo_data_url: "data:image/png;base64," + Buffer.alloc(512 * 1024 + 1).toString("base64") },
      { name: "corrupt ICO", favicon_data_url: "data:image/x-icon;base64," + Buffer.from([0,0,1,0,1,0]).toString("base64") },
    ]) {
      const responseStatus = await page.evaluate(async change => {
        const response = await fetch("/api/admin/branding", {
          method: "PUT", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ app_name: "OpenSigWeave Browser Smoke", app_color: "#f97316", ...change })
        });
        return response.status;
      }, Object.fromEntries(Object.entries(invalid).filter(([key]) => key !== "name")));
      assert.equal(responseStatus, 422, invalid.name + " must be rejected server-side");
    }
    const unchangedBrand = await (await page.request.get(baseURL + "/api/admin/branding")).json();
    assert.equal(unchangedBrand.has_custom_logo, true, "failed uploads must not replace the existing logo");
    assert.equal(unchangedBrand.has_custom_dark_logo, true, "failed uploads must not replace the dark mode logo");
    assert.equal(unchangedBrand.has_custom_favicon, true, "failed uploads must not replace the existing favicon");

    await page.getByRole("button", { name: "Admin", exact: true }).click();
    await page.getByRole("button", { name: "Branding", exact: true }).click();
    brandingPanel = page.locator('[x-data="brandingManager"]');
    const resetButton = brandingPanel.getByRole("button", { name: "Reset to Default", exact: true });
    const resetDialog = page.locator('[x-data="confirmModal"] [role="alertdialog"]');
    await resetButton.click();
    await resetDialog.waitFor({ state: "visible" });
    await assertModalLayout(page, resetDialog, "branding reset confirmation");
    await resetDialog.getByRole("button", { name: "Cancel", exact: true }).click();
    await resetDialog.waitFor({ state: "hidden" });
    assert.equal((await (await page.request.get(baseURL + "/api/admin/branding")).json()).has_custom_dark_logo, true,
      "cancel must not modify persisted branding");

    await page.evaluate(() => { window.__brandingResetDocumentMarker = true; });
    const themeBeforeReset = await page.evaluate(() => ({
      selected: document.documentElement.dataset.theme,
      stored: localStorage.getItem("opensigweave.theme.v1")
    }));
    await resetButton.click();
    await resetDialog.waitFor({ state: "visible" });
    assert.match(await resetDialog.locator("#osw-confirm-description").textContent(), /light and dark mode logos/);
    await resetDialog.getByRole("button", { name: "Confirm", exact: true }).click();
    await page.waitForFunction(() => document.body.dataset.appColor === "#ff7043"
      && document.querySelector(".app-brand")?.textContent === "OpenSigWeave"
      && document.querySelector('nav img[data-osw-brand-logo]')?.getAttribute("src")?.includes("?v="));
    assert.equal(await page.evaluate(() => window.__brandingResetDocumentMarker), true,
      "reset must update the existing page without navigation");
    assert.deepEqual(await page.evaluate(() => ({
      selected: document.documentElement.dataset.theme,
      stored: localStorage.getItem("opensigweave.theme.v1")
    })), themeBeforeReset, "reset must preserve the selected theme and its storage");
    assert.equal(await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--osw-accent").trim()), "#ff7043");
    assert.match(await page.locator('link[rel="icon"]').getAttribute("href"), /\/branding\/favicon\?v=/);
    await page.waitForFunction(() => {
      const logo = document.querySelector('nav img[data-osw-brand-logo]');
      return logo?.complete && logo.naturalWidth > 0;
    });
    await page.locator("#theme-toggle").click();
    await page.waitForFunction(() => document.documentElement.dataset.theme === "light"
      && document.querySelector('nav img[data-osw-brand-logo]')?.getAttribute("src")?.startsWith("/branding/logo.png?v="));
    await page.locator("#theme-toggle").click();
    await page.waitForFunction(() => document.documentElement.dataset.theme === "dark"
      && document.querySelector('nav img[data-osw-brand-logo]')?.getAttribute("src")?.startsWith("/branding/logo-dark.png?v="));
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.locator(".app-brand").filter({ hasText: "OpenSigWeave" }).waitFor();
    const repeatResetStatus = await page.evaluate(async () => (await fetch("/api/admin/branding/reset", { method: "POST" })).status);
    assert.equal(repeatResetStatus, 200, "full reset must be idempotent");
    const restoredBranding = await (await page.request.get(baseURL + "/api/admin/branding")).json();
    assert.equal(restoredBranding.app_name, "OpenSigWeave");
    assert.equal(restoredBranding.app_color, "#ff7043");
    assert.equal(restoredBranding.has_custom_logo, false);
    assert.equal(restoredBranding.has_custom_dark_logo, false);
    assert.equal(restoredBranding.has_custom_favicon, false);
    // The authenticated admin is redirected from /login to the portal.
    // Verify the default login branding without sharing the administrator session.
    const anonymousLoginContext = await browser.newContext();
    try {
      const loginAfterReset = await anonymousLoginContext.request.get(baseURL + "/login");
      assert.equal(loginAfterReset.status(), 200, "anonymous users must reach the login form");
      assert.match(await loginAfterReset.text(), /Sign in · OpenSigWeave/);
    } finally {
      await anonymousLoginContext.close();
    }
    const bundledLogo = await page.request.get(baseURL + "/branding/logo.png");
    assert.equal(bundledLogo.status(), 200, "built-in logo must remain available after restoring defaults");
    assert.deepEqual(await (await page.request.get(baseURL + "/branding/logo-dark.png")).body(), await bundledLogo.body(), "dark mode must fall back to standard logo");
    assert.notDeepEqual(await bundledLogo.body(), brandingLogoBytes, "built-in image must replace the custom logo");
    const restoredFavicon = await page.request.get(baseURL + "/branding/favicon");
    assert.deepEqual(await restoredFavicon.body(), await bundledLogo.body(), "bundled logo must also be the favicon fallback");


    checkpoint("branding lifecycle: complete");

    await page.getByRole("button", { name: "Admin", exact: true }).click();
    const groupPanel = page.locator('[x-data="signatureGroupManager"]');
    await page.getByRole("button", { name: "Signature Groups", exact: true }).click();
    await groupPanel.getByRole("button", { name: "+ Add Group" }).click();
    const groupDialog = groupPanel.locator('[x-show="editing"][role="dialog"]');
    await assertFloatingDialog(groupDialog, "signature group editor");
    await groupPanel.getByLabel("Name").fill("Browser Smoke Group");
    await groupPanel.getByRole("button", { name: "Save Group", exact: true }).click();
    const groupCard = groupPanel.locator(".osw-table-scroll tbody tr").filter({ has: page.getByRole("heading", { name: "Browser Smoke Group", exact: true }) });
    await groupCard.getByRole("heading", { name: "Browser Smoke Group", exact: true }).waitFor();
    await groupPanel.getByRole("button", { name: "+ Add Signature to Group", exact: true }).click();
    const memberDialog = groupPanel.locator('div[x-show="memberEditor"]');
    await assertFloatingDialog(memberDialog, "group membership editor");
    await memberDialog.locator('select[x-model="memberForm.group_id"]').selectOption({ label: "Browser Smoke Group" });
    await memberDialog.locator('select[x-model="memberForm.signature_id"]').selectOption({ label: "Browser Smoke Signature" });
    await memberDialog.getByRole("button", { name: "Add Signature", exact: true }).click();
    await groupCard.locator('span[x-text="signatureName(member.signature_id)"]').filter({ hasText: "Browser Smoke Signature" }).waitFor();

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
    await rulesPanel.getByRole("button", { name: "+ Add Condition" }).click();
    const conditionRows = rulesPanel.locator("div.grid.grid-cols-1.md\\:grid-cols-12");
    const kindSelect = conditionRows.last().locator('select[x-model="condition.kind"]');
    const attributeKey = conditionRows.last().locator('input[x-model="condition.key"]');
    await kindSelect.selectOption("attribute_equals");
    await attributeKey.waitFor({ state: "visible" });
    await attributeKey.fill("department");
    await kindSelect.selectOption("sender_not_equals");
    await attributeKey.waitFor({ state: "hidden" });
    assert.equal(await attributeKey.inputValue(), "", "irrelevant attribute key must be cleared");
    await conditionRows.last().locator('input[placeholder="Value"]').fill("example.test");
    await rulesPanel.getByRole("button", { name: "Save Rule", exact: true }).click();
    await page.waitForFunction(() => {
      const row = [...document.querySelectorAll('[x-data="ruleManager"] tbody tr')]
        .find(item => item.textContent.includes("Browser Smoke Rule"));
      return row && row.children[1].textContent.trim() === "25"
        && row.children[3].textContent.includes("Sender not equal to")
        && row.children[3].textContent.includes("example.test");
    });

    await createdRuleRow.getByRole("button", { name: "Edit", exact: true }).click();
    assert.equal(await conditionRows.last().locator('select[x-model="condition.kind"]').inputValue(), "sender_not_equals",
      "negative comparison must remain selected after saving and reopening a rule");
    await dismissAndDiscard(ruleDialog);

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
    const updatedGroupCard = groupPanel.locator(".osw-table-scroll tbody tr")
      .filter({ has: page.getByRole("heading", { name: "Browser Smoke Group Updated", exact: true }) });
    await updatedGroupCard.getByText("Browser Smoke Group Updated", { exact: true }).waitFor();
    const updatedMemberLabel = updatedGroupCard.locator('span[x-text="signatureName(member.signature_id)"]').filter({ hasText: "Browser Smoke Signature" });
    await updatedMemberLabel.waitFor();

    // Update a group member's weight and verify the UI remains operational.
    const memberWeight = updatedGroupCard.locator('input[type="number"]').first();
    await memberWeight.fill("4");
    await memberWeight.dispatchEvent("change");

    // Exercise explicit membership removal independently from group deletion.
    const updatedMemberRow = updatedMemberLabel.locator("xpath=..");
    await updatedMemberRow.getByRole("button", { name: "Remove", exact: true }).click();
    await page.locator('[x-data="confirmModal"]').getByRole("button", { name: "Confirm", exact: true }).click();
    await updatedMemberLabel.waitFor({ state: "detached" });

    // Update the signature metadata without creating an extra HTML version.
    await page.getByRole("button", { name: "Signatures", exact: true }).click();
    const updatedSignatureRow = signaturePanel.locator("tbody tr").filter({ hasText: "Browser Smoke Signature" });
    await updatedSignatureRow.getByRole("button", { name: "Edit", exact: true }).click();
    await signaturePanel.getByLabel("Name").fill("Browser Smoke Signature Updated");
    await signaturePanel.getByRole("button", { name: "Save Signature", exact: true }).click();
    await signaturePanel.locator("tbody tr").filter({ hasText: "Browser Smoke Signature Updated" })
      .getByText("Browser Smoke Signature Updated", { exact: true }).waitFor();

    checkpoint("group/signature updates: complete");
    // Remove the rule and group again so this test also covers destructive UI paths.
    await page.getByRole("button", { name: "Rules", exact: true }).click();
    const updatedRuleRow = rulesPanel.locator("tbody tr").filter({ hasText: "Browser Smoke Rule" });
    await updatedRuleRow.getByRole("button", { name: "Delete", exact: true }).click();
    await page.locator('[x-data="confirmModal"]').getByRole("button", { name: "Confirm", exact: true }).click();
    await updatedRuleRow.waitFor({ state: "detached" });

    await page.getByRole("button", { name: "Signature Groups", exact: true }).click();
    await updatedGroupCard.getByRole("button", { name: "Delete", exact: true }).click();
    await page.locator('[x-data="confirmModal"]').getByRole("button", { name: "Confirm", exact: true }).click();
    await groupPanel.getByRole("heading", { name: "Browser Smoke Group Updated", exact: true }).waitFor({ state: "detached" });

    await page.getByRole("button", { name: "Signatures", exact: true }).click();
    await signaturePanel.locator("tbody tr").filter({ hasText: "Browser Smoke Signature Updated" })
      .getByText("Browser Smoke Signature Updated", { exact: true }).waitFor();

    checkpoint("rule/group deletion: complete");

    // Create a login-capable OIDC provider through the real UI and verify that its
    // non-secret SSO settings survive a reload. Secrets intentionally remain hidden.
    await page.getByRole("button", { name: "Identity Providers", exact: true }).click();
    const ssoProviderPanel = page.locator('[x-data="identityProviderManager"]');
    await ssoProviderPanel.getByRole("button", { name: "+ Add Identity Provider" }).click();
    const ssoProviderDialog = ssoProviderPanel.locator('[x-show="editing"][role="dialog"]');
    await ssoProviderDialog.getByLabel("Provider ID").fill("browser-smoke-login-provider");
    await ssoProviderDialog.getByLabel("Display Name").fill("Browser Smoke Login Provider");
    await ssoProviderDialog.getByLabel("Provider Type").selectOption("oidc");
    const oidcFields = ssoProviderDialog.locator('div[x-show="form.provider_type === \'oidc\'"]');
    await oidcFields.getByLabel("OIDC Discovery URL").fill("https://idp.example.org/.well-known/openid-configuration");
    await oidcFields.getByLabel("OIDC Client ID").fill("browser-smoke-client");
    await oidcFields.getByLabel("OIDC Client Secret").fill("browser-smoke-secret");
    await oidcFields.getByLabel("OIDC Scopes").fill("openid email profile groups");
    await oidcFields.getByLabel("OIDC Callback / Redirect URI").fill("https://signatures.example.org/auth/browser-smoke-login-provider");
    const createSsoProvider = page.waitForResponse(response =>
      response.request().method() === "POST"
      && response.url().endsWith("/api/admin/identity-providers")
    );
    // Do not hide a 4xx/5xx response behind the generic response timeout.
    // Avoid logging credentials or the raw POST body when diagnosing a failure.
    await ssoProviderDialog.getByRole("button", { name: "Save", exact: true }).click();
    let createSsoResponse;
    try {
      createSsoResponse = await createSsoProvider;
    } catch (error) {
      const diagnostic = await ssoProviderDialog.evaluate(el => {
        const state = window.Alpine?.$data(el);
        return { id: state?.form?.id, display_name: state?.form?.display_name,
          provider_type: state?.form?.provider_type,
          oidc_field_count: el.querySelectorAll('div[x-show="form.provider_type === \'oidc\'"] input').length };
      });
      throw new Error("No identity-provider POST after Save. Non-secret form state: "
        + JSON.stringify(diagnostic), { cause: error });
    }
    assert.equal(createSsoResponse.status(), 201,
      "Identity-provider POST failed with HTTP " + createSsoResponse.status());
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
    assert.equal(await reloadedOidcFields.getByLabel("OIDC Discovery URL").inputValue(),
      "https://idp.example.org/.well-known/openid-configuration");
    assert.equal(await reloadedOidcFields.getByLabel("OIDC Client ID").inputValue(), "browser-smoke-client");
    assert.equal(await reloadedOidcFields.getByLabel("OIDC Callback / Redirect URI").inputValue(),
      "https://signatures.example.org/auth/browser-smoke-login-provider");
    assert.equal(await reloadedOidcFields.getByLabel("OIDC Client Secret").inputValue(), "",
      "saved OIDC secrets must remain write-only in the browser");

    // Updating an existing OIDC provider intentionally leaves the write-only
    // client-secret input blank. The existing credential must be retained before
    // validation while changed non-secret settings persist across another reload.
    await reloadedOidcFields.getByLabel("OIDC Scopes").fill("openid email profile groups offline_access");
    // Attach the response waiter and click together. If the click fails,
    // no orphaned waitForRequest/Response promise can hide the real error.
    const [scopeResponse] = await Promise.all([
      page.waitForResponse(response =>
        response.request().method() === "PUT"
        && response.url().endsWith("/api/admin/identity-providers/browser-smoke-login-provider")
      ),
      reloadedSsoDialog.getByRole("button", { name: "Save", exact: true }).click(),
    ]);
    const scopePayload = JSON.parse(scopeResponse.request().postData() || "{}");
    assert.equal(scopePayload.settings?.OIDC_SCOPES, "openid email profile groups offline_access",
      "OIDC form must send the current scope value instead of an earlier x-model snapshot");
    // Do not read response.text() on a successful Playwright response:
    // the browser's body-reading promise may outlive the request and stall CI.
    assert.equal(scopeResponse.status(), 200,
      "OIDC provider update must succeed (HTTP " + scopeResponse.status() + ")");
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
    assert.equal(await updatedOidcFields.getByLabel("OIDC Scopes").inputValue(),
      "openid email profile groups offline_access");
    assert.equal(await updatedOidcFields.getByLabel("OIDC Client Secret").inputValue(), "",
      "OIDC client secret must remain write-only after update and reload");
    await updatedSsoDialog.press("Escape");
    await updatedSsoDialog.waitFor({ state: "hidden" });

    // Exercise the complete domain lifecycle through the administrator UI.
    await page.getByRole("button", { name: "Domains", exact: true }).click();
    const domainPanel = page.locator('[x-data="domainManager"]');
    await domainPanel.getByRole("heading", { name: "Managed Domains" }).waitFor({ state: "visible" });
    await domainPanel.getByRole("button", { name: "+ Add Domain" }).click();
    const addDomainDialog = page.locator('[x-data="addDomainModal"]');
    await addDomainDialog.getByRole("textbox", { name: "Mail domain" }).fill("browser-smoke-domain.test");
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
    assert.equal(await domainRow.locator("select").count(), 0,
      "provider assignments must not be editable in the domain overview");
    await domainRow.getByRole("button", { name: "Manage Domain", exact: true }).click();
    const assignmentDialog = page.locator('[x-data="domainSettingsModal"]');
    const profileProviderSelect = assignmentDialog.getByLabel("Identity Provider", { exact: true });
    const loginProviderSelect = assignmentDialog.getByLabel("Authentication / SSO Provider", { exact: true });
    await profileProviderSelect.selectOption("browser-smoke-profile-provider");
    await loginProviderSelect.selectOption("browser-smoke-login-provider");
    const saveProviderSettingsButton = assignmentDialog.getByRole("button", { name: "Save Settings", exact: true });
    const inspectProviderState = () => page.evaluate(() => {
      const modal = document.querySelector('[x-data="domainSettingsModal"]');
      const data = window.Alpine?.$data(modal);
      const selects = [...modal.querySelectorAll('select')].slice(0, 2);
      return {
        show: data?.show, saving: data?.saving, providerLoading: data?.providerLoading,
        providerError: data?.providerError, identityProviderId: data?.identityProviderId,
        authenticationProviderId: data?.authenticationProviderId,
        selectedProfileUnavailable: data?.selectedProfileUnavailable(),
        selectedLoginUnavailable: data?.selectedLoginUnavailable(),
        saveDisabled: [...modal.querySelectorAll('button')].find(b => b.textContent.trim() === 'Save Settings')?.disabled,
        selectedOptions: selects.map(el => ({ value: el.value, disabled: el.disabled })),
        providers: data?.providers.map(item => ({
          id: item.id, type: item.provider_type, enabled: item.is_enabled, configured: item.configured
        }))
      };
    });
    checkpoint("domain provider assignment pre-save: " + JSON.stringify(await inspectProviderState()));
    let providerAssignment;
    try {
      [, providerAssignment] = await Promise.all([
        saveProviderSettingsButton.click({ timeout: 8000 }),
        page.waitForResponse(response =>
          response.request().method() === "PUT"
          && response.url().endsWith("/domains/" + createdDomain.id),
          { timeout: 9000 })
      ]);
    } catch (error) {
      throw new Error("Domain provider Save Settings failed: "
        + JSON.stringify(await inspectProviderState()), { cause: error });
    }
    assert.equal(providerAssignment.status(), 200, "domain provider settings must save together");

    domainRecords = await (await page.request.get(baseURL + "/domains/")).json();
    createdDomain = domainRecords.find(item => item.domain_name === "browser-smoke-domain.test");
    assert.equal(createdDomain.identity_provider_id, "browser-smoke-profile-provider",
      "profile assignment must be saved from Manage Domain");
    assert.equal(createdDomain.authentication_provider_id, "browser-smoke-login-provider",
      "SSO login assignment must be saved from Manage Domain");

    // Both provider labels remain visible, but editable only inside Domain Management.
    const providerRecords = await (await page.request.get(baseURL + "/api/admin/identity-providers")).json();
    const profileRecord = providerRecords.find(item => item.id === "browser-smoke-profile-provider");
    const loginRecord = providerRecords.find(item => item.id === "browser-smoke-login-provider");
    assert.ok(profileRecord && loginRecord, "browser smoke providers must be configured");
    assert.match(await domainRow.locator("td").nth(2).textContent(), new RegExp(profileRecord.display_name));
    assert.match(await domainRow.locator("td").nth(3).textContent(), new RegExp(loginRecord.display_name));

    await page.reload();
    await page.getByRole("heading", { name: "Managed Domains" }).waitFor({ state: "visible" });
    domainRow = page.locator('[x-data="domainManager"] tbody tr').filter({ hasText: "browser-smoke-domain.test" });
    await domainRow.waitFor();
    assert.equal(await domainRow.locator("select").count(), 0);
    await domainRow.getByRole("button", { name: "Manage Domain", exact: true }).click();
    const restoredDialog = page.locator('[x-data="domainSettingsModal"]');
    assert.equal(await restoredDialog.getByLabel("Identity Provider", { exact: true }).inputValue(),
      "browser-smoke-profile-provider",
      "profile provider must remain selected after reload");
    assert.equal(await restoredDialog.getByLabel("Authentication / SSO Provider", { exact: true }).inputValue(),
      "browser-smoke-login-provider",
      "login provider must remain selected after reload");
    await restoredDialog.getByRole("button", { name: "Close", exact: true }).click();

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
    await domainRow.getByRole("button", { name: "Manage Domain", exact: true }).click();
    const domainSettings = page.locator('[x-data="domainSettingsModal"]');
    // The retired domain master editor is gone. Policy changes must preserve old data.
    assert.equal(await domainSettings.locator("#domain-rich-editor").count(), 0);
    const historicalDomainTemplate = createdDomain.template_html;
    const saveDomainPolicy = page.waitForResponse(response =>
      response.request().method() === "PUT"
      && response.url().endsWith("/domains/" + createdDomain.id)
      && response.status() === 200
    );
    await domainSettings.getByRole("button", { name: "Save Settings", exact: true }).click();
    await saveDomainPolicy;
    domainRecords = await (await page.request.get(baseURL + "/domains/")).json();
    createdDomain = domainRecords.find(item => item.domain_name === "browser-smoke-domain.test");
    assert.equal(createdDomain.template_html, historicalDomainTemplate,
      "saving domain policy must not erase existing retired master-template data");

    // Exercise the administrator's per-user override editor and save workflow.
    await domainRow.getByRole("button", { name: "Manage Domain", exact: true }).click();
    await domainSettings.getByRole("button", { name: "Users & Overrides", exact: true }).click();
    await domainSettings.getByRole("heading", { name: "Domain users" }).waitFor({ state: "visible" });
    const usersApi = await page.request.get(baseURL + "/api/admin/domains/" + createdDomain.id + "/users");
    assert.equal(usersApi.status(), 200, "authorized administrator can inspect domain users");
    assert.equal((await usersApi.json()).items.some(u => u.email === "nonexistent@browser-smoke-domain.test"), false,
      "domain users must not be invented");
    await domainSettings.getByPlaceholder("user@domain.com").fill("editor-smoke@browser-smoke-domain.test");
    await domainSettings.getByRole("button", { name: "+ Add or Edit User" }).click();
    const adminEditor = domainSettings.locator("#admin-rich-editor .ck-editor__editable");
    await adminEditor.waitFor({ state: "visible" });
    const adminFontFamily = domainSettings.locator("#admin-rich-editor .osw-signature-family-input input");
    const adminFontSize = domainSettings.locator("#admin-rich-editor .osw-signature-size-input input");
    await adminFontFamily.waitFor({ state: "visible" });
    await adminFontSize.waitFor({ state: "visible" });
    // Font choices are rendered lazily when the shared combobox opens, not
    // when CKEditor mounts. Check the real user interaction in this third
    // editor instance rather than expecting a hidden pre-rendered list.
    await adminFontFamily.click();
    const adminFontList = domainSettings.locator("#admin-rich-editor .osw-font-listbox");
    await adminFontList.waitFor({ state: "visible" });
    assert.equal(await adminFontList.getByRole("option", { name: "Georgia", exact: true }).count(), 1,
      "admin CKEditor must share the searchable family list");
    await adminFontFamily.fill("geo");
    assert.deepEqual(await adminFontList.locator('[role="option"]').allTextContents(), ["Georgia"]);
    await adminFontFamily.press("Escape");
    assert.equal(await adminFontList.isVisible(), false,
      "Escape must close the font menu without closing domain settings");
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
    await domainSettings.getByRole("button", { name: "Save Changes", exact: true }).click();
    await saveAdminOverride;
    await domainSettings.getByText("editor-smoke@browser-smoke-domain.test", { exact: true }).first().waitFor();
    const refreshedUsers = await (await page.request.get(baseURL + "/api/admin/domains/" + createdDomain.id + "/users")).json();
    assert.ok(refreshedUsers.items.some(u => u.email === "editor-smoke@browser-smoke-domain.test" && u.override_status === "Configured"),
      "user directory must show a newly created override");
    const adminOverrides = await (await page.request.get(baseURL + "/api/admin/overrides/browser-smoke-domain.test")).json();
    const editorOverride = adminOverrides.find(item => item.user_email === "editor-smoke@browser-smoke-domain.test");
    assert.ok(editorOverride, "administrator override must be persisted and returned by the domain API");
    assert.match(editorOverride.html_content, /<strong>Administrator user override<\/strong>/,
      "administrator override API must return the formatted HTML");
    await domainSettings.getByRole("button", { name: "Close", exact: true }).click();
    await domainRow.getByRole("button", { name: "Manage Domain", exact: true }).click();
    const allowOverridesLabel = domainSettings.locator("label").filter({ hasText: "Allow Personal Signature Overrides" });
    await allowOverridesLabel.click();
    const replyInjectionLabel = domainSettings.locator("label").filter({ hasText: "Apply Signatures to Replies (Experimental)" });
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
    assert.equal(await domainRow.locator("select").count(), 0, "provider columns must stay read-only");
    await domainRow.getByRole("button", { name: "Manage Domain", exact: true }).click();
    const reloadedSettings = page.locator('[x-data="domainSettingsModal"]');
    assert.equal(await reloadedSettings.getByLabel("Identity Provider", { exact: true }).inputValue(),
      "browser-smoke-profile-provider");
    assert.equal(await reloadedSettings.getByLabel("Authentication / SSO Provider", { exact: true }).inputValue(),
      "browser-smoke-login-provider", "login provider must remain selected after page reload");
    await reloadedSettings.locator("label").filter({ hasText: "Allow Personal Signature Overrides" })
      .locator('input[type="checkbox"]').waitFor();
    assert.equal(await reloadedSettings.locator("label").filter({ hasText: "Allow Personal Signature Overrides" })
      .locator('input[type="checkbox"]').isChecked(), false);
    assert.equal(await reloadedSettings.locator("label").filter({ hasText: "Apply Signatures to Replies (Experimental)" })
      .locator('input[type="checkbox"]').isChecked(), true);
    // Native on/off switches retain accessible names, Space-key operation and Alpine bindings.
    const overridesSwitch = reloadedSettings.getByRole("switch", { name: "Allow Personal Signature Overrides" });
    assert.equal(await overridesSwitch.isChecked(), false);
    assert.equal(await overridesSwitch.evaluate(node => getComputedStyle(node).appearance), "none");
    await overridesSwitch.focus();
    await overridesSwitch.press("Space");
    assert.equal(await overridesSwitch.isChecked(), true);
    await overridesSwitch.press("Space");
    assert.equal(await overridesSwitch.isChecked(), false);
    // Keyboard changes correctly mark the dialog as dirty, even when reverted.
    // Confirm the discard instead of leaving a modal backdrop over later tests.
    await reloadedSettings.getByRole("button", { name: "Close", exact: true }).click();
    const discardDomainSettings = page.locator('[x-data="confirmModal"]');
    await discardDomainSettings.waitFor({ state: "visible" });
    await discardDomainSettings.getByRole("button", { name: "Confirm", exact: true }).click();
    await reloadedSettings.waitFor({ state: "hidden" });
    await discardDomainSettings.waitFor({ state: "hidden" });

    checkpoint("domain settings: complete");

    // Visual regression matrix: actual Alpine dialogs in two themes and two viewports.
    checkpoint("dialog visual matrix: desktop/mobile and dark/light");
    for (const [mode, width, height] of [
      ["dark", 1440, 900], ["light", 1440, 900],
      ["dark", 390, 844], ["light", 390, 844],
    ]) {
      const visualContext = await browser.newContext({ viewport: { width, height } });
      await visualContext.addCookies(await page.context().cookies());
      const visualPage = await visualContext.newPage();
      visualPage.setDefaultTimeout(15000);
      try {
        await visualPage.goto(baseURL + "/?view=admin&section=domains");
        await visualPage.evaluate(theme => {
          localStorage.setItem("opensigweave.theme.v1", theme);
          document.documentElement.dataset.theme = theme;
        }, mode);
        const prefix = width + "x" + height + "-" + mode + "-";
        const nav = async name => visualPage.getByRole("button", { name, exact: true }).click();
        const shell = sel => visualPage.locator(sel);
        const inspect = async (sel, name) => assertModalLayout(visualPage, shell(sel), prefix + name, true);
        for (const [tab, selector] of [
          ["Identity Providers", '[x-data="identityProviderManager"]'],
          ["Domains", '[x-data="domainManager"]'],
          ["Signatures", '[x-data="signatureManager"]'],
          ["Signature Groups", '[x-data="signatureGroupManager"]'],
          ["Rules", '[x-data="ruleManager"]'],
          ["Branding", '[x-data="brandingManager"]'],
          ["Backup", '[x-data="backupManager"]'],
          ["Import / Export", '[x-show="adminTab === \'import-export\'"]'],
          ["System", '[x-data="systemStatusManager"]'],
        ]) {
          await nav(tab);
          const section = shell(selector);
          await assertAdminSectionLayout(section, prefix + tab);
          if (tab === "Branding") await assertCompactBrandingPreviews(section, prefix + tab);
        }
        const close = async sel => {
          await shell(sel).locator("[data-osw-dialog-close]:visible").first().click();
          await shell(sel).waitFor({ state: "hidden" });
        };
        await nav("Identity Providers");
        await visualPage.getByRole("button", { name: "+ Add Identity Provider" }).click();
        const providerSelector = '[x-data="identityProviderManager"] [role="dialog"]';
        await inspect(providerSelector, "identity-provider");
        if (width === 1440 && mode === "dark") {
          await shell(providerSelector).getByLabel("Display Name").fill("Unsaved");
          await shell(providerSelector).getByRole("button", { name: "Cancel", exact: true }).click();
          const confirmation = visualPage.locator('[x-data="confirmModal"]');
          await confirmation.waitFor({ state: "visible" });
          await confirmation.getByRole("button", { name: "Cancel", exact: true }).click();
          assert.equal(await shell(providerSelector).isVisible(), true, "canceling the discard must preserve the form");
          await shell(providerSelector).getByRole("button", { name: "Cancel", exact: true }).click();
          await confirmation.getByRole("button", { name: "Confirm", exact: true }).click();
          await shell(providerSelector).waitFor({ state: "hidden" });
          await visualPage.getByRole("button", { name: "+ Add Identity Provider" }).click();
          assert.equal(await shell(providerSelector).getByLabel("Display Name").inputValue(), "", "reopening must reset draft");
        }
        await close(providerSelector);
        await nav("Signatures");
        await visualPage.getByRole("button", { name: "+ Add Signature" }).click();
        const signatureSelector = '[x-data="signatureManager"] [role="dialog"][aria-label="Signature editor"]';
        await inspect(signatureSelector, "signature-editor");
        const fontToolbar = shell(signatureSelector).locator("#signature-template-editor .ck-toolbar");
        const familyControl = fontToolbar.locator(".osw-signature-family-input input");
        const sizeControl = fontToolbar.locator(".osw-signature-size-input input");
        for (const [index, control] of [familyControl, sizeControl].entries()) {
          await control.waitFor({ state: "visible" });
          await control.scrollIntoViewIfNeeded();
          const bounds = await control.evaluate(element => {
            const r = element.getBoundingClientRect();
            return { left: r.left, right: r.right, top: r.top, bottom: r.bottom,
              vw: innerWidth, vh: innerHeight };
          });
          assert.ok(bounds.left >= -2 && bounds.right <= bounds.vw + 2
            && bounds.top >= -2 && bounds.bottom <= bounds.vh + 2,
            "signature font control " + index + " must fit viewport: " + JSON.stringify(bounds));
        }
        assert.equal(await sizeControl.getAttribute("inputmode"), "decimal",
          "font size must be an editable numeric input without dropdown");
        const urlButton = shell(signatureSelector).getByRole("button", { name: "Import Image from URL" });
        // The toolbar button exists before OpenSigWeaveEditor.create() returns its adapter.
        // Avoid clicking during that initialization window in the visual layout matrix.
        await visualPage.waitForFunction(() => {
          const manager = Alpine.$data(document.querySelector('[x-data="signatureManager"]'));
          return manager.editing && !manager.isViewingHistoricalVersion
            && !!window.signatureRichEditor
            && !!document.getElementById("signature-template-editor").__openSigWeaveEditor;
        });
        await urlButton.scrollIntoViewIfNeeded();
        await urlButton.click();
        const urlSelector = '[x-data="signatureManager"] [role="dialog"][aria-label="Import Image from URL"]';
        const modalState = await visualPage.evaluate(() => {
          const manager = Alpine.$data(document.querySelector('[x-data="signatureManager"]'));
          return { editing: manager.editing, imageImportOpen: manager.imageImportOpen,
            historic: manager.isViewingHistoricalVersion,
            editorReady: !!window.signatureRichEditor };
        });
        console.log("[browser-smoke] signature URL dialog " + prefix + JSON.stringify(modalState));
        await inspect(urlSelector, "image-url-import");
        await close(urlSelector);
        await close(signatureSelector);
        await nav("Signature Groups");
        await visualPage.getByRole("button", { name: "+ Add Group" }).click();
        const groupSelector = '[x-data="signatureGroupManager"] [x-show="editing"][role="dialog"]';
        await inspect(groupSelector, "signature-group");
        await close(groupSelector);
        await visualPage.getByRole("button", { name: "+ Add Signature to Group" }).click();
        const memberSelector = '[x-data="signatureGroupManager"] [x-show="memberEditor"][role="dialog"]';
        await inspect(memberSelector, "group-member");
        await close(memberSelector);
        await nav("Rules");
        await visualPage.getByRole("button", { name: "+ Add Rule" }).click();
        const ruleSelector = '[x-data="ruleManager"] [role="dialog"]';
        await inspect(ruleSelector, "rule");
        await shell(ruleSelector).locator('input[x-model="form.time_limited"]').check();
        await inspect(ruleSelector, "timed-rule");
        // Changing the time toggle makes the draft dirty. The common modal
        // coordinator correctly asks for discard confirmation before closing.
        await shell(ruleSelector).locator("[data-osw-dialog-close]:visible").first().click();
        const discardTimedRule = visualPage.locator('[x-data="confirmModal"] [role="alertdialog"]');
        await discardTimedRule.waitFor({ state: "visible" });
        await discardTimedRule.getByRole("button", { name: "Confirm", exact: true }).click();
        await shell(ruleSelector).waitFor({ state: "hidden" });
        await nav("Domains");
        await visualPage.getByRole("button", { name: "+ Add Domain" }).click();
        const addDomainSelector = '[x-data="addDomainModal"] [role="dialog"]';
        await inspect(addDomainSelector, "add-domain");
        await close(addDomainSelector);
        const row = visualPage.locator('[x-data="domainManager"] tbody tr')
          .filter({ hasText: "browser-smoke-domain.test" });
        await row.getByRole("button", { name: "Manage Domain" }).click();
        const domainSelector = '[x-data="domainSettingsModal"] [role="dialog"]';
        await inspect(domainSelector, "domain-settings");
        await shell(domainSelector).getByRole("button", { name: "Users & Overrides" }).click();
        // The Users view fetches asynchronously. Wait for its content to settle
        // before measuring footer position across a body scroll.
        await visualPage.waitForFunction(() => {
          const controller = Alpine.$data(document.querySelector('[x-data="domainSettingsModal"]'));
          return controller.modalTab === "users" && !controller.userLoading;
        });
        await inspect(domainSelector, "domain-users");
        await close(domainSelector);
        await visualPage.evaluate(() => window.dispatchEvent(new CustomEvent("open-sanitizer", { detail: "admin" })));
        const importSelector = '[x-data="sanitizerModal"] [role="dialog"]';
        await inspect(importSelector, "html-import");
        await close(importSelector);
        await visualPage.evaluate(() => window.dispatchEvent(new CustomEvent("open-preview", { detail: { html: "<p>Preview</p>" } })));
        const previewSelector = '[x-data="previewModal"] [role="dialog"]';
        await inspect(previewSelector, "preview");
        await close(previewSelector);
        await visualPage.evaluate(() => window.dispatchEvent(new CustomEvent("open-confirm", { detail: { action: "clear-user" } })));
        const confirmSelector = '[x-data="confirmModal"] [role="alertdialog"]';
        await inspect(confirmSelector, "confirmation");
        await shell(confirmSelector).getByRole("button", { name: "Cancel", exact: true }).click();
        await shell(confirmSelector).waitFor({ state: "hidden" });
        assert.equal(await visualPage.evaluate(() => document.body.classList.contains("osw-overlay-open")), false);
      } finally {
        await visualContext.close();
      }
    }
    checkpoint("dialog visual matrix: complete");

    // Run the same administrative path with a narrow mobile viewport.
    const mobileContext = await browser.newContext({ viewport: { width: 390, height: 844 } });
    await mobileContext.addCookies(await page.context().cookies());
    const mobilePage = await mobileContext.newPage();
    await mobilePage.goto(baseURL + "/?view=admin&section=domains");
    await mobilePage.getByRole("heading", { name: "Managed Domains" }).waitFor({ state: "visible" });
    const mobileLayout = await mobilePage.evaluate(() => ({
      documentWidth: document.documentElement.scrollWidth,
      viewportWidth: document.documentElement.clientWidth,
    }));
    assert.ok(mobileLayout.documentWidth <= mobileLayout.viewportWidth + 2,
      "mobile administrative view must not scroll horizontally: " + JSON.stringify(mobileLayout));
    const mobileRow = mobilePage.locator('[x-data="domainManager"] tbody tr').filter({ hasText: "browser-smoke-domain.test" });
    await mobileRow.getByRole("button", { name: "Manage Domain", exact: true }).click();
    await mobilePage.locator('[x-data="domainSettingsModal"]').getByRole("button", { name: "Users & Overrides", exact: true }).click();
    await mobilePage.getByRole("heading", { name: "Domain users" }).waitFor({ state: "visible" });
    const mobileOverlayOverflow = await mobilePage.evaluate(() => {
      const modal = document.querySelector('[x-data="domainSettingsModal"]');
      return { scrollWidth: modal.scrollWidth, clientWidth: modal.clientWidth };
    });
    assert.ok(mobileOverlayOverflow.scrollWidth <= mobileOverlayOverflow.clientWidth + 2,
      "domain dialog overlay must not overflow horizontally: " + JSON.stringify(mobileOverlayOverflow));
    assert.equal(await mobilePage.evaluate(() => document.body.classList.contains("osw-overlay-open")), true,
      "opening a dialog must lock background scrolling");
    await mobilePage.locator('[x-data="domainSettingsModal"]').getByRole("button", { name: "Close", exact: true }).click();
    await mobilePage.waitForFunction(() => !document.body.classList.contains("osw-overlay-open"));
    await mobileContext.close();

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
    const deniedDirectory = await normalPage.request.get(baseURL + "/api/admin/domains/1/users");
    assert.equal(deniedDirectory.status(), 403, "regular user must not enumerate domain users");
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
