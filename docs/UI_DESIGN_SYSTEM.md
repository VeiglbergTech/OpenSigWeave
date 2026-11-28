# OpenSigWeave UI design system and health diagnostics

**Status:** Proposed design specification for implementation; not a claim that the current application already conforms.
**Target:** `dev` only. Promote to `beta` after green CI and visual acceptance; `main` only by explicit release approval.
**Scope:** All portal views (personal/admin), login/setup, overlays, signature editing, dynamic branding, and operational status.
**Language:** English throughout the application.

## 1. Product and UX principles

1. **One application, one visual grammar.** The domain-management UI is a historical reference, not a second style system. Build shared component primitives instead of adding local override rules to each tab.
2. **Dark mode:** near-black canvas and charcoal surfaces with a restrained red/orange accent. **Light mode:** neutral, warm-compatible near-white surfaces with identical spacing, shape, component semantics and focus treatment.
3. **Branding is not semantic status.** Brand accent may change, but success/warning/error colors do not. User-selected colors are displayed exactly in the branding picker and swatch; contrast-corrected derivatives may be used for accessible buttons and text, clearly labeled as the applied contrast-safe variant.
4. **Consistency beats density.** Every interactive item has an obvious hit area, border, selected state and spacing from its neighbors.
5. **Editor equivalence.** Personal and admin signature editors use the same CKEditor 5 build, component factory, font behavior, toolbar order, themes, field layout and responsive behavior. Permission-restricted operations are explicitly disabled or hidden with an explanation, not replaced with a different editor.
6. **No invisible or unreachable actions.** Dialog headers and action footers remain usable on short/mobile viewports, at high zoom, with virtual keyboards and CKEditor dropdowns.
7. **Health states must describe what was actually tested.** TCP connectivity, app readiness, internal dependencies, Rspamd module registration and successful end-to-end mail injection are distinct signals.

## 2. Color tokens

Use semantic CSS custom properties with exactly one dark/default and one light definition. Do not scatter hard-coded Tailwind color utilities across templates.

| Role | Dark | Light | Rule |
|---|---|---|---|
| Application canvas | `#09090B` | `#F2F5F9` | Whole page |
| Header/table header | `#171719` | `#F6F8FB` | Distinct from cards |
| Card/dialog surface | `#202024` | `#FFFFFF` | Primary containers |
| Raised/hover/select surface | `#29292F` | `#E9EEF5` | Secondary controls |
| Input background | `#121215` | `#FFFFFF` | Bordered inputs |
| Neutral border | `#3B3B43` | `#CBD5E1` | Outline and section delimiters |
| Main foreground | `#F4F4F5` | `#1E293B` | Normal text |
| Secondary foreground | `#A1A1AA` | `#516174` | Descriptions and metadata |
| Heading | `#FFFFFF` | `#111827` | All page/card/dialog headings |
| Brand accent (stored default) | `#FF7043` | `#FF7043` | Exact chosen swatch; computed contrast variants for actions |
| Critical action | `#B91C1C` | `#B91C1C` | Delete/destructive, never ordinary save |

Additional semantic variables: `--osw-bg`, `--osw-surface`, `--osw-surface-raised`, `--osw-input-bg`, `--osw-border`, `--osw-text`, `--osw-text-muted`, `--osw-heading`, `--osw-brand-chosen`, `--osw-brand-action`, `--osw-brand-text`, `--osw-focus`, `--osw-success-*`, `--osw-warning-*`, `--osw-danger-*`.

Keep legacy `--osw-black`, `--osw-panel` etc. as *aliases* while migrating templates. Remove competing hard-coded overrides when migration is complete. One JS owner (`static/js/branding.js`) computes effective contrast-safe accent variants, and `static/js/theme.js` remains the only theme persistence/logo switch owner. Changing theme or resetting branding must immediately update every surface and CKEditor chrome; no stale inline colors.

**Contrast requirements:** WCAG AA minimum 4.5:1 for normal text, 3:1 for large text and meaningful controls/focus indicators. At least one non-color signal must distinguish a selected option, failure or disabled control. The email HTML preview itself is always white in both themes and must not be recolored.

## 3. Typography and geometry

Use a system font stack (Segoe UI, Inter, Arial, sans-serif; no mandatory external font downloads). Standard line height 1.5.

| Element | Font | Weight | Geometry |
|---|---|---|---|
| Page heading | 24px | 600 | One shared `osw-page-header` |
| Section heading | 18px | 600 | One shared `osw-section-heading` |
| Card/dialog title | 16–18px | 600 | Same in personal/admin |
| Body text | 14–16px | 400 | No arbitrary bold labels |
| Field label/button | 14px | 500 | Identical all tabs |
| Muted helper text | 12–13px | 400 | Full contrast checks |
| Table header | 12–13px | 600 | Same case and letter spacing |
| Input/select/button | 14px | 400–500 | Min. 40px high; 44px touch target |
| Border radius | 8px card, 6px control, 14px dialog | — | No random combinations |

Spacing scale: **4 / 8 / 12 / 16 / 24 / 32px**. Field label -> input 6–8px; field -> field 16px; control grouping 8–12px; card padding 20–24px desktop, 16px mobile; page grid gap 24px; section gap 24–32px. Use fluid administration and editor content width with responsive gutters (no global fixed maximum width); constrain only prose or forms that benefit from a readable measure. Sections always align to the same horizontal edge.

The global app bar, admin navigation and section heading use separate but consistent hierarchy. Do not make some tab headings bold while others are plain. Display page title at 24px/600; make sub-navigation obvious with an active indicator beyond background color.

## 4. Component contracts

### Headers, cards and navigation
- `osw-page-header`: left title + optional subtitle, right actions. All headings use the same typography regardless of nested `h1/h2` or wrapper `div`; mobile actions wrap below the title.
- `osw-card`: one surface, 1px border, 8px radius, standard padding; `osw-card-header` for repeated card titles; avoid nested visually identical gray boxes without space or dividers.
- `osw-tabs`: active fill + indicator/border + `aria-selected`; inactive and hover states clearly distinct. A pair of adjacent options must never resemble one large clickable area.
- Tables: one header format; consistent row height, alignment, hover and separator semantics. Narrow screens use responsive row/card layouts without horizontal page or table scrolling. Keep header/cell insets identical and preserve right alignment for Actions.

### Form controls
- One `osw-field` wrapper per value: visible label, optional description, control, inline validation/error. Validation never relies only on color. Focus ring and disabled appearance are uniform across all themes.
- **Switch = binary setting only** (On/Off), accessible `role="switch"`, native checked state. Track approximately 44×24px, 16px thumb, checked thumb moves approximately 20px, full 44px touch target. Text label placed consistently to the right. Disabled state visibly and programmatically disabled.
- **Checkbox = independent multi-selection**. Use a standard square checkbox with check mark for domain scopes, import/export selections and any other multi-select. Remove the current visual conflation with `osw-switch--multi`.
- Radio buttons / segmented options = mutually exclusive choices, each with a separate visible border, padding and active indicator.
- Selects show a clear dropdown icon and boundary. Two side-by-side selects keep at least 16px gap and explicit labels. Native and custom dropdowns must not inherit illegible light/dark foreground.
- Primary action: contrast-safe brand color; secondary: raised neutral; destructive: semantic red; tertiary: text-only with visible focus. Equal heights/paddings, one common disabled/loading design.

### Overlays and dialog behavior
- One structure for **all** dialogs: root/backdrop -> placement -> shell -> fixed header -> one scrollable body -> fixed action footer. Reuse `osw-modal-*` classes and `initOswDialogs()`; no second fixed-position system.
- Width presets: small 480px, medium 864px, large 1024px, always capped to viewport width minus safe margins.
- Max height: `calc(100dvh - 32px)` desktop; `calc(100dvh - 16px - env(safe-area-inset-top) - env(safe-area-inset-bottom))` on mobile. Shell and every flex ancestor must have `min-height: 0`. Body alone uses `overflow-y: auto` and `overscroll-behavior: contain`; footer never disappears behind scrolling content.
- **Never change modal body overflow to `visible` on CKEditor menu open.** Existing `:has(.ck-dropdown__panel-visible)` override is a clipping/scroll regression risk. Use a viewport-constrained editor menu/portal overlay with an independent scroll and suitable stacking context instead.
- Keep toolbar overlays above modal content but below confirmation dialogs, and flip/shift dropdowns near viewport edges. Do not allow menus to exceed mobile viewport.
- Initial focus, Tab trapping, Escape close, background scrolling lock, focus return and unsaved-change confirmation are mandatory. Native keyboard and touch scrolling both work.

## 5. Single editor implementation

`static/js/rich-editor.js` stays the sole owner of CKEditor 5 configuration. The personal editor, admin user override and signature catalog all call the same `OpenSigWeaveEditor.create` adapter. `static/js/portal.js` only provides permissions and model bindings.

Shared toolbar order and behavior: font family search/scroll; numeric font size entry with autocomplete and validated 6–96pt (preserve existing px); text formatting; font color/background color; link; list and alignment; tables and horizontal rules; source/HTML capability. All have identical sizing/spacing/keyboard support. This is the existing CKEditor-only decision; do not reintroduce Quill.

Feature-specific upload actions are exposed **only** where the server grants corresponding permissions. The personal editor must still look and behave like the admin editor for all *shared* formatting functions; provide an explicit explanation if an action requires catalog access. Do not bypass authorization just for visual parity.

Editor chrome adapts to theme, content canvas stays white for accurate email appearance. Preserve advanced raw HTML, legacy Outlook markup warnings, source fidelity until an actual model edit, font families/sizes, asset references and existing sanitizer constraints. Auto-size editing region within the dialog rather than letting it expand the entire modal. The editor area and its outer dialog can scroll independently, with clear focus and scroll boundaries.

## 6. Status / health architecture (functional requirement)

Keep these separate in both APIs and UI:

| Level | Probe | Green means | Do **not** claim |
|---|---|---|---|
| Container liveness | HTTP `GET /livez` from inside container | HTTP server event loop responds | Database/mail adapter works |
| Application readiness | HTTP `GET /readyz` | Core DB and required signature/image storage can be used | Outbound mail was modified |
| Admin system diagnostics | Authenticated `GET /api/admin/system-status` | Current explicit DB/storage/support-alert results | External controller reachable |
| Mail adapter monitor | Read-only adapter-specific probe | Module registration / expected config verified | Successful end-to-end message rewrite |
| SMTP acceptance | Authenticated disposable test message + inspection | Signature is present in delivered MIME and final DKIM is valid | Continuous ongoing health |

**Current observed issues (dev source):**
- `compose.yaml` container healthcheck only opens a TCP socket; it cannot detect application-level readiness. Replace with explicit HTTP liveness check and expose independent readiness. Readiness failures should be diagnosable without turning temporary *external* Rspamd outages into container restart loops.
- `core/adapter_monitor.py` `_find_symbols()` checks only `symbols` and certain top-level keys; Rspamd `GET /symbols` may return grouped `rules` with `symbol` properties. Parse these narrowly and safely; add realistic fixture tests. No malformed/unavailable/unauthenticated response may turn green.
- The Rspamd controller is often bound to localhost and its protected read-only endpoints can require authentication from non-trusted clients. Do not expose port 11334 publicly to make the check pass. Implement a documented safe strategy for private connectivity and read-only credentials (if needed); keep SSRF restrictions, avoid redirects, log only safe status codes/reason categories.
- The scheduler already starts during FastAPI startup, but the result needs visible `last_checked`, `next_check`, `status`, `reason`, `probe type` and a user-triggered **Check now** action. Distinguish `not configured`, `not checked`, `unsupported`, `unknown`, `missing`, `configured`, `degraded`, `unreachable`, `auth required` and `stale`. Never show `configured` as “signature successfully inserted”.
- The UI displays no sensitive controller URL credentials or email bodies. Make manual probe results explain failure without exposing secrets. Browser errors 401/403/500 from the admin status endpoint must produce distinct, understandable user feedback.

Verification: unit-test nested Rspamd response formats, missing one/both symbols, invalid content, refusal, auth failure, inaccessible endpoint, scheduler timing, stale results and role enforcement. Separately test Docker live/ready endpoints through real HTTP, with database/storage broken. The final SMTP test remains an explicit integration test, not a fake “health” status.

### Canonical navigation and realtime UI

The browser route contract is `/user`, `/admin`, and `/admin/{tab}`. The administrator’s tab list is deep-linkable; URL state is represented by the pathname, not `?view=...&section=...`. Existing query bookmarks redirect to canonical paths.

The System tab uses an authenticated same-origin WebSocket (`/ws/portal`) instead of a 10-second JavaScript interval. The backend pushes the first snapshot, refresh responses, and relevant change notifications. Its 20-second backend check detects out-of-process state changes but **does not** trigger browser HTTP polling or redraw unchanged status. The backup overview also uses WebSocket change notifications instead of its previous 20-second browser interval. A disconnected socket is clearly marked and reconnects with bounded exponential delay. The REST diagnostics endpoint remains available for operators and machine clients.

The signed browser session and administrator role protect status frames, and the WebSocket handshake rejects untrusted or missing Origin headers. A process-local event queue supports the default single-worker deployment; multiple ASGI worker processes require a shared pub/sub broker for immediate cross-worker list invalidations.

## 7. Known implementation targets and causes

- `templates/index.html`: normalize heading/header markup and classes, switch vs checkbox semantics, selected groups, personal/admin editor containers, modal shell nesting and control labels.
- `static/css/portal.css`: consolidate semantic tokens, remove redundant hard-coded color overrides, standardize all headings/forms/cards and control dimensions, correct switches and multi-checkboxes, eliminate `:has(...): overflow: visible` on scrollable dialog body.
- `static/js/branding.js` and `static/js/theme.js`: preserve exact selected color in settings; separate effective contrast-safe action/text variants; update immediately on theme/branding changes and reset.
- `static/js/rich-editor.js` / `static/js/portal.js`: share all formatting/editor chrome, permission-gate asset actions, fix viewport-aware editor overlays, show clear status labels.
- `core/adapter_monitor.py`, `core/monitor_scheduler.py`, `main.py`, `compose.yaml`: implement trustworthy health layers, Rspamd response parsing and UI diagnostics without exposing controllers.
- Do not introduce additional branches or new JavaScript/CSS frameworks. Use existing Alpine, self-hosted CKEditor and the project CSS stack.

### Global attribution and table/status UI contract

- Every rendered page (`/user`, `/admin/*`, login, setup and access denied) includes the same unconditional `Powered by OpenSigWeave` GitHub footer. Branding and environment settings do not hide it. This does not restrict GPLv3 source modification.
- The application body uses a column flex layout; content expands on short pages, while the attribution remains in normal flow, below editor/modals and without fixed positioning.
- Table wrappers clip their own decorative rounded backgrounds. Data tables use separated borders with zero spacing, shared column padding and matching action alignment; mobile/tablet layouts retain labelled rows rather than horizontal scrolling.
- Status outputs use `Active` rather than `Enabled`; persisted `is_enabled`, `enabled`, `is_active`, API fields, and technical settings remain unchanged.
- The branding swatch affects primary action fills but never redefines link tokens. Links retain their own recognizable colors, underline and focus treatment in both themes.

## 8. Acceptance and regression criteria

A change is done only when all conditions below hold:

1. All personal/admin/login/setup screens use the same token colors, heading weights, spacing, fields, buttons, tables and navigation grammar. All copy remains English.
2. Dark/light themes, brand-color changes and global branding reset update *all* controls in-place; stored chosen swatch remains exact; computed action/text colors pass contrast.
3. Binary controls behave as switches; multi-selection controls look and behave as checkboxes. Mouse, touch and keyboard interactions correctly reflect state and persist it.
4. Every overlay and editor fits viewports **320×568, 390×844, 768×1024, 1440×900** and at **200% zoom**. Body can scroll to every field; footer buttons never become unreachable; dropdowns are scrollable and do not disable parent scroll.
5. Personal, admin-override and signature-catalog editors produce equivalent HTML for equivalent shared formatting actions. Permission differences are deliberate, documented and tested. White preview canvas and raw HTML/Outlook safeguards remain.
6. Visual regression in Playwright compares consistent properties (tokens, bounding boxes, typography and semantic states), screenshots against reviewed baselines where feasible; screenshots alone are not considered a pass.
7. Health probes differentiate TCP/HTTP liveness, DB/storage readiness, authenticated admin diagnostic, adapter symbol registration and SMTP integration. Failures are explicit and cannot produce false healthy results.
8. Python tests, real-browser smoke tests, syntax/security checks and GitHub Actions on **dev** pass. Only then consider `dev` -> `beta` by existing promotion policy; do not touch `main` in this design phase.

## 9. Execution sequence

1. **Health correctness first:** add realistic Rspamd fixtures, fix parsing/auth failure classifications, introduce HTTP live/ready checks, expose truthful UI diagnostics, then prove behavior on disposable or authorized live deployment.
2. **Tokens and components:** implement shared headers/cards/buttons/form controls/tabs, separate multi-checkbox styling, standardize both themes.
3. **Dialog/CKEditor mechanics:** remove overflow regression, normalize modal structure and fix responsive menus and editor parity.
4. **Cross-tab polish:** audit all admin and personal views for spacing, group separation, text casing, selected state and brand color application.
5. **Regression and rollout:** automated browser checks with manual screenshot approval, full CI and security CI on dev, beta deployment test, release approval to main.

This document is the **source of truth for the intended UI**. Existing scattered docs (`DIALOG_SYSTEM.md`, `LIGHT_MODE_QA.md`, `EDITOR_DECISION.md`) record earlier technical decisions and should be linked back here when implementation is completed.
