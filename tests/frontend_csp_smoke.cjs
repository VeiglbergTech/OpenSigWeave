const assert = require("node:assert/strict");
const fs = require("node:fs");
const { JSDOM } = require("jsdom");

const dom = new JSDOM(`<!doctype html>
<html><body>
  <div id="portal" x-data="portalShell"><button id="portal-preview" @click="previewUserSignature()">Preview</button></div>
  <div id="rules" x-data="ruleManager">
    <span id="mode" x-text="form.action_mode"></span>
    <button id="switch" type="button" @click="setMode('INTERNAL')">Switch</button>
    <template x-for="signature in matchingSignatures" :key="signature.id">
      <span class="signature" x-text="signature.name"></span>
    </template>
  </div>
  <div id="toasts" x-data="toastManager" @notify.window="add($event)">
    <span id="toast-message" x-text="toasts.length ? toasts[0].msg : ''"></span>
  </div>
  <div id="toggle" x-data="toggleManager">
    <button id="toggle-button" type="button" @click="open()">Toggle</button>
    <span id="toggle-state" x-text="show ? 'open' : 'closed'"></span><p id="toggle-content" x-show="show" x-cloak>Visible</p>
  </div>
</body></html>`, {
  url: "https://opensigweave.test/",
  runScripts: "outside-only",
});

global.window = dom.window;
global.document = dom.window.document;
global.MutationObserver = dom.window.MutationObserver;
global.Element = dom.window.Element;
global.HTMLElement = dom.window.HTMLElement;
global.Node = dom.window.Node;
global.ShadowRoot = dom.window.ShadowRoot;
global.Document = dom.window.Document;
global.HTMLTemplateElement = dom.window.HTMLTemplateElement;
global.CustomEvent = dom.window.CustomEvent;
global.getComputedStyle = dom.window.getComputedStyle.bind(dom.window);
for (const key of Object.getOwnPropertyNames(dom.window)) {
  if (typeof dom.window[key] === "function" && global[key] === undefined) {
    global[key] = dom.window[key];
  }
}

window.portalShell = () => ({
  previewUserSignature() { window.dispatchEvent(new CustomEvent("preview-requested", { detail: "ok" })); },
});
window.ruleManager = () => ({
  form: { action_mode: "AUTO" },
  signatures: [
    { id: "a", name: "Automatic", mode: "AUTO", is_active: true },
    { id: "b", name: "Internal", mode: "INTERNAL", is_active: true },
    { id: "c", name: "Disabled", mode: "AUTO", is_active: false },
  ],
  get matchingSignatures() {
    return this.signatures.filter(item => item.is_active && item.mode === this.form.action_mode);
  },
  setMode(mode) {
    this.form.action_mode = mode;
  },
});

window.toastManager = () => ({
  toasts: [],
  add(event) {
    this.toasts.push({ msg: event.detail.msg });
  },
});
window.toggleManager = () => ({
  show: false,
  open() {
    this.show = true;
  },
});
global.ruleManager = window.ruleManager;
global.toastManager = window.toastManager;

const imported = require("@alpinejs/csp");
const Alpine = imported.default || imported;
Alpine.data("portalShell", window.portalShell);
Alpine.data("ruleManager", window.ruleManager);
Alpine.data("toastManager", window.toastManager);
Alpine.data("toggleManager", window.toggleManager);
window.Alpine = Alpine;
Alpine.start();

const tick = () => new Promise(resolve => setTimeout(resolve, 0));

(async () => {
  await tick();
  assert.equal(document.getElementById("mode").textContent, "AUTO");
  let previewRequested = false;
  window.addEventListener("preview-requested", event => { previewRequested = event.detail === "ok"; });
  document.getElementById("portal-preview").click();
  await tick();
  assert.equal(previewRequested, true);
  assert.deepEqual(
    [...document.querySelectorAll(".signature")].map(node => node.textContent),
    ["Automatic"],
  );

  document.getElementById("switch").click();
  await tick();
  assert.equal(document.getElementById("mode").textContent, "INTERNAL");
  assert.deepEqual(
    [...document.querySelectorAll(".signature")].map(node => node.textContent),
    ["Internal"],
  );

  window.dispatchEvent(new CustomEvent("notify", { detail: { msg: "CSP build active" } }));
  await tick();
  assert.equal(document.getElementById("toast-message").textContent, "CSP build active");

  document.getElementById("toggle-button").click();
  await tick();
  assert.equal(document.getElementById("toggle-state").textContent, "open");
  assert.equal(document.getElementById("toggle-content").hasAttribute("x-cloak"), false);

  const template = fs.readFileSync("templates/index.html", "utf8");
  const portalJs = fs.readFileSync("static/js/portal.js", "utf8");
  assert.match(template, /id="group-preview-domain"[^>]*x-model="previewDomainId"/, "Group preview must expose a domain selector");
  assert.match(template, /x-model="selectedGroupSlug"/, "Signature editor must expose a group tag selector");
  assert.match(template, /insertGroupTag\(selectedGroupSlug, \$refs\.signatureHtmlTemplate\)/, "Group picker must insert into the signature HTML editor");
  assert.match(template, /x-for="group in availableGroups"/, "Only active groups must be offered by the signature tag picker");
  assert.match(portalJs, /async fetchAll\(\)[\s\S]*?fetch\('\/api\/admin\/signature-groups'\)/, "Signature catalog must load signature groups");
  assert.match(portalJs, /insertGroupTag\(slug, editor\)[\s\S]*?\{\{ group:/, "Group tags must use the documented template namespace");
  assert.match(portalJs, /setSelectionRange\(start \+ token\.length/, "Group tag insertion must restore the cursor after the inserted tag");
  assert.match(template, /@click="previewGroup\(group\)"[^>]*>Preview<\/button>/, "Every group card must expose its preview action");
  assert.match(portalJs, /async previewGroup\(group\)[\s\S]*?\/api\/admin\/signature-groups\//, "Group preview must call the administrator preview API");
  assert.match(portalJs, /rotation history\./, "The UI must explain that preview does not mutate rotation history");
  assert.match(portalJs, /open-preview[\s\S]*?result\.html/, "The rendered API HTML must be sent to the existing preview modal");
  assert.match(portalJs, /Alpine\.data\("identityProviderManager", identityProviderManager\)/, "Identity provider Alpine component must be registered");
  assert.match(template, /@alpinejs\/csp@3\.17\.5/);
  assert.doesNotMatch(template, /alpinejs@3\.17\.5\/dist\/cdn\.min\.js/);
  assert.doesNotMatch(template, /x-data="\{ showHtml: false \}"/);
  assert.doesNotMatch(template, /document\.getElementById\(/);
  assert.match(template, /integrity="sha384-Q4UOujiwqAb\/jTt3ki4pdkETk4I3rIFQX8h271PLI41okj\/YIKY7IQQFMw73wkrX"/);
  const templateDom = new JSDOM(template);
  const directiveAttributes = [...templateDom.window.document.querySelectorAll("*")]
    .flatMap(element => [...element.attributes])
    .filter(attribute => attribute.name.startsWith("x-") || attribute.name.startsWith("@") || attribute.name.startsWith(":"));
  assert.ok(directiveAttributes.length >= 150, "Expected the complete Alpine template to be inspected");
  for (const attribute of directiveAttributes) {
    const expression = attribute.value;
    assert.doesNotMatch(expression, /=>/, "Arrow functions are not supported by the CSP evaluator");
    assert.equal(expression.includes(String.fromCharCode(96)), false, "Template literals are not supported by the CSP evaluator");
    assert.doesNotMatch(expression, /\?\./, "Optional chaining is not supported by the CSP evaluator");
    assert.doesNotMatch(expression, /\?\?/, "Nullish coalescing is not supported by the CSP evaluator");
    assert.doesNotMatch(expression, /\bnew\s+/, "Constructor expressions are not supported by the CSP evaluator");
    assert.doesNotMatch(expression, /;/, "Multi-statement directive expressions are not supported by the CSP evaluator");
  }
  assert.equal(templateDom.window.document.querySelectorAll("script:not([src])").length, 0, "The template must not introduce inline scripts");
  assert.equal([...templateDom.window.document.querySelectorAll("script[src^='https://']")].every(script => script.hasAttribute("integrity") && script.getAttribute("crossorigin") === "anonymous"), true, "External scripts must use SRI and anonymous CORS");
  templateDom.window.close();
  console.log("Alpine CSP evaluator smoke test passed");
  dom.window.close();
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
