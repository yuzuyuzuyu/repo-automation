"""Execute the workflow's actual launcher with controlled engine exit codes."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


class RenovateCommandTests(unittest.TestCase):
    def launch(self, codes):
        workflow = yaml.safe_load(
            (ROOT / ".github/workflows/renovate-runner.yml").read_text()
        )
        step = next(
            s for s in workflow["jobs"]["renovate"]["steps"] if s.get("id") == "engine"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "automation").symlink_to(ROOT, target_is_directory=True)
            tools = root / "bin"
            tools.mkdir()
            log = root / "attempts.jsonl"
            (tools / "renovate").write_text(
                f"#!{sys.executable}\n"
                "import json, os, sys\n"
                "from pathlib import Path\n"
                "log = Path(os.environ['ATTEMPTS_LOG'])\n"
                "attempt = len(log.read_text().splitlines()) if log.exists() else 0\n"
                "with log.open('a') as f:\n"
                " f.write(json.dumps({'options':os.environ['NODE_OPTIONS'], 'state':os.environ['RENOVATE_CANDIDATE_STATE']}) + '\\n')\n"
                "sys.exit(json.loads(os.environ['EXIT_CODES'])[attempt])\n"
            )
            (tools / "sleep").write_text(
                f"#!{sys.executable}\n"
                "import os, sys\n"
                "with open(os.environ['SLEEP_LOG'], 'a') as f: f.write(sys.argv[1] + '\\n')\n"
            )
            for executable in tools.iterdir():
                executable.chmod(0o700)
            environment = {
                **os.environ,
                "PATH": f"{tools}{os.pathsep}{os.environ['PATH']}",
                "RUNNER_TEMP": str(root),
                "GITHUB_OUTPUT": str(root / "outputs"),
                "RENOVATE_AUTOMATION_PATH": str(ROOT),
                "RENOVATE_CANDIDATE_STATE": str(root / "state.json"),
                "NODE_OPTIONS": "--max-old-space-size=1024",
                "ATTEMPTS_LOG": str(log),
                "EXIT_CODES": json.dumps(codes),
                "SLEEP_LOG": str(root / "sleeps"),
            }
            subprocess.run(
                ["bash", "-euo", "pipefail", "-c", step["run"]],
                cwd=root,
                env=environment,
                check=True,
            )
            result = subprocess.run(
                [str(root / "renovate-command.sh")],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            attempts = [json.loads(line) for line in log.read_text().splitlines()]
            for attempt in attempts:
                self.assertEqual(
                    attempt["state"], environment["RENOVATE_CANDIDATE_STATE"]
                )
                self.assertEqual(
                    attempt["options"],
                    f"--max-old-space-size=1024 --import=file:{ROOT}/src/renovate_bootstrap.mjs",
                )
            sleeps = (
                (root / "sleeps").read_text().splitlines()
                if (root / "sleeps").exists()
                else []
            )
            return result.returncode, len(attempts), sleeps

    def test_temporary_errors_recover_with_bounded_backoff(self):
        for codes, expected in (
            ([0], (0, 1, [])),
            ([6, 0], (0, 2, ["15"])),
            ([6, 6, 0], (0, 3, ["15", "30"])),
            ([6, 6, 6, 0], (6, 3, ["15", "30"])),
            ([6, 7, 0], (7, 2, ["15"])),
        ):
            with self.subTest(codes=codes):
                self.assertEqual(self.launch(codes), expected)

    def test_other_failures_are_not_retried_or_hidden(self):
        for code in (1, 2, 3, 4, 5, 7, 8, 126, 127, 130, 137, 143):
            with self.subTest(code=code):
                self.assertEqual(self.launch([code, 0]), (code, 1, []))


if __name__ == "__main__":
    unittest.main()
