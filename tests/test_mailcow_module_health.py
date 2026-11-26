"""Host-side Mailcow module health check regression tests."""
import subprocess
import unittest

from scripts.check_mailcow_modules import FILES, inspect, parse_modules


def fake_run(*, missing=(), enabled=("opensigweave", "opensigweave_guard"),
             failed=(), command_failure=None, malformed=False):
    def runner(args, cwd):
        assert cwd == "/mailcow"
        assert args[:5] == ["docker", "compose", "exec", "-T", "rspamd-mailcow"]
        if args[5] == "sh":
            if command_failure == "files":
                return subprocess.CompletedProcess(args, 127, "", "")
            out = "".join(f"MISSING:{path}\n" for path in missing)
            return subprocess.CompletedProcess(args, 0, out, "")
        if command_failure == "modules":
            return subprocess.CompletedProcess(args, 1, "", "")
        out = "unexpected output" if malformed else (
            "Modules enabled: " + ", ".join(enabled) + "\n"
            "Modules disabled (failed): " + ", ".join(failed) + "\n"
        )
        return subprocess.CompletedProcess(args, 0, out, "")
    return runner


class MailcowHealthTests(unittest.TestCase):
    def test_registered_modules_pass(self):
        code, result = inspect("/mailcow", runner=fake_run())
        self.assertEqual((code, result["status"]), (0, "PASS"))

    def test_missing_injector_file_fails(self):
        code, result = inspect("/mailcow", runner=fake_run(missing=(FILES[0],)))
        self.assertEqual(code, 1)
        self.assertIn(FILES[0], "\n".join(result["messages"]))

    def test_missing_guard_fails_in_enabled(self):
        code, result = inspect("/mailcow", runner=fake_run(
            missing=(FILES[1],), enabled=("opensigweave",)))
        self.assertEqual(code, 1)
        self.assertIn("opensigweave_guard", "\n".join(result["messages"]))

    def test_missing_guard_allowed_in_disabled(self):
        code, result = inspect("/mailcow", "disabled", fake_run(
            missing=(FILES[1],), enabled=("opensigweave",)))
        self.assertEqual(code, 0, result)

    def test_missing_helper_fails(self):
        for path in FILES[2:]:
            code, result = inspect("/mailcow", runner=fake_run(missing=(path,)))
            self.assertEqual(code, 1, result)

    def test_modules_disabled_failed_fails(self):
        code, result = inspect("/mailcow", runner=fake_run(failed=("opensigweave",)))
        self.assertEqual(code, 1, result)

    def test_unparseable_output_is_unknown(self):
        code, result = inspect("/mailcow", runner=fake_run(malformed=True))
        self.assertEqual((code, result["status"]), (2, "UNKNOWN"))

    def test_command_error_is_unknown(self):
        for failing in ("files", "modules"):
            code, result = inspect("/mailcow", runner=fake_run(command_failure=failing))
            self.assertEqual((code, result["status"]), (2, "UNKNOWN"))

    def test_modules_are_matched_exactly(self):
        self.assertEqual(parse_modules(
            "Modules enabled: opensigweave_fake, opensigweave_guard\n"
            "Modules disabled (failed): \n"
        ), ({"opensigweave_fake", "opensigweave_guard"}, set()))


if __name__ == "__main__":
    unittest.main()
