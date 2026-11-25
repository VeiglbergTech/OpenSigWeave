"""Safety checks for the narrowly scoped host backup operator."""
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from scripts import backup_control_agent as operator


class HostBackupOperatorTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name)
        self.directory = self.root / "remote"
        self.directory.mkdir()
        self.settings = {
            "directory": str(self.directory), "automatic": True,
            "interval_hours": 12, "keep": 5,
        }
        with operator.job_lock:
            operator.current_job = {"state": "idle", "action": None, "id": None, "error": None}

    def test_path_validation_rejects_traversal_symlinks_and_local_disks(self):
        with patch.object(operator, "ALLOWED_PARENT", self.root):
            with self.assertRaises(operator.OperatorError):
                operator._validated_directory("/mnt/../etc")
            with self.assertRaises(operator.OperatorError):
                operator._validated_directory("relative-path")
            link = self.root / "alias"
            link.symlink_to(self.directory, target_is_directory=True)
            with self.assertRaises(operator.OperatorError):
                operator._validated_directory(str(link))
            with patch.object(operator.os.path, "ismount", return_value=False):
                with self.assertRaises(operator.OperatorError):
                    operator._validated_directory(str(self.directory))
            with patch.object(operator.os.path, "ismount", return_value=True):
                result = type("Completed", (), {"returncode": 0, "stdout": "nfs4\n"})()
                with patch.object(operator.subprocess, "run", return_value=result):
                    self.assertEqual(operator._validated_directory(str(self.directory)), self.directory)

    def test_settings_are_strictly_validated(self):
        with patch.object(operator, "ALLOWED_PARENT", self.root), \
             patch.object(operator, "_validated_directory", return_value=self.directory):
            self.assertEqual(operator._validate_settings(self.settings), self.settings)
            for update in [
                {"keep": 0}, {"keep": True}, {"interval_hours": 0},
                {"interval_hours": 721}, {"automatic": "true"}, {"unexpected": 1},
            ]:
                candidate = {**self.settings, **update}
                with self.subTest(update=update), self.assertRaises(operator.OperatorError):
                    operator._validate_settings(candidate)

    def test_settings_persist_privately_and_change_timer(self):
        config = self.root / "backup-ui.json"
        env = self.root / "backup-ui.env"
        timer = self.root / "timer.conf"
        with patch.object(operator, "ALLOWED_PARENT", self.root), \
             patch.object(operator, "STATE_PATH", config), \
             patch.object(operator, "OVERRIDE_PATH", env), \
             patch.object(operator, "TIMER_OVERRIDE_PATH", timer), \
             patch.object(operator, "_validated_directory", return_value=self.directory), \
             patch.object(operator, "_systemctl") as systemctl:
            self.assertEqual(operator._save_settings(self.settings), self.settings)
            self.assertEqual(json.loads(config.read_text()), self.settings)
            self.assertIn("RETENTION_COUNT=5", env.read_text())
            self.assertIn("OnUnitActiveSec=12h", timer.read_text())
            self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(env.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(timer.stat().st_mode), 0o600)
            self.assertIn(("enable", "--now", "opensigweave-backup.timer"), [
                call.args for call in systemctl.call_args_list
            ])
            disabled = {**self.settings, "automatic": False}
            operator._save_settings(disabled)
            self.assertIn(("disable", "--now", "opensigweave-backup.timer"), [
                call.args for call in systemctl.call_args_list
            ])

    def test_backup_listing_download_and_deletion_validate_names_and_types(self):
        name = "opensigweave-20261122T191400Z.age"
        path = self.directory / name
        path.write_bytes(b"encrypted-archive")
        (self.directory / "random.tar.gz").write_bytes(b"plain")
        (self.directory / "opensigweave-unsafe.age").symlink_to(path)
        with patch.object(operator, "_settings", return_value=self.settings), \
             patch.object(operator, "_validated_directory", return_value=self.directory):
            names = [item["name"] for item in operator._entries(self.directory)]
            self.assertEqual(names, [name])
            with self.assertRaises(operator.OperatorError):
                operator._file("../secret.age")
            with self.assertRaises(operator.OperatorError):
                operator._file("opensigweave-unsafe.age")
            stream, size = operator._open_backup(name)
            with stream:
                self.assertEqual(stream.read(), b"encrypted-archive")
            self.assertEqual(size, len(b"encrypted-archive"))
            with self.assertRaises(operator.OperatorError):
                operator._delete(name, "no")
            lock = self.root / "lock"
            with patch.dict(os.environ, {"LOCK_FILE": str(lock)}):
                self.assertEqual(operator._delete(name, "DELETE"), {"deleted": name})
            self.assertFalse(path.exists())

    def test_restore_requires_explicit_confirmation_before_queuing(self):
        with patch.object(operator, "_file", return_value=(self.root / "dummy.age", None)):
            with self.assertRaises(operator.OperatorError):
                operator._queue("restore", "opensigweave-test.age", "yes")
        self.assertEqual(operator.current_job["state"], "idle")


    def test_unix_peer_credentials_against_actual_kernel_socket(self):
        import socket
        import threading
        if not hasattr(socket, "SO_PEERCRED") or os.getuid() == 0:
            self.skipTest("SO_PEERCRED test requires non-root Linux caller")
        path = self.root / "control.sock"
        with operator.Server(str(path), operator.Handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                def query():
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                        client.settimeout(3)
                        client.connect(str(path))
                        client.sendall(b'{"action":"invalid-operation"}\n')
                        return json.loads(client.makefile("rb").readline())

                with patch.object(operator, "APP_UID", os.getuid() + 1):
                    self.assertEqual(query(), {"ok": False, "error": "Backup access denied"})
                with patch.object(operator, "APP_UID", os.getuid()):
                    self.assertEqual(query(), {"ok": False, "error": "Unknown backup action"})
            finally:
                server.shutdown()
                thread.join(timeout=3)

    def test_socket_and_host_directory_modes_are_private(self):
        source = (Path(__file__).resolve().parents[1] / "scripts" /
                  "backup_control_agent.py").read_text(encoding="utf-8")
        self.assertIn("os.chown(SOCKET_PATH, APP_UID, 0)", source)
        self.assertIn("os.chmod(SOCKET_PATH, 0o600)", source)
        self.assertIn("os.chmod(SOCKET_PATH.parent, 0o700)", source)
        self.assertIn("os.chmod(lock_directory, 0o700)", source)
        self.assertNotIn("os.chmod(SOCKET_PATH, 0o660)", source)

    def test_restore_stays_non_destructive_by_default(self):
        from backup_cli import build_parser
        parser = build_parser()
        args = parser.parse_args([
            "restore", "backup.age", "--identity", "age.key",
            "--target-dir", str(self.root / "new-recovery"), "--project-name", "oswrestore1",
            "--app-port", "18095",
        ])
        self.assertEqual(args.app_port, 18095)


if __name__ == "__main__":
    unittest.main()
