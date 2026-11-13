# Signature groups: target design

**Status:** Backend, administrator API/UI, rule selection, stable-principal rotation and group placeholders are implemented on the development branch. Existing AUTO / PROFESSIONAL / INTERNAL / NONE behavior remains available for compatibility. The migrations create reusable legacy groups from existing variants without rewriting existing rules.

## 1. Why groups

The current model couples a signature's identity to a fixed engine mode. That prevents administrators from creating arbitrary reusable pools and makes rule behavior depend on a small hard-coded set of modes.

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

Selection history is keyed by a stable principal identifier and group ID, not by email address and a mode string. Email address is mutable and can change during a rename or domain migration.

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

The old mode actions remain readable during the compatibility period. They must be migrated to generated legacy groups before the new resolver becomes the default.

## 5. Template tags

Group tags need a dedicated namespace so they cannot collide with profile attributes such as first_name, title or phone.

Proposed syntax: {{ group:unternehmen }} where unternehmen is the normalized group slug. Resolve a given group tag only once per message and reuse the exact selected signature wherever that tag appears again in the same message. Different group tags in the same message are independent.

The renderer must not interpret arbitrary unknown group names as profile attributes. Unknown, inactive or unauthorized groups resolve safely to an empty string and are logged without message content or secrets.

The administrator preview endpoint is `GET /api/admin/signature-groups/{group_id}/preview?domain_id={id}`. It renders a deterministic eligible member and returns `rotation_state_changed: false`; it does not require or create a user principal.

The signature catalog editor now provides a group-tag picker. It loads active groups from the administrator API and inserts `{{ group:slug }}` at the current HTML textarea cursor (or replaces the selected text). The picker changes only the editor buffer; existing HTML and group membership are not modified until the signature version is saved. Manual entry remains supported. The administrator API also exposes a deterministic group-member preview using the first eligible signature ID; it does not read or mutate production rotation state.

## 6. Preview behavior

Preview must not advance production rotation state. It should accept an explicit preview principal and group, or use a deterministic preview selection. Actual mail processing always uses the transactional selection path.

## 7. Migration and compatibility

The migration must be additive and preserve existing IDs, HTML files, versions, hashes and rule ordering.

1. Create group and group-membership tables.
2. Create one generated legacy group for each existing mode pool (AUTO, PROFESSIONAL, INTERNAL).
3. Add each existing signature to the matching generated group. Preserve its current variant weight and enabled state as membership metadata.
4. Preserve NONE as a rule action; it does not become a group.
5. Add a nullable group reference to rules and populate it for legacy mode rules.
6. Keep old mode columns and variant rows during the compatibility release. Do not delete or rewrite signature files.
7. Add stable identity IDs to provider attribute results and their cache. Backfill only where a trustworthy immutable provider ID can be retrieved; never infer it from email.
8. Run old and new selection in isolated tests, then switch the engine to group selection behind an explicit feature flag.
9. Remove legacy mode storage only in a later migration after compatibility and rollback windows have passed.

Migrations `0009`–`0011` are tested against PostgreSQL and SQLite. Migration `0011` creates `legacy-auto`, `legacy-professional` and `legacy-internal` groups from existing weighted variants while leaving existing rules and signature assets untouched. The populated legacy fixture verifies aggregation of multiple variants, disabled-only members, all three generated groups and preservation of existing rule action, group reference and priority.

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
- Group CRUD, membership, group rules, weighted no-repeat selection, stable principal validation, repeated group placeholders and the Rspamd engine path are covered by API/repository tests. PostgreSQL concurrency stress and full UI browser tests remain outstanding.
