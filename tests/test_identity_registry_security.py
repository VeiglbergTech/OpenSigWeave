"""Real filesystem security regressions for identity registry."""
import os
from pathlib import Path
import tempfile
import unittest

from identity.registry import IdentityProviderRegistry, ProviderConfigError


class RegistrySecurityTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.base = Path(self.work.name)
        self.registry = IdentityProviderRegistry(self.base / ".idp")
        self.good = {"AUTHENTIK_URL": "https://example.org", "AUTHENTIK_API_TOKEN": "secret"}

    def test_traversal_absolute_unicode_and_encoded_slugs_fail_for_all_operations(self):
        unsafe = ("../escape", "../a/b", "a/../../etc", "/etc", "C:\\windows",
                  "with space", "UPPER", "a_b", "..", ".", "é", "e\u0301",
                  "%2e%2e%2f", "a\\x00b", "a\\b", "")
        for slug in unsafe:
            with self.subTest(slug=slug):
                for operation in (
                    lambda: self.registry.save(slug, "x", "authentik", self.good),
                    lambda: self.registry.load(slug),
                    lambda: self.registry.describe(slug),
                    lambda: self.registry.delete(slug),
                    lambda: self.registry.get_config(slug),
                ):
                    with self.assertRaises(ProviderConfigError):
                        operation()
        self.assertFalse((self.base / "escape").exists())

    def test_normal_roundtrip_preserves_secrets_and_uses_private_modes(self):
        self.registry.save("test-idp", "Example", "authentik", self.good)
        self.assertEqual(self.registry.get_config("test-idp")["AUTHENTIK_API_TOKEN"], "secret")
        self.registry.save("test-idp", "Updated", "authentik",
                           {"AUTHENTIK_URL": "https://example.org", "AUTHENTIK_API_TOKEN": ""})
        self.assertEqual(self.registry.describe("test-idp")["display_name"], "Updated")
        self.assertEqual(self.registry.list_configs()[0]["id"], "test-idp")
        path = self.base / ".idp" / "test-idp" / ".env"
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
        self.registry.delete("test-idp")
        self.assertFalse(path.parent.exists())

    def test_existing_env_format_is_compatible(self):
        path = self.base / ".idp" / "legacy-idp"
        path.mkdir(parents=True, mode=0o700)
        (path / ".env").write_text('IDP_TYPE="authentik"\n'
                                  'IDP_DISPLAY_NAME="Existing"\n'
                                  'IDP_ENABLED="true"\n'
                                  'AUTHENTIK_URL="https://example.org"\n'
                                  'AUTHENTIK_API_TOKEN="legacy"\n', encoding="utf-8")
        os.chmod(path / ".env", 0o600)
        self.assertEqual(self.registry.describe("legacy-idp")["display_name"], "Existing")
        self.assertEqual(self.registry.get_config("legacy-idp")["AUTHENTIK_API_TOKEN"], "legacy")

    def test_provider_symlink_is_never_read_written_or_deleted(self):
        outside = self.base / "outside"
        outside.mkdir()
        sentinel = outside / ".env"
        sentinel.write_text("secret", encoding="utf-8")
        self.registry.root.mkdir(mode=0o700)
        (self.registry.root / "evil").symlink_to(outside, target_is_directory=True)
        for operation in (
            lambda: self.registry.load("evil"),
            lambda: self.registry.save("evil", "Example", "authentik", self.good),
            lambda: self.registry.delete("evil"),
        ):
            with self.assertRaises((OSError, ProviderConfigError)):
                operation()
        self.assertEqual(sentinel.read_text(), "secret")

    def test_env_symlink_hardlink_and_permissive_files_are_rejected(self):
        self.registry.save("test-idp", "Example", "authentik", self.good)
        original = self.registry.root / "test-idp" / ".env"
        outside = self.base / "outside.env"
        outside.write_text("valuable", encoding="utf-8")
        original.unlink()
        original.symlink_to(outside)
        with self.assertRaises(ProviderConfigError):
            self.registry.load("test-idp")
        with self.assertRaises(ProviderConfigError):
            self.registry.save("test-idp", "Example", "authentik", self.good)
        original.unlink()
        os.link(outside, original)
        with self.assertRaises(ProviderConfigError):
            self.registry.load("test-idp")
        original.unlink()
        original.write_text("IDP_TYPE=authentik", encoding="utf-8")
        os.chmod(original, 0o644)
        with self.assertRaises(ProviderConfigError):
            self.registry.load("test-idp")
        self.assertEqual(outside.read_text(), "valuable")

    def test_root_and_ancestor_symlinks_fail_closed(self):
        other = self.base / "other"
        other.mkdir()
        registry_root = self.base / ".idp"
        registry_root.symlink_to(other, target_is_directory=True)
        with self.assertRaises((OSError, ProviderConfigError)):
            self.registry.save("safe", "Example", "authentik", self.good)
        self.assertEqual(os.listdir(other), [])
        registry_root.unlink()
        parent = self.base / "alias"
        parent.symlink_to(other, target_is_directory=True)
        nested = IdentityProviderRegistry(parent / "idp")
        with self.assertRaises((OSError, ProviderConfigError)):
            nested.save("safe", "Example", "authentik", self.good)

    def test_delete_refuses_unrecognized_files(self):
        self.registry.save("test-idp", "Example", "authentik", self.good)
        directory = self.registry.root / "test-idp"
        (directory / "unexpected").write_text("do not delete", encoding="utf-8")
        with self.assertRaises(ProviderConfigError):
            self.registry.delete("test-idp")
        self.assertEqual((directory / "unexpected").read_text(), "do not delete")
        self.assertTrue((directory / ".env").exists())

    def test_pinned_directory_fd_cannot_be_redirected_by_rename(self):
        self.registry.save("test-idp", "Example", "authentik", self.good)
        original = self.registry.root / "test-idp"
        external = self.base / "external"
        external.mkdir()
        (external / ".env").write_text("IDP_TYPE=oidc\n", encoding="utf-8")
        with self.registry._root_fd() as root_fd:
            with self.registry._provider_fd(root_fd, "test-idp") as provider_fd:
                moved = self.registry.root / "moved"
                original.rename(moved)
                original.symlink_to(external, target_is_directory=True)
                fd = os.open(".env", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=provider_fd)
                with os.fdopen(fd, "r", encoding="utf-8") as stream:
                    self.assertIn("AUTHENTIK_API_TOKEN", stream.read())
                original.unlink()
                moved.rename(original)
        self.assertEqual((external / ".env").read_text(), "IDP_TYPE=oidc\n")


if __name__ == "__main__":
    unittest.main()
