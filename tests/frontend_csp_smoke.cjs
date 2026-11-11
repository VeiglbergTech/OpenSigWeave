const assert = require("node:assert/strict");
const { JSDOM } = require("jsdom");

const dom = new JSDOM(`<!doctype html>
<html><body>
  <div id="rules" x-data="ruleManager()">
    <span id="mode" x-text="form.action_mode"></span>
    <button id="switch" type="button" @click="setMode('INTERNAL')">Switch</button>
    <template x-for="signature in matchingSignatures" :key="signature.id">
      <span class="signature" x-text="signature.name"></span>
    </template>
  </div>
  <div id="toasts" x-data="toastManager()" @notify.window="add($event)">
    <span id="toast-message" x-text="toasts.length ? toasts[0].msg : ''"></span>
  </div>
  <div id="toggle" x-data="{ show: false }">
    <button id="toggle-button" type="button" @click="show = !show">Toggle</button>
    <p id="toggle-content" x-show="show" x-cloak>Visible</p>
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
global.CustomEvent = dom.window.CustomEvent;
global.getComputedStyle = dom.window.getComputedStyle.bind(dom.window);

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
global.ruleManager = window.ruleManager;
global.toastManager = window.toastManager;

const imported = require("@alpinejs/csp");
const Alpine = imported.default || imported;
window.Alpine = Alpine;
Alpine.start();

const tick = () => new Promise(resolve => setTimeout(resolve, 0));

(async () => {
  await tick();
  assert.equal(document.getElementById("mode").textContent, "AUTO");
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
  assert.equal(document.getElementById("toggle-content").style.display, "");

  console.log("Alpine CSP evaluator smoke test passed");
  dom.window.close();
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
