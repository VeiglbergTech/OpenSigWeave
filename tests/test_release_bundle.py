"""Offline release-package integrity, host-file scope and Gitless identity tests."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from scripts import release_bundle as bundle
from scripts import deploy_common


class MinimalReleaseBundleTests(unittest.TestCase):
    VERSION = "2026.11"
    SHA = "a" * 40

    def _source(self, root):
        for path in bundle.FILES:
            location = root / path
            location.parent.mkdir(parents=True, exist_ok=True)
            location.write_text(f"# bundled {path}\n", encoding="utf-8")

    def test_build_verifies_exact_allowlist_and_integrity(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work) / "source"
            root.mkdir()
            self._source(root)
            output = Path(work) / "bundle.tar.gz"
            bundle.build(root, self.VERSION, self.SHA, output)
            self.assertLess(output.stat().st_size, bundle.MAX_ARCHIVE_BYTES)
            twin = Path(work) / "repeat.tar.gz"
            bundle.build(root, self.VERSION, self.SHA, twin)
            self.assertEqual(output.read_bytes(), twin.read_bytes(), "Release archive must be deterministic")
            with tarfile.open(output) as archive:
                names = {m.name for m in archive.getmembers()}
                self.assertEqual(names, set(bundle.FILES) | {bundle.MANIFEST})
                self.assertFalse(any("test" in n or ".git" in n or ".env.example" == n + ".bad" for n in names))
                target = Path(work) / "installed"
                target.mkdir()
                for member in archive:
                    item = target / member.name
                    item.parent.mkdir(parents=True, exist_ok=True)
                    item.write_bytes(archive.extractfile(member).read())
            self.assertEqual(bundle.read_manifest(target)["revision"], self.SHA)
            (target / "compose.yaml").write_text("tampered", encoding="utf-8")
            with self.assertRaises(ValueError):
                bundle.read_manifest(target)

    def test_install_files_preserves_local_data_secrets_and_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work) / "source"
            root.mkdir()
            self._source(root)
            bundle_path = Path(work) / "release.tar.gz"
            bundle.build(root, self.VERSION, self.SHA, bundle_path)
            staged = Path(work) / "staging"
            staged.mkdir()
            with tarfile.open(bundle_path) as archive:
                for member in archive:
                    dest = staged / member.name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(archive.extractfile(member).read())
            live = Path(work) / "live"
            live.mkdir()
            secret = live / ".env"
            secret.write_text("KEEP_TOKEN=unchanged\n", encoding="utf-8")
            data = live / "signatures"
            data.mkdir()
            (data / "signature.html").write_text("keep", encoding="utf-8")
            bundle.install_files(staged, live)
            self.assertEqual(secret.read_text(), "KEEP_TOKEN=unchanged\n")
            self.assertEqual((data / "signature.html").read_text(), "keep")
            self.assertEqual(bundle.read_manifest(live)["tag"], self.VERSION)
            (live / "install.sh").unlink()
            (live / "install.sh").symlink_to(secret)
            with self.assertRaises(ValueError):
                bundle.install_files(staged, live)
            self.assertEqual(secret.read_text(), "KEEP_TOKEN=unchanged\n")

    def test_verified_bundle_requires_expected_api_asset_digest_and_revision(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work) / "source"
            root.mkdir()
            self._source(root)
            output = Path(work) / "archive.tar.gz"
            bundle.build(root, self.VERSION, self.SHA, output)
            raw = output.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            release = {
                "tag_name": self.VERSION, "draft": False, "prerelease": False,
                "assets": [{
                    "name": bundle.asset_name(self.VERSION),
                    "digest": "sha256:" + digest,
                    "browser_download_url": "https://github.com/VeiglbergTech/OpenSigWeave/releases/download/"
                        + self.VERSION + "/" + bundle.asset_name(self.VERSION),
                }]
            }
            with patch.object(bundle, "release_commit_sha", return_value=self.SHA), \
                 patch.object(bundle, "_api", return_value=release), \
                 patch.object(bundle, "_download", return_value=raw):
                staging, manifest = bundle.fetch_verified_bundle(self.VERSION, Path(work) / "extract")
                self.assertEqual(manifest["revision"], self.SHA)
                self.assertTrue((staging / "install.sh").exists())
            release["assets"][0]["digest"] = "sha256:" + "0" * 64
            with patch.object(bundle, "release_commit_sha", return_value=self.SHA), \
                 patch.object(bundle, "_api", return_value=release), \
                 patch.object(bundle, "_download", return_value=raw):
                with self.assertRaises(ValueError):
                    bundle.fetch_verified_bundle(self.VERSION, Path(work) / "reject")

    def test_stable_only_rejects_alpha_and_malformed_publication(self):
        for tag in ("2026.11-alpha", "2026.11-beta1", "latest", "../2026.11"):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                bundle.asset_name(tag)
        with patch.object(bundle, "_api", return_value={
            "draft": False, "prerelease": True, "tag_name": self.VERSION,
            "published_at": "2026-11-28T00:00:00Z",
        }), self.assertRaises(ValueError):
            bundle.release_commit_sha(self.VERSION)

    def test_missing_asset_digest_revision_and_malicious_archive_rejected(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work) / "src"
            root.mkdir()
            self._source(root)
            output = Path(work) / "good.tar.gz"
            bundle.build(root, self.VERSION, self.SHA, output)
            raw = output.read_bytes()
            url = "https://github.com/VeiglbergTech/OpenSigWeave/releases/download/" + self.VERSION + "/" + bundle.asset_name(self.VERSION)
            release = {
                "draft": False, "prerelease": False, "tag_name": self.VERSION,
                "assets": [{
                    "name": bundle.asset_name(self.VERSION), "digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
                    "browser_download_url": url,
                }]
            }
            with patch.object(bundle, "release_commit_sha", return_value=self.SHA), patch.object(bundle, "_api", return_value=release), patch.object(bundle, "_download", return_value=raw):
                self.assertEqual(bundle.fetch_verified_bundle(self.VERSION, Path(work) / "good")[1]["tag"], self.VERSION)
            for reason, changes in (
                ("missing asset", {"assets": []}),
                ("missing digest", {"assets": [{**release["assets"][0], "digest": None}]}),
                ("incorrect revision", {}),
            ):
                with self.subTest(reason=reason), patch.object(bundle, "release_commit_sha", return_value=("b" * 40 if reason == "incorrect revision" else self.SHA)), patch.object(bundle, "_api", return_value={**release, **changes}), patch.object(bundle, "_download", return_value=raw), self.assertRaises(ValueError):
                    bundle.fetch_verified_bundle(self.VERSION, Path(work) / reason.replace(" ", "-"))
            # The digest can match an attacker-supplied tar and still must NOT
            # permit traversal, links or unexpected archive members.
            for member_name, kind in (("../outside", "file"), ("install.sh", "symlink"), ("unexpected.py", "file")):
                with self.subTest(member_name=member_name, kind=kind):
                    modified = io.BytesIO()
                    with tarfile.open(fileobj=modified, mode="w:gz") as archive:
                        for path in (*bundle.FILES, bundle.MANIFEST):
                            item = tarfile.TarInfo(member_name if path == "install.sh" else path)
                            if kind == "symlink" and path == "install.sh":
                                item.type = tarfile.SYMTYPE
                                item.linkname = "/etc/passwd"
                                archive.addfile(item)
                                continue
                            content = (root / path).read_bytes() if path in bundle.FILES else b"{}"
                            item.size = len(content)
                            archive.addfile(item, io.BytesIO(content))
                    corrupt = modified.getvalue()
                    fake = {**release, "assets": [{**release["assets"][0], "digest": "sha256:" + hashlib.sha256(corrupt).hexdigest()}]}
                    with patch.object(bundle, "release_commit_sha", return_value=self.SHA), patch.object(bundle, "_api", return_value=fake), patch.object(bundle, "_download", return_value=corrupt), self.assertRaises(ValueError):
                        bundle.fetch_verified_bundle(self.VERSION, Path(work) / "bad")

    def test_install_files_rejects_parent_directory_symlink_before_writing(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work) / "source"
            root.mkdir()
            self._source(root)
            archive = Path(work) / "release.tar.gz"
            bundle.build(root, self.VERSION, self.SHA, archive)
            staging = Path(work) / "staging"
            staging.mkdir()
            with tarfile.open(archive) as tar:
                for member in tar:
                    destination = staging / member.name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(tar.extractfile(member).read())
            target = Path(work) / "deployment"
            target.mkdir()
            external = Path(work) / "external"
            external.mkdir()
            (target / "scripts").symlink_to(external, target_is_directory=True)
            with self.assertRaises(ValueError):
                bundle.install_files(staging, target)
            self.assertEqual(list(external.iterdir()), [])
            self.assertFalse((target / "install.sh").exists())

    def test_actual_repository_package_is_small_and_host_side_stdlib_only(self):
        # Use the real project file set, not just generated fake files.
        repository = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as work:
            output = Path(work) / "bundle.tar.gz"
            bundle.build(repository, self.VERSION, self.SHA, output)
            self.assertLess(output.stat().st_size, bundle.MAX_ARCHIVE_BYTES)
            extracted = Path(work) / "real-package"
            extracted.mkdir()
            with tarfile.open(output) as archive:
                self.assertEqual({m.name for m in archive}, set(bundle.FILES) | {bundle.MANIFEST})
                for member in archive:
                    path = extracted / member.name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(archive.extractfile(member).read())
            self.assertEqual(bundle.read_manifest(extracted)["revision"], self.SHA)
            services = (extracted / "compose.yaml").read_text().split("volumes:\\n", 1)[0]
            self.assertEqual(set(__import__("re").findall(r"(?m)^  ([a-z0-9-]+):$", services)),
                             {"postgres", "opensigweave"})
            # Python isolated mode with site-packages disabled: package host
            # modules must import without extra pip-installed dependencies.
            imports = (
                f"import sys; sys.path[:0] = [{str(extracted / 'scripts')!r}, {str(extracted)!r}]; "
                "import release_bundle, release_target, deploy_common, config_manager, "
                "install_manager, update_manager, auth_config"
            )
            process = subprocess.run(
                [sys.executable, "-I", "-S", "-c", imports],
                capture_output=True, text=True, timeout=30
            )
            self.assertEqual(process.returncode, 0, process.stderr)

    def test_bundle_rejects_symlinked_archive_download_directory(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work) / "source"
            root.mkdir()
            self._source(root)
            output = Path(work) / "good.tar.gz"
            bundle.build(root, self.VERSION, self.SHA, output)
            raw = output.read_bytes()
            release = {
                "draft": False, "prerelease": False, "tag_name": self.VERSION,
                "assets": [{
                    "name": bundle.asset_name(self.VERSION),
                    "digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
                    "browser_download_url": "https://github.com/VeiglbergTech/OpenSigWeave/releases/download/"
                        + self.VERSION + "/" + bundle.asset_name(self.VERSION),
                }],
            }
            external = Path(work) / "external"
            external.mkdir()
            link = Path(work) / "redirect"
            link.symlink_to(external, target_is_directory=True)
            with patch.object(bundle, "release_commit_sha", return_value=self.SHA), patch.object(bundle, "_api", return_value=release), patch.object(bundle, "_download", return_value=raw), self.assertRaises(ValueError):
                bundle.fetch_verified_bundle(self.VERSION, link)
            self.assertEqual(list(external.iterdir()), [])

    def test_bundle_without_git_is_recognized_but_unverified_changes_rejected(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            self._source(root)
            bundle_path = root / "release.tar.gz"
            bundle.build(root, self.VERSION, self.SHA, bundle_path)
            with tarfile.open(bundle_path) as archive:
                member = archive.extractfile(bundle.MANIFEST)
                (root / bundle.MANIFEST).write_bytes(member.read())
            with patch.object(deploy_common, "ROOT", root):
                self.assertTrue(deploy_common.bundle_mode())
                self.assertEqual(deploy_common.current_channel(), "main")
                self.assertEqual(deploy_common.current_sha(), self.SHA)
                deploy_common.pristine()
                (root / "install.sh").write_text("# malicious", encoding="utf-8")
                with self.assertRaises(deploy_common.DeploymentError):
                    deploy_common.pristine()


if __name__ == "__main__":
    unittest.main()
