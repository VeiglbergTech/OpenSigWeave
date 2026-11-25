# Signature groups: implemented design and operational behavior

**Status:** Implemented. Signature groups are the reusable weighted selection pool; the hard-coded legacy mode model has been retired from runtime, API and UI. Historical database structures remain only for safe upgrades and rollback compatibility.

## 1. Why groups

The previous model coupled a signature's identity to a fixed engine selector. The implemented group model removes that coupling: signatures are reusable templates and groups own weighted selection.

A signature group should be the selection pool. The group owns the selection policy; signatures remain reusable HTML templates.

## 2. Domain model

### SignatureGroup

- Stable internal UUID/hex ID.
- Unique normalized name within its scope.
- Optional domain assignment; global groups can be used by multiple domains.
- Active/disabled state and timestamps.
- Selection policy, initially WEIGHTED_RANDOM_NO_REPEAT.

### SignatureGroupMember

A many-to-many association between groups and signatures:

- group_id and signature_id form the primary key.
- weight is a positive integer and belongs to the membership, not the signature itself.
- is_enabled allows a signature to be temporarily excluded from one group without disabling the signature globally.

A signature may belong to multiple groups. A group may contain multiple signatures. This is preferable to a one-to-many model because it avoids duplicating the same HTML template for each business unit or campaign.

### Selection state

Selection history is keyed by a stable principal identifier and group ID, not by email address or a fixed selector. Email address is mutable and can change during a rename or domain migration.

The principal identifier must be namespaced by the identity source:
- Authentik: provider ID plus immutable user pk/UUID, not email.
- LDAP / Active Directory: provider ID plus immutable entryUUID / objectGUID, not distinguished name or email.
- Local emergency account: a stable local account identifier.

Do not silently fall back to email as the permanent key. If a provider cannot supply a stable identifier, surface that limitation and use an explicitly documented transitional compatibility path.

## 3. Selection behavior

Initial policy: weighted random with no immediate repeat.

1. Resolve the matching rule and its target group.
2. Load enabled group members whose signatures are active and eligible for the sender's domain.
3. Exclude the previous member when at least one alternative exists.
4. Select using a cryptographically secure weighted random choice.
5. Record the selected signature under (principal_id, group_id) in the same database transaction.
6. If a group contains only one eligible signature, it remains selectable on every send.
7. If no eligible member exists, do not inject a signature; never silently select from another group.

Concurrency must be serialized per principal and group. PostgreSQL row locking and a unique constraint must prevent two simultaneous messages from both selecting based on stale history.

## 4. Rule behavior

A rule action should become one of:
- GROUP: select from the referenced signature group.
- NONE: deliberately do not inject a signature.

Rules continue to use priority ordering and AND-combined sender/domain/group/claim/attribute conditions. The group reference is a foreign key and must be validated when creating or editing a rule.

Installed legacy rule actions are converted by migration `0014_retire_signature_modes` to ordinary generated groups before the current resolver runs.

## 5. Template tags

Group tags need a dedicated namespace so they cannot collide with profile attributes such as first_name, title or phone.

Proposed syntax: {{ group:unternehmen }} where unternehmen is the normalized group slug. Resolve a given group tag only once per message and reuse the exact selected signature wherever that tag appears again in the same message. Different group tags in the same message are independent.

The renderer must not interpret arbitrary unknown group names as profile attributes. Unknown, inactive or unauthorized groups resolve safely to an empty string and are logged without message content or secrets.

The administrator preview endpoint is `GET /api/admin/signature-groups/{group_id}/preview?domain_id={id}`. It renders a deterministic eligible member and returns `rotation_state_changed: false`; it does not require or create a user principal.

Group tags remain supported in existing HTML templates and can be entered manually in raw HTML. The legacy group-tag picker has been removed from the signature catalog editor. The administrator API exposes a deterministic group-member preview using the first eligible signature ID; it does not read or mutate production rotation state. The group overview offers one-click preview with an automatically chosen eligible domain; manual domain selection is available only inside Advanced Preview.

## 6. Preview behavior

Preview must not advance production rotation state. It should accept an explicit preview principal and group, or use a deterministic preview selection. Actual mail processing always uses the transactional selection path.

## 7. Migration and compatibility

Migration is intentionally additive. Earlier migrations and the historical mode/variant tables are kept so existing installations can upgrade without losing IDs, HTML versions, weights or rollback data. Migration `0014_retire_signature_modes` reconstructs/uses compatibility groups from historical variant rows, rewrites old pooled rules to `GROUP`, and neutralizes the retained signature metadata field to `UNUSED`. Empty generated groups are removed only if they have no memberships, rules, schedules **or administrator-assigned domain scopes**. Normal API, UI, scheduling, rules and engine paths do not consume the historical fields afterward. Compatibility-only stripping of deprecated email headers/HTML markers prevents stale selectors from leaking into outgoing messages; it does not reactivate old selection behavior.

## 8. Acceptance criteria

- Administrators can create, rename, disable and delete groups with clear handling of referenced groups.
- Signatures can be added to multiple groups with independent weights and enabled flags.
- Rules can select a group or NONE.
- Group tag placeholders resolve correctly and repeated placeholders select only once per message.
- Selection is stable per identity provider's immutable user ID and group, even when the user's email changes.
- Weighted random selection does not immediately repeat when alternatives exist.
- Concurrent messages cannot race rotation state.
- Preview does not mutate production rotation state.
- Existing signature HTML, versions, IDs and current production behavior survive migration.
- Group CRUD, membership, group rules, weighted no-repeat selection, stable principal validation, repeated group placeholders and the Rspamd engine path are covered by API/repository tests. The PostgreSQL concurrency stress test covers transactional group selection. Chromium/Playwright now also covers group creation, rename, membership weight updates, GROUP rule creation/update/deletion, and group deletion in the administration UI; live identity-provider group synchronization remains external acceptance work.
