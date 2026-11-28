"""Run the release fixture builder without repository-root Python imports."""
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tests" / "release_package_docker_smoke.py"


class ReleasePackageDirectRunnerTests(unittest.TestCase):
    def test_stage_bundle_under_isolated_direct_script_environment(self):
        # -I avoids implicit working-directory/PYTHONPATH injection. This
        # reproduces the failure from python3 tests/release_package_docker_smoke.py
        # without requiring Docker or running the install/update scenario.
        code = textwrap.dedent("""
            import runpy
            from pathlib import Path
            import sys
            import tempfile
            script = runpy.run_path(sys.argv[1])
            with tempfile.TemporaryDirectory() as temp:
                root, manifest = script["stage_bundle"](
                    "2099.01", "a" * 40, Path(temp)
                )
                assert manifest["revision"] == "a" * 40
                assert (root / "scripts" / "release_bundle.py").is_file()
                assert (root / "install.sh").is_file()
        """)
        with tempfile.TemporaryDirectory() as outside:
            result = subprocess.run(
                [sys.executable, "-I", "-c", code, str(SCRIPT)],
                cwd=outside,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=25,
                check=False,
            )
        self.assertEqual(
            result.returncode, 0,
            "Direct package fixture import/build must work without PYTHONPATH: "
            + result.stderr,
        )


if __name__ == "__main__":
    unittest.main()
