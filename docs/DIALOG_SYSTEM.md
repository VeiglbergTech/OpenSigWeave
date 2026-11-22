# Web dialog layout

The **Register New Mail Domain** and **Domain Management** dialogs are the visual references for the portal. All 11 dialog types use the same three-part structure:

```html
<div class="osw-modal-root">
  <div class="osw-modal-placement">
    <div class="osw-modal-shell osw-modal-width-medium" role="dialog" aria-modal="true">
      <div class="osw-modal-header">Title / close action</div>
      <div class="osw-modal-body">Form, preview or message</div>
      <div class="osw-modal-footer">Cancel / save actions</div>
    </div>
  </div>
</div>
```

The six administrator editors retain their existing Alpine `x-show` and event bindings on the inner dialog, inside their original `x-data` scope. The shared fixed-position root centers the shell as it does for the domain references. No editor uses a second, independent fixed-position rule.

The CSS variables and shared `osw-modal-*` rules in `static/css/portal.css` define the border, colors, typography, paddings and scrolling. The optional width classes are **small (30rem)** for short forms and confirmations, **medium (54rem)** for ordinary forms and previews, and **large (64rem)** for the signature editor and Domain Management. Each shell is capped to the available viewport, including on mobile; only the body scrolls and the footer remains reachable. Editor content keeps the existing white email canvas.

`initOswDialogs()` in `static/js/portal.js` remains the single coordinator for backdrop visibility, focus, Escape/Tab, dirty forms, and closing actions. All domain/editor/preview/confirmation dialogs use the same backdrop. Existing save calls and validation are not rewritten.

## Regression coverage

`tests/browser_smoke.cjs` exercises all eleven dialog types across dark/light themes at 1440×900 and 390×844. It compares computed shell/header/body/footer styles against the **Register New Mail Domain** reference; checks centered positioning, overlay, viewport containment, accessible footer buttons and scroll-stable actions; and exercises close/cancel, discard confirmation, preview and save workflows. Screenshots are stored under `test-artifacts/dialogs/` in the browser CI job.

Run the browser smoke check through the existing `.github/workflows/tests.yml` job; it provisions a real application session and Playwright. The same workflow also runs syntax and backend tests. **Only push to `dev`**. The existing CI and Security-CI promotion rules control `beta`; do not promote to `main` automatically.
