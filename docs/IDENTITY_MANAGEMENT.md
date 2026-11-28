# Identity and user-override management

## Provider capabilities and backward compatibility

The **Identity Providers** page separates **Web Interface** authentication from
**User Provisioning** directory/profile lookups. Both tables operate on the
existing provider records and filesystem-backed `.idp/<slug>/.env` storage;
there is **no data migration or new provider type**.

| Existing provider type | Web Interface | User Provisioning |
|---|---|---|
| `oidc` | OIDC login (and claims cached on verified sign-in) | No directory enumeration or scheduled synchronization |
| `ldap` | LDAP password authentication | LDAP/Active Directory lookup and directory enumeration |
| `authentik` | Optional OIDC login, if OIDC settings and client secret are configured | Authentik API profile lookup and directory enumeration |

LDAP and Authentik can appear in **both** tables because they support both
capabilities. This does **not** create a second provider. Edit/Delete operate on
the same existing identifier; deleting a provider removes it from both tables
and remains blocked while any domain references it. The provider slug is
retained for storage, domain assignments and callback URL paths but is no
longer printed under its display name.

The two editors expose only settings needed for the selected capability.
For an Authentik provider, the provisioning editor contains Authentik API
settings; the web editor contains its optional OIDC settings. For LDAP,
the same connection and bind settings are legitimately required for both
password login and directory lookup. Non-secret settings from the other
capability are carried through unchanged on save. Empty password fields
preserve existing credentials via `IdentityProviderRegistry.save()`.
Secrets are never returned by the admin provider-list endpoint; readiness
flags only indicate whether the corresponding credential is configured.

The existing provider endpoints, OIDC callbacks, domain provider assignments,
login rules, LDAP authentication, provisioning and cached OIDC claims remain
unchanged. A legacy Authentik provider that has API credentials but no OIDC
client secret is **not** displayed as ready for web login.

## Managing user overrides

In Domain Management > Users & Overrides, administrators can filter and page
through an **overrides-only** list alongside the existing user directory.

* One **Remove** action is available on each override.
* Selection checkboxes persist across override pagination and search.
* **Select all** explicitly selects **all overrides for the current domain**,
  including hidden pages and filtered-out rows. The selected count always
  reflects the complete selection, not the current page.
* **Remove selected** is disabled when the selection is empty; it is the
  supported way to remove every override. A confirmation names the exact
  number selected and the domain.
* `POST /api/admin/overrides/bulk-delete` accepts
  `{"domain_name":"example.org","ids":[1,2]}`. The server validates
  administrator privileges, the domain, distinct positive IDs and the
  per-record domain scope. It deletes **only** confirmed IDs in one database
  transaction (max. 5,000 per request), then returns
  `deleted_ids`, `not_found_ids` and `out_of_scope_ids`.
  Missing or out-of-domain entries are never removed. On a database commit
  failure the whole batch rolls back.
* The UI reports partial results and refreshes the override and directory
  lists after every attempt. No account, domain, signature, directory cache,
  LDAP/OIDC identity, or provider record is changed.

The signature engine resolves overrides from the database for each request.
There is no effective-override cache to invalidate; after deletion the next
signature resolution uses normal domain-level and signature-rule selection.
The pre-existing single-record DELETE endpoint remains supported for older
clients.

## Verification

Run the following locally or through the **Tests** GitHub Actions workflow:

```bash
python -m unittest tests.test_identity_management -v
node tests/identity_management_smoke.cjs
```

The Python tests cover real SQLite transactions, authorization, a mixed
valid/missing/out-of-domain batch, rollback, effective-signature fallback,
credential redaction/preservation and UI structure. The Node smoke test checks
provider classification and controller-level selection/confirmation/partial
failure behavior across multiple pages and filters.

**Limits:** The override-list endpoint still returns the current domain's
complete override list (including editable HTML). The client paginates it
locally. A very large domain may therefore incur additional response size
and memory use; the bulk-delete endpoint itself is bounded to 5,000 IDs.
The connection-test endpoint remains a provider-wide health check, so a
combined Authentik provider may fail that test if one configured capability
is unhealthy even when the other works.
