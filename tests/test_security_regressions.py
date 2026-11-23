"""Regression tests for security properties that can be checked without external services.

These tests deliberately inspect the application source. Runtime/API tests will be added
when the application is split into importable services and dependency overrides.
"""
import ast
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
MAIN = ROOT / "main.py"


class SecurityRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = MAIN.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_domain_crud_routes_require_admin(self):
        expected = {
            "read_domains",
            "create_domain",
            "update_domain",
            "delete_domain",
        }
        found = {
            node.name: node
            for node in self.tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertTrue(expected.issubset(found))
        for name in expected:
            with self.subTest(route=name):
                function_source = ast.get_source_segment(self.source, found[name])
                self.assertIn("is_admin_user(request)", function_source)
                self.assertIn('detail="Admins only"', function_source)

    def test_session_secret_has_no_insecure_fallback(self):
        self.assertIn('"fallback-secret"', self.source)
        self.assertIn("at least 32 non-placeholder characters", self.source)
        self.assertIn("replace_with_random_secret_at_least_32_characters", self.source)
        self.assertIn('ENGINE_API_KEY.lower().startswith(("replace_", "changeme", "your_"))', self.source)

    def test_authentik_http_client_does_not_disable_tls_validation(self):
        provider_source = (ROOT / "identity" / "providers" / "authentik.py").read_text(encoding="utf-8")
        self.assertNotIn("verify=False", provider_source)
        self.assertIn("verify=True", provider_source)


    def test_signature_templates_use_sandboxed_autoescaping(self):
        renderer = (ROOT / "signature_renderer.py").read_text(encoding="utf-8")
        self.assertIn("SandboxedEnvironment(autoescape=True)", renderer)
        self.assertIn('filters.pop("safe", None)', renderer)
        self.assertIn("from signature_renderer import render_signature_template", self.source)
        self.assertNotIn("re.sub(", self.source)

    def test_authentik_lookup_uses_encoded_query_parameters(self):
        provider_source = (ROOT / "identity" / "providers" / "authentik.py").read_text(encoding="utf-8")
        self.assertIn('params={"search": email}', provider_source)
        self.assertNotIn("?search={email}", provider_source)

    def test_database_configuration_is_separate_and_url_driven(self):
        database_source = (ROOT / "database.py").read_text(encoding="utf-8")
        self.assertIn('os.environ.get("DATABASE_URL"', database_source)
        self.assertIn("pool_pre_ping=True", database_source)
        self.assertIn('DATABASE_URL.startswith("sqlite")', database_source)
        self.assertIn("from database import get_db", self.source)
        self.assertIn("from models import DomainDB, UserOverrideDB", self.source)
        self.assertNotIn("Base.metadata.create_all", self.source)
        self.assertTrue((ROOT / "migrations" / "versions" / "0001_initial_schema.py").exists())

    def test_state_changing_browser_requests_require_csrf_token(self):
        self.assertIn("class CSRFProtectionMiddleware", self.source)
        self.assertIn('{"POST", "PUT", "PATCH", "DELETE"}', self.source)
        self.assertIn("hmac.compare_digest(expected, supplied)", self.source)
        template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        portal_js = (ROOT / "static" / "js" / "portal.js").read_text(encoding="utf-8")
        self.assertIn("data-csrf-token", template)
        self.assertIn("X-CSRF-Token", portal_js)
        self.assertIn("csrf_token", self.source)
        self.assertIn('@app.post("/logout")', self.source)
        self.assertNotIn('@app.get("/logout")', self.source)
        self.assertIn('fetch("/logout"', (ROOT / "static" / "js" / "logout.js").read_text(encoding="utf-8"))

    def test_html_import_uses_dom_nodes_instead_of_regex_tag_filter(self):
        portal = (ROOT / "static" / "js" / "portal.js").read_text(encoding="utf-8")
        self.assertIn("doc.querySelectorAll('script')", portal)
        self.assertNotIn("cleanHtml.replace(", portal)

    def test_admin_logs_do_not_interpolate_untrusted_provider_ids(self):
        self.assertNotIn("provider %s (%s)", self.source)
        self.assertNotIn("Identity provider test failed for %s", self.source)

    def test_signature_previews_are_sandboxed_and_js_data_is_serialized(self):
        template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn("|tojson", template)
        self.assertIn('sandbox=""', template)
        self.assertNotIn('x-html="previewHtml"', template)
        self.assertNotIn("{{ domain_template | safe }}", template)
        portal_js = (ROOT / "static" / "js" / "portal.js").read_text(encoding="utf-8")
        self.assertNotIn("root.innerHTML =", portal_js)
        self.assertIn("window.OpenSigWeaveEditor", portal_js)
        editor_adapter = (ROOT / "static" / "js" / "rich-editor.js").read_text(encoding="utf-8")
        self.assertIn("ClassicEditor.create", editor_adapter)
        self.assertIn("editor.getData()", editor_adapter)
        self.assertNotIn("clipboard.dangerouslyPasteHTML", portal_js)

    def test_rspamd_credentials_are_external_and_http_is_bounded(self):
        lua = (ROOT / "rspamd" / "opensigweave.lua").read_text(encoding="utf-8")
        self.assertIn('get_all_opt("opensigweave")', lua)
        self.assertNotIn('local ENGINE_API_KEY = "', lua)
        self.assertIn('ENGINE_API_KEY:match("^[Rr][Ee][Pp][Ll][Aa][Cc][Ee]_")', lua)
        self.assertIn("timeout = REQUEST_TIMEOUT", lua)
        self.assertIn("max_size = MAX_RESPONSE_SIZE", lua)
        self.assertIn("no_ssl_verify = false", lua)
        self.assertIn("url_encode(sender_email)", lua)
        self.assertTrue((ROOT / "rspamd" / "opensigweave.conf.example").exists())

    def test_identity_attribute_lookup_is_behind_provider_interface(self):
        self.assertIn("AuthentikAttributeProvider", self.source)
        self.assertNotIn("httpx.AsyncClient", self.source)
        provider_source = (ROOT / "identity" / "providers" / "authentik.py").read_text(encoding="utf-8")
        self.assertIn("class AuthentikAttributeProvider", provider_source)
        self.assertIn('params={"search": email}', provider_source)
        protocol_source = (ROOT / "identity" / "providers" / "base.py").read_text(encoding="utf-8")
        self.assertIn("class AttributeProvider(Protocol)", protocol_source)

    def test_oidc_login_uses_managed_provider_configuration_only(self):
        self.assertIn('client = OAuth()', self.source)
        self.assertIn('server_metadata_url=config["OIDC_DISCOVERY_URL"]', self.source)
        self.assertIn('client_id=config["OIDC_CLIENT_ID"]', self.source)
        self.assertIn('app.add_api_route("/auth/{provider_id}"', self.source)
        self.assertNotIn("pending_legacy_login_email", self.source)
        self.assertNotIn("OIDC_ENABLED", self.source)
        self.assertNotIn('app.add_api_route("/auth",', self.source)
        self.assertIn("return await client.authorize_redirect", self.source)
        self.assertIn("await client.authorize_access_token(request)", self.source)

    def test_branding_and_scheduling_routes_require_admin(self):
        for route in (
            '@app.get("/api/admin/branding")',
            '@app.put("/api/admin/branding")',
            '@app.get("/api/admin/schedules")',
            '@app.post("/api/admin/schedules", status_code=201)',
            '@app.put("/api/admin/schedules/{schedule_id}")',
            '@app.delete("/api/admin/schedules/{schedule_id}")',
        ):
            self.assertIn(route, self.source)
        self.assertIn("class BrandingSettingsDB", (ROOT / "models.py").read_text(encoding="utf-8"))
        self.assertIn("class SignatureScheduleDB", (ROOT / "models.py").read_text(encoding="utf-8"))

    def test_release_branding_asset_is_packaged_and_upload_budget_is_bounded(self):
        # Three optional 12 MiB images require a larger but bounded base64 request.
        self.assertIn('"/api/admin/branding": 51 * 1024 * 1024', self.source)
        self.assertIn('large_image_uploads', self.source)
        self.assertIn('DEFAULT_SIGNATURE_IMAGE_BYTES', self.source)
        dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertNotIn("branding/logo.png", dockerignore)
        self.assertNotIn("branding/logo.png", gitignore)
        logo = (ROOT / "branding" / "logo.png").read_bytes()
        self.assertTrue(logo.startswith(b"\x89PNG\r\n\x1a\n"))
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("FROM node:22.23.5-alpine3.24 AS ckeditor", dockerfile)
        self.assertNotIn("FROM node:22-alpine AS ckeditor", dockerfile)
        for obsolete in (
            ROOT / "branding" / "logo.png.example",
            ROOT / "branding" / "favicon.ico.example",
            ROOT / "branding" / "settings.json.example",
            ROOT / "docs" / "EDITOR_MIGRATION_PLAN.md",
        ):
            self.assertFalse(obsolete.exists(), f"Obsolete release artifact still exists: {obsolete}")

    def test_signature_management_api_routes_require_admin(self):
        self.assertIn('@app.get("/api/admin/signatures")', self.source)
        self.assertIn('@app.post("/api/admin/signatures", status_code=201)', self.source)
        self.assertIn('@app.get("/api/admin/signatures/{signature_id}/versions")', self.source)
        self.assertIn('@app.post("/api/admin/signatures/{signature_id}/versions", status_code=201)', self.source)
        self.assertGreaterEqual(self.source.count('raise HTTPException(status_code=403, detail="Admins only")'), 5)

    def test_hybrid_storage_uses_immutable_files_and_metadata(self):
        storage = (ROOT / "signature_storage.py").read_text(encoding="utf-8")
        models = (ROOT / "models.py").read_text(encoding="utf-8")
        self.assertIn("os.link(temp_path", storage)
        self.assertIn("hmac.compare_digest", storage)
        self.assertIn("hashlib.sha256", storage)
        self.assertIn("class SignatureVersionDB", models)
        self.assertIn("class SignatureVariantDB", models)
        self.assertIn("class SelectionStateDB", models)
        self.assertTrue((ROOT / "migrations" / "versions" / "0002_signature_assets.py").exists())

    def test_postgresql_is_recommended_and_placeholder_password_is_rejected(self):
        env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
        database_source = (ROOT / "database.py").read_text(encoding="utf-8")
        self.assertIn('DATABASE_URL="postgresql+psycopg://', env_example)
        self.assertIn('postgres_password.lower().startswith(("replace_", "changeme"))', database_source)
        self.assertIn("make_url(DATABASE_URL).password != postgres_password", database_source)
        self.assertTrue((ROOT / "compose.yaml").exists())


    def test_engine_uses_current_rules_without_legacy_mode_selection(self):
        service_source = (ROOT / "core" / "engine_service.py").read_text(encoding="utf-8")
        self.assertIn("class SignatureEngineService", service_source)
        self.assertIn("from core.rule_repository import load_signature_rules", service_source)
        self.assertIn("evaluate_rules(load_signature_rules(db), context)", service_source)
        self.assertNotIn("select_and_record_variant", service_source)
        self.assertNotIn("explicit_mode", service_source)
        self.assertNotIn("SignatureMode", service_source)

    def test_rspamd_only_strips_deprecated_mode_selector(self):
        lua = (ROOT / "rspamd" / "opensigweave.lua").read_text(encoding="utf-8")
        self.assertIn("task:get_header_count('X-OpenSigWeave-Mode')", lua)
        self.assertIn("task:modify_header('X-OpenSigWeave-Mode', { remove = { 0 } })", lua)
        self.assertNotIn("request_headers['X-OpenSigWeave-Mode']", lua)
        self.assertNotIn("requested_mode", lua)
        self.assertIn("local signature_marker = find_signature_text_marker(task, has_quoted_history)", lua)
        self.assertIn("remove_text_marker_from_message()", lua)

    def test_rspamd_deprecated_html_markers_are_cleanup_only(self):
        lua = (ROOT / "rspamd" / "opensigweave.lua").read_text(encoding="utf-8")
        self.assertIn("OpenSigWeave:MODE=", lua)
        self.assertIn("data%-opensigweave%-mode", lua)
        self.assertIn("remove_deprecated_selector_markers(decoded)", lua)
        self.assertNotIn("find_outlook_mode_marker", lua)
        self.assertNotIn("requested_mode", lua)

    def test_domain_routes_receive_request_context(self):
        for name in ("read_domains", "create_domain", "update_domain", "delete_domain"):
            node = next(
                item for item in self.tree.body
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                and item.name == name
            )
            args = [arg.arg for arg in node.args.args]
            self.assertIn("request", args, name)

    def test_rspamd_marker_removal_preserves_the_marked_html_element(self):
        lua = (ROOT / "rspamd" / "opensigweave.lua").read_text(encoding="utf-8")
        start = lua.index("local function remove_deprecated_selector_markers")
        end = lua.index("\nend", start)
        remover = lua[start:end]
        self.assertIn('data%-opensigweave%-mode', remover)
        self.assertIn('html = html:gsub("%s+data%-opensigweave%-mode', remover)
        self.assertNotIn('>.-</', remover)

    def test_signature_storage_uses_named_signature_directories(self):
        storage = (ROOT / "signature_storage.py").read_text(encoding="utf-8")
        catalog = (ROOT / "signature_catalog.py").read_text(encoding="utf-8")
        self.assertIn('self.signatures_root = self.root / "signatur"', storage)
        self.assertIn('"signature.html"', storage)
        self.assertIn('".versions"', storage)
        self.assertIn("store.rename_signature(old_name, new_name)", catalog)
        self.assertIn("version.relative_path.replace(", catalog)

    def test_rspamd_removes_incomplete_html_escaped_and_quoted_printable_markers(self):
        lua = (ROOT / "rspamd" / "opensigweave.lua").read_text(encoding="utf-8")
        self.assertIn('&[lL][tT];%-%-%-%[', lua)
        self.assertIn('=3[Cc]%-%-%-%[', lua)
        self.assertIn('local quoted_printable = signature_marker.raw:gsub("<", "=3C")', lua)

    def test_rspamd_multipart_parser_rewrites_only_safe_text_parts(self):
        lua = (ROOT / "rspamd" / "opensigweave.lua").read_text(encoding="utf-8")
        mime = (ROOT / "rspamd" / "opensigweave_mime.lua").read_text(encoding="utf-8")
        self.assertIn('pcall(mime_rewriter.rewrite, task, function(decoded, is_html)', lua)
        self.assertIn('task:get_parts()', mime)
        self.assertIn('part:is_attachment()', mime)
        self.assertIn('signed, encrypted or embedded message', mime)
        self.assertIn('message exceeds MIME rewrite limit', mime)
        self.assertNotIn('process_mime_block(raw_body, "html")', lua)


    def test_templates_do_not_use_inline_scripts_or_event_handlers(self):
        for name in ("index.html", "error.html"):
            template = (ROOT / "templates" / name).read_text(encoding="utf-8")
            self.assertNotRegex(template, r"<script(?![^>]*\bsrc=)[^>]*>")
            self.assertNotRegex(template, r"\bon[a-z]+\s*=")
        index = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn("/static/js/portal.js", index)
        self.assertIn("/static/css/portal.css", index)
        self.assertIn("data-csrf-token", index)
        self.assertIn("x-cloak", index)
        self.assertTrue((ROOT / "static" / "js" / "portal.js").is_file())
        self.assertTrue((ROOT / "static" / "js" / "branding.js").is_file())
        login = (ROOT / "templates" / "login.html").read_text(encoding="utf-8")
        self.assertIn("/static/js/branding.js", index)
        self.assertIn("/static/js/branding.js", login)
        self.assertIn("/branding/logo.png", login)
        self.assertIn("/branding/favicon", login)
        self.assertIn("/static/js/logout.js", (ROOT / "templates" / "error.html").read_text(encoding="utf-8"))
        self.assertTrue((ROOT / "static" / "js" / "logout.js").is_file())
        self.assertTrue((ROOT / "static" / "css" / "portal.css").is_file())



    def test_frontend_is_prepared_for_alpine_csp_evaluator(self):
        template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        portal_js = (ROOT / "static" / "js" / "portal.js").read_text(encoding="utf-8")
        expressions = re.findall(
            r'(?:@[\w.:-]+|x-[\w.:-]+|:[\w.:-]+)="([^"]*)"',
            template,
            flags=re.DOTALL,
        )
        for expression in expressions:
            with self.subTest(expression=expression[:100]):
                self.assertNotRegex(expression, r"=>|;|\bif\s*\(|\?\.")
        self.assertNotIn("x-bind:srcdoc", template)
        for component in ("toastManager", "sanitizerModal", "previewModal", "addDomainModal", "confirmModal"):
            self.assertIn(f"function {component}()", portal_js)
        self.assertIn("get matchingSignatures()", portal_js)
        self.assertIn('Alpine.data("ruleManager", ruleManager)', portal_js)
        self.assertIn('Alpine.data("previewModal", previewModal)', portal_js)
        self.assertIn('Alpine.data("domainSettingsModal", domainSettingsModal)', portal_js)

    def test_content_security_policy_is_report_only_until_browser_migration_is_complete(self):
        self.assertIn("Content-Security-Policy-Report-Only", self.source)
        self.assertNotIn('"Content-Security-Policy"', self.source)
        self.assertIn("script-src-attr 'none'", self.source)
        self.assertIn("style-src-attr 'none'", self.source)
        self.assertNotIn("'unsafe-inline'", self.source)
        self.assertNotIn("'unsafe-eval'", self.source)



    def test_sensitive_routes_use_shared_database_rate_limiting(self):
        self.assertIn("from rate_limiter import RateLimitMiddleware", self.source)
        self.assertIn("app.add_middleware(RateLimitMiddleware)", self.source)
        limiter = (ROOT / "rate_limiter.py").read_text(encoding="utf-8")
        self.assertIn("on_conflict_do_update", limiter)
        self.assertIn("postgres_insert", limiter)
        self.assertIn("sqlite_insert", limiter)
        self.assertIn("request.client.host", limiter)
        self.assertIn("TRUSTED_PROXY_CIDRS", limiter)
        self.assertIn("status_code=429", limiter)
        self.assertIn("status_code=503", limiter)



    def test_remote_reverse_proxy_requires_private_bind_and_firewall_boundary(self):
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        architecture = (ROOT / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")

        self.assertIn('${APP_BIND_ADDRESS:-127.0.0.1}:${APP_PORT:-8085}:8085', compose)
        self.assertIn('APP_PORT="8085"', env_example)
        self.assertIn('APP_BIND_ADDRESS="127.0.0.1"', env_example)
        self.assertIn("private-interface IP", readme)
        self.assertIn("only from the reverse-proxy private IP", readme)
        self.assertIn("does not make the proxy network boundary for you", readme)
        self.assertIn("TRUSTED_PROXY_CIDRS", architecture)

        postgres_service = compose.split("  postgres:", 1)[1].split("  opensigweave:", 1)[0]
        self.assertNotIn("\n    ports:", postgres_service)

    def test_forwarded_client_ip_is_ignored_for_untrusted_direct_peers(self):
        from unittest.mock import patch
        import ipaddress
        from starlette.requests import Request
        from rate_limiter import client_identity

        request = Request({
            "type": "http", "method": "GET", "path": "/",
            "headers": [(b"x-forwarded-for", b"198.51.100.99")],
            "client": ("10.20.30.40", 54321),
            "server": ("app", 8085), "scheme": "http", "query_string": b"",
        })
        with patch("rate_limiter.TRUSTED_PROXY_NETWORKS", (ipaddress.ip_network("10.0.0.0/24"),)):
            self.assertEqual(client_identity(request), "10.20.30.40")

    def test_only_ckeditor_is_shipped_as_rich_text_editor(self):
        template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        portal_css = (ROOT / "static" / "css" / "portal.css").read_text(encoding="utf-8")
        editor_adapter = (ROOT / "static" / "js" / "rich-editor.js").read_text(encoding="utf-8")
        self.assertIn("/static/vendor/ckeditor5/49.1.0/ckeditor5.js", template)
        self.assertIn("ClassicEditor.create", editor_adapter)
        self.assertNotIn("quill", template.lower())
        self.assertNotIn(".ql-", portal_css)
        self.assertFalse((ROOT / "static" / "vendor" / "quill").exists())

    def test_portal_uses_charcoal_red_orange_brand_palette(self):
        portal_css = (ROOT / "static" / "css" / "portal.css").read_text(encoding="utf-8")
        for token in ("--osw-black", "--osw-charcoal", "--osw-red", "--osw-orange"):
            self.assertIn(token, portal_css)

if __name__ == "__main__":
    unittest.main()

