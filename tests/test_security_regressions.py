"""Regression tests for security properties that can be checked without external services.

These tests deliberately inspect the application source. Runtime/API tests will be added
when the application is split into importable services and dependency overrides.
"""
import ast
import pathlib
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
        self.assertIn('startswith(("your_", "replace_"))', self.source)

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
        self.assertIn("X-CSRF-Token", template)
        self.assertIn("csrf_token", self.source)
        self.assertIn('@app.post("/logout")', self.source)
        self.assertNotIn('@app.get("/logout")', self.source)
        self.assertIn("fetch('/logout'", template)

    def test_signature_previews_are_sandboxed_and_js_data_is_serialized(self):
        template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn("|tojson", template)
        self.assertIn('sandbox=""', template)
        self.assertNotIn('x-html="previewHtml"', template)
        self.assertNotIn("{{ domain_template | safe }}", template)
        self.assertNotIn("root.innerHTML =", template)
        self.assertIn("clipboard.dangerouslyPasteHTML", template)

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

    def test_oidc_login_uses_provider_discovery_configuration(self):
        self.assertIn('name="oidc"', self.source)
        self.assertIn('os.environ.get("OIDC_DISCOVERY_URL")', self.source)
        self.assertIn('not oidc_discovery_url.startswith("https://")', self.source)
        self.assertIn("explicit OIDC_REDIRECT_URI ending in /auth", self.source)
        self.assertNotIn("oauth.authentik.", self.source)
        self.assertIn("oauth.oidc.authorize_redirect", self.source)
        self.assertIn("oauth.oidc.authorize_access_token", self.source)

    def test_signature_management_api_routes_require_admin(self):
        self.assertIn('@app.get("/api/admin/signatures")', self.source)
        self.assertIn('@app.post("/api/admin/signatures", status_code=201)', self.source)
        self.assertIn('@app.get("/api/admin/signatures/{signature_id}/versions")', self.source)
        self.assertIn('@app.post("/api/admin/signatures/{signature_id}/versions", status_code=201)', self.source)
        self.assertIn('@app.post("/api/admin/signatures/{signature_id}/variants", status_code=201)', self.source)
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


    def test_rspamd_resolution_uses_persisted_rules_and_explicit_mode_override(self):
        self.assertIn("from core.rule_repository import load_signature_rules", self.source)
        self.assertIn("evaluate_rules(load_signature_rules(db), context)", self.source)
        self.assertIn('alias="X-OpenSigWeave-Mode"', self.source)
        self.assertIn("if override and domain_obj.allow_overrides and not explicit_mode:", self.source)
        self.assertIn("signature_id=signature_id", self.source)


    def test_rspamd_consumes_and_forwards_validated_mode_header(self):
        lua = (ROOT / "rspamd" / "opensigweave.lua").read_text(encoding="utf-8")
        self.assertIn("task:get_header_count('X-OpenSigWeave-Mode')", lua)
        self.assertIn("task:modify_header('X-OpenSigWeave-Mode', { remove = { 0 } })", lua)
        self.assertIn("request_headers['X-OpenSigWeave-Mode'] = requested_mode", lua)
        self.assertIn("candidate == 'INTERNAL' or candidate == 'NONE'", lua)

    def test_rspamd_outlook_marker_is_decoded_and_removed(self):
        lua = (ROOT / "rspamd" / "opensigweave.lua").read_text(encoding="utf-8")
        self.assertIn('task:get_text_parts()', lua)
        self.assertIn('part:get_content("raw_utf")', lua)
        self.assertIn("OpenSigWeave:MODE=", lua)
        self.assertIn("data%-opensigweave%-mode", lua)
        self.assertIn("remove_outlook_mode_markers(decoded)", lua)
        self.assertIn("find_outlook_mode_marker(task, is_reply)", lua)
        self.assertIn("if not requested_mode then", lua)

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
        start = lua.index("local function remove_outlook_mode_markers")
        end = lua.index("\nend", start)
        remover = lua[start:end]
        self.assertIn('data%-opensigweave%-mode', remover)
        self.assertIn('html = html:gsub("%s+data%-opensigweave%-mode', remover)
        self.assertNotIn('>.-</', remover)

    def test_rspamd_fails_closed_for_multipart_until_safe_rewriter_exists(self):
        lua = (ROOT / "rspamd" / "opensigweave.lua").read_text(encoding="utf-8")
        guard = lua.index('if is_multipart then')
        warning = lua.index('multipart message left unchanged', guard)
        early_return = lua.index('return', warning)
        raw_rewriter = lua.index('process_mime_block(raw_body, "html")')
        self.assertLess(guard, warning)
        self.assertLess(warning, early_return)
        self.assertLess(early_return, raw_rewriter)


    def test_templates_do_not_use_inline_scripts_or_event_handlers(self):
        for name in ("index.html", "error.html"):
            template = (ROOT / "templates" / name).read_text(encoding="utf-8")
            self.assertNotRegex(template, r"<script(?![^>]*\\bsrc=)[^>]*>")
            self.assertNotRegex(template, r"\\bon[a-z]+\\s*=")
        index = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn("/static/js/portal.js", index)
        self.assertIn("/static/css/portal.css", index)
        self.assertIn("data-csrf-token", index)
        self.assertIn("x-cloak", index)
        self.assertTrue((ROOT / "static" / "js" / "portal.js").is_file())
        self.assertTrue((ROOT / "static" / "css" / "portal.css").is_file())



if __name__ == "__main__":
    unittest.main()


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
