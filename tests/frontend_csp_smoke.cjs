const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const { JSDOM } = require("jsdom");

const dom = new JSDOM(`<!doctype html>
<html><body>
  <div id="portal" x-data="portalShell"><button id="portal-preview" @click="previewUserSignature()">Preview</button></div>
  <div id="rules" x-data="ruleManager">
    <span id="action" x-text="form.action_mode"></span>
    <button id="switch" type="button" @click="setAction('GROUP')">Switch</button>
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
  form: { action_mode: "SIGNATURE" },
  signatures: [
    { id: "a", name: "Primary", is_active: true },
    { id: "b", name: "Secondary", is_active: true },
    { id: "c", name: "Disabled", is_active: false },
  ],
  get matchingSignatures() {
    return this.signatures.filter(item => item.is_active);
  },
  setAction(action) {
    this.form.action_mode = action;
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
  assert.equal(document.getElementById("action").textContent, "SIGNATURE");
  let previewRequested = false;
  window.addEventListener("preview-requested", event => { previewRequested = event.detail === "ok"; });
  document.getElementById("portal-preview").click();
  await tick();
  assert.equal(previewRequested, true);
  assert.deepEqual(
    [...document.querySelectorAll(".signature")].map(node => node.textContent),
    ["Primary", "Secondary"],
  );

  document.getElementById("switch").click();
  await tick();
  assert.equal(document.getElementById("action").textContent, "GROUP");
  assert.deepEqual(
    [...document.querySelectorAll(".signature")].map(node => node.textContent),
    ["Primary", "Secondary"],
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

  const notifications = [];
  window.addEventListener("notify", event => notifications.push(event.detail));
  window.fetch = async () => ({ ok: true, json: async () => [] });
  const signatureContext = {
    window,
    document,
    fetch: (...args) => window.fetch(...args),
    CustomEvent: window.CustomEvent,
    notify: (message, type) => notifications.push({ message, type }),
    Alpine: { data() {} },
    userData: {},
  };
  vm.runInNewContext(portalJs, signatureContext, { filename: "portal.js" });
  const signatureEditor = vm.runInNewContext("signatureManager()", signatureContext);
  assert.equal(typeof signatureEditor.insertGroupTag, "undefined",
    "the discontinued tag-insertion UI helper must be removed");
  assert.equal(template.includes("Advanced: Insert signature group tag"), false,
    "the signatures editor must not expose the removed tag picker");
  assert.equal(template.includes('id="group-preview-domain"'), false,
    "the group overview must not expose a permanent preview domain selector");
  assert.equal(template.includes('id="group-advanced-preview-domain"'), true,
    "domain selection must remain available inside Advanced Preview");

  const groupManager = vm.runInNewContext("signatureGroupManager()", signatureContext);
  const groupRequests = [];
  let groupResponses = [];
  window.fetch = async (url, options = {}) => {
    groupRequests.push({ url, options });
    const response = groupResponses.shift() || { ok: true, json: async () => [] };
    return response;
  };
  groupResponses = [
    { ok: true, json: async () => [{ id: "g1", name: "Sales", slug: "sales", domain_ids: [2], is_active: true,
      members: [{ signature_id: "s1", weight: 1, is_enabled: true }] }] },
    { ok: true, json: async () => [{ id: "s1", name: "Corporate", is_active: true, domain_ids: [] }] },
    { ok: true, json: async () => [{ id: 2, domain_name: "example.test", is_active: true }] },
  ];
  await groupManager.fetchAll();
  assert.equal(groupManager.groups[0].slug, "sales");
  assert.deepEqual([...groupManager.previewDomainsForGroup(groupManager.groups[0])].map(item => item.id), [2],
    "eligible domains must consider both group and member scopes");
  assert.equal(groupManager.signatureName("missing"), "Missing signature");
  groupManager.editGroup(groupManager.groups[0]);
  groupManager.toggleDomain("3", true);
  groupManager.toggleDomain("2", false);
  assert.deepEqual([...groupManager.form.domain_ids], [3]);
  groupManager.form.name = "  Sales Team  ";
  groupManager.form.slug = "";
  groupResponses = [{ ok: true, json: async () => ({}) }, ...[
    { ok: true, json: async () => [] },
    { ok: true, json: async () => [] },
    { ok: true, json: async () => [] },
  ]];
  await groupManager.saveGroup();
  const updateRequest = groupRequests.at(-4);
  assert.equal(updateRequest.url, "/api/admin/signature-groups/g1");
  assert.equal(updateRequest.options.method, "PUT");
  assert.deepEqual(JSON.parse(updateRequest.options.body), {
    name: "Sales Team", slug: null, domain_ids: [3], is_active: true,
  });
  assert.equal(groupManager.editing, false);

  const previewGroup = { id: "g/1", name: "Preview", is_active: true, domain_ids: [2],
    members: [{ signature_id: "s1", weight: 1, is_enabled: true }] };
  groupManager.domains = [{ id: 2, domain_name: "example.test", is_active: true }];
  groupManager.signatures = [{ id: "s1", name: "Corporate", is_active: true, domain_ids: [] }];
  let previewHtml = "";
  window.addEventListener("open-preview", event => { previewHtml = event.detail.html; });
  groupResponses = [{ ok: true, json: async () => ({
    html: "<p>Preview</p>", rotation_state_changed: false
  }) }];
  await groupManager.previewGroup(previewGroup);
  assert.equal(groupRequests.at(-1).url, "/api/admin/signature-groups/g%2F1/preview?domain_id=2");
  assert.equal(previewHtml, "<p>Preview</p>");
  assert.match(notifications.at(-1).msg, /does not change rotation history/);
  assert.equal(groupManager.previewDomainsForGroup({ ...previewGroup, domain_ids: [99] }).length, 0,
    "group domain restrictions must not be bypassed");
  assert.equal(groupManager.previewDomainsForGroup({ ...previewGroup,
    members: [{ signature_id: "s1", weight: 1, is_enabled: false }] }).length, 0,
    "disabled group members must not qualify for a preview");

  groupManager.openAdvancedPreview(previewGroup);
  assert.equal(groupManager.advancedPreviewOpen, true);
  assert.equal(groupManager.advancedPreviewDomainId, "2");
  groupResponses = [{ ok: true, json: async () => ({
    html: "<p>Advanced Preview</p>", rotation_state_changed: false
  }) }];
  await groupManager.previewAdvancedGroup();
  assert.equal(groupManager.advancedPreviewOpen, false);
  assert.equal(groupRequests.at(-1).url, "/api/admin/signature-groups/g%2F1/preview?domain_id=2");
  assert.equal(previewHtml, "<p>Advanced Preview</p>");

  groupManager.memberForm = { group_id: "g1", signature_id: "s1", weight: "4" };
  groupResponses = [{ ok: true, json: async () => ({}) }, ...[
    { ok: true, json: async () => [] },
    { ok: true, json: async () => [] },
    { ok: true, json: async () => [] },
  ]];
  await groupManager.addMember();
  const addRequest = groupRequests.at(-4);
  assert.equal(addRequest.url, "/api/admin/signature-groups/g1/members");
  assert.deepEqual(JSON.parse(addRequest.options.body), { signature_id: "s1", weight: 4, is_enabled: true });

  groupResponses = [{ ok: true, json: async () => ({}) }];
  await groupManager.saveMember({ id: "g1" }, { signature_id: "s1", weight: "5", is_enabled: false });
  assert.equal(groupRequests.at(-1).options.method, "PUT");
  assert.deepEqual(JSON.parse(groupRequests.at(-1).options.body), { signature_id: "s1", weight: 5, is_enabled: false });

  window.confirm = () => true;
  groupResponses = [{ ok: true, json: async () => ({}) }, ...[
    { ok: true, json: async () => [] },
    { ok: true, json: async () => [] },
    { ok: true, json: async () => [] },
  ]];
  await groupManager.removeMember({ id: "g1" }, { signature_id: "s1" });
  assert.equal(groupRequests.at(-4).url, "/api/admin/signature-groups/g1/members/s1");
  assert.equal(groupRequests.at(-4).options.method, "DELETE");

  groupResponses = [{ ok: true, json: async () => ({}) }, ...[
    { ok: true, json: async () => [] },
    { ok: true, json: async () => [] },
    { ok: true, json: async () => [] },
  ]];
  await groupManager.deleteGroup({ id: "g1", name: "Sales" });
  assert.equal(groupRequests.at(-4).url, "/api/admin/signature-groups/g1");
  assert.equal(groupRequests.at(-4).options.method, "DELETE");
  // Exercise the real ruleManager implementation, not only the CSP fixture.
  const rulesManager = vm.runInNewContext("ruleManager()", signatureContext);
  const ruleRequests = [];
  let ruleResponses = [];
  window.fetch = async (url, options = {}) => {
    ruleRequests.push({ url, options });
    const response = ruleResponses.shift() || { ok: true, json: async () => [] };
    return response;
  };
  ruleResponses = [
    { ok: true, json: async () => [{ id: "r1", name: "Existing", action_mode: "GROUP", group_id: "g1", conditions: [{ kind: "sender_equals", value: "sales@example.test" }] }] },
    { ok: true, json: async () => [
      { id: "s1", name: "Primary", is_active: true },
      { id: "s2", name: "Inactive", is_active: false },
      { id: "s3", name: "Secondary", is_active: true },
    ] },
    { ok: true, json: async () => [{ id: "g1", name: "Sales" }] },
  ];
  await rulesManager.fetchAll();
  assert.equal(rulesManager.rules[0].name, "Existing");
  assert.equal(rulesManager.groups[0].id, "g1");
  rulesManager.form.action_mode = "SIGNATURE";
  assert.deepEqual([...rulesManager.matchingSignatures].map(item => item.id), ["s1", "s3"]);
  rulesManager.editRule(rulesManager.rules[0]);
  assert.equal(rulesManager.form.group_id, "g1");
  assert.equal(rulesManager.form.signature_id, "");
  rulesManager.changeAction();
  assert.equal(rulesManager.form.group_id, "");
  assert.equal(rulesManager.form.signature_id, "");
  rulesManager.newRule();
  rulesManager.form.name = "  Sales routing  ";
  rulesManager.form.action_mode = "GROUP";
  rulesManager.form.group_id = "g1";
  rulesManager.addCondition();
  rulesManager.form.conditions[0] = { kind: "sender_equals", key: "", value: "sales@example.test" };
  rulesManager.addCondition();
  rulesManager.form.conditions[1].value = "signature-users";
  ruleResponses = [{ ok: true, json: async () => ({ id: "r2" }) },
    { ok: true, json: async () => [] },
    { ok: true, json: async () => [] },
    { ok: true, json: async () => [] }];
  await rulesManager.saveRule();
  const savedRule = ruleRequests.find(request => request.url === "/api/admin/rules" && request.options.method === "POST");
  assert.ok(savedRule, "Creating a rule must use the rules API");
  assert.deepEqual(JSON.parse(savedRule.options.body), {
    id: null, name: "  Sales routing  ", priority: 0, action_mode: "GROUP",
    signature_id: null, group_id: "g1", is_enabled: true,
    conditions: [
      { kind: "sender_equals", key: "", value: "sales@example.test" },
      { kind: "sender_equals", key: "", value: "signature-users" },
    ],
  });
  assert.equal(rulesManager.editing, false);
  window.confirm = () => true;
  ruleResponses = [{ ok: true, json: async () => ({}) },
    { ok: true, json: async () => [] },
    { ok: true, json: async () => [] },
    { ok: true, json: async () => [] }];
  await rulesManager.deleteRule({ id: "r2", name: "Sales routing" });
  const deleteRule = ruleRequests.find(request => request.url === "/api/admin/rules/r2" && request.options.method === "DELETE");
  assert.ok(deleteRule, "Deleting a rule must use its identifier in the DELETE endpoint");

  // Exercise identity-provider form state without a second network fetch.
  const identityManager = vm.runInNewContext("identityProviderManager()", signatureContext);
  identityManager.changeType({ target: { value: "oidc" } });
  assert.equal(identityManager.form.provider_type, "oidc");
  assert.equal(identityManager.form.settings.OIDC_SCOPES, "openid email profile groups");
  identityManager.editProvider({
    id: "oidc-main",
    display_name: "Company OIDC",
    provider_type: "oidc",
    is_enabled: true,
    settings: {
      OIDC_DISCOVERY_URL: "https://idp.example.org/.well-known/openid-configuration",
      OIDC_CLIENT_ID: "opensigweave",
      OIDC_REDIRECT_URI: "https://signatures.example.org/auth/oidc-main",
    },
  });
  assert.equal(identityManager.editing, true);
  assert.equal(identityManager.form.settings.OIDC_DISCOVERY_URL,
    "https://idp.example.org/.well-known/openid-configuration");
  assert.equal(identityManager.form.settings.OIDC_CLIENT_ID, "opensigweave");
  assert.equal(identityManager.form.settings.OIDC_CLIENT_SECRET, "");

  const themeScript = fs.readFileSync("static/js/theme.js", "utf8");
  const portalCss = fs.readFileSync("static/css/portal.css", "utf8");
  assert.match(template, /id="theme-toggle"/, "Portal must expose an appearance toggle");
  assert.match(template, /src="\/static\/js\/theme.js"/, "Theme must initialize before first paint");
  assert.match(themeScript, /opensigweave\.theme\.v1/, "Browser should retain the selected theme");
  assert.match(themeScript, /aria-pressed/, "Toggle must advertise its active state");
  assert.match(portalCss, /\.ck\.ck-dropdown__panel/, "CKEditor menus need themed surfaces");
  assert.match(portalCss, /--osw-text/, "Theme foreground colors must be defined");
  assert.match(portalCss, /ck-editor__editable[\s\S]*?background: #fff !important/, "Email canvas must be white");
  assert.match(template, /id="group-preview-domain"[^>]*x-model="previewDomainId"/, "Group preview must expose a domain selector");
  assert.match(template, /x-model="selectedGroupSlug"/, "Signature editor must expose a group tag selector");
  assert.match(template, /Advanced: Insert signature group tag/, "Group tags must be behind advanced disclosure");
  assert.match(template, /Choose ZIP file/, "Import requires a clearly labeled ZIP selector");
  assert.match(template, /<summary class="cursor-pointer">Scheduling priority<\/summary>/,
    "Scheduling precedence must remain accessible in a collapsible help section");
  assert.match(template, /Start is inclusive; end is exclusive\./,
    "Scheduling help must retain the required time-window semantics");
  for (const retiredMode of ["AU" + "TO", "PROF" + "ESSIONAL", "INTER" + "NAL"]) {
    assert.ok(!template.includes("<option>" + retiredMode + "</option>"),
      "User-facing retired signature mode options must be removed");
  }
  assert.match(template, /<option value="SIGNATURE">Signature<\/option>/,
    "Rule actions must target a concrete signature instead of a mode");
  assert.match(template, /id="outlook-markup-warning"[^>]*role="alert"/, "Personal editor must warn when Outlook-specific markup is detected");
  assert.doesNotMatch(template, /id="domain-raw-html-editor"/, "Retired domain master editor must not be offered");
  assert.match(template, /id="admin-outlook-markup-warning"[^>]*role="alert"/, "Admin editor must warn when Outlook-specific markup is detected");
  assert.ok(portalJs.includes(String.raw`const outlookMarkup = /<!--\s*\[if\s+mso\b|<\s*v:[a-z][\w-]*/i;`), "Editor must detect Outlook conditional comments and VML");
  assert.match(portalJs, /checkAllLocks\(\)[\s\S]*?checkOutlookMarkupWarnings\(\)/, "Editor state updates must refresh the Outlook markup warning");
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
