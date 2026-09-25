"""Exercise the action boundary where privileged code leaves the caller workspace."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


class ActionTests(unittest.TestCase):
    def test_readiness_uses_pinned_code_from_outside_caller_checkout(self):
        action = ROOT / "actions/readiness"
        step = yaml.safe_load((action / "action.yml").read_text())["runs"]["steps"][0]
        with tempfile.TemporaryDirectory() as directory:
            caller = Path(directory)
            tools = caller / "bin"
            tools.mkdir()
            (tools / "python3").symlink_to(sys.executable)
            log = caller / "api.jsonl"
            gh = tools / "gh"
            gh.write_text(
                f"#!{sys.executable}\n"
                "import json, os, sys\n"
                "from pathlib import Path\n"
                "endpoint=sys.argv[2]\n"
                "if '/pulls?' in endpoint:\n"
                " print(json.dumps([[{'number':1,'head':{'sha':'tested-sha','ref':'renovate/lock-file-maintenance','repo':{'full_name':'owner/caller'}},'user':{'id':42,'type':'Bot'}}]]))\n"
                "elif '/commits/' in endpoint:\n"
                " print(json.dumps([[{'id':1,'context':'renovate/artifacts','state':'success','creator':{'id':42}}]]))\n"
                "else:\n"
                " with Path(os.environ['API_LOG']).open('a') as f: f.write(json.dumps({'endpoint':endpoint,'body':json.load(sys.stdin)})+'\\n')\n"
                " print('{}')\n"
            )
            gh.chmod(0o700)
            # A malicious caller copy must never be imported or executed.
            local = caller / ".github/scripts"
            local.mkdir(parents=True)
            (local / "renovate_readiness.py").write_text(
                "raise RuntimeError('untrusted')"
            )
            result = subprocess.run(
                ["bash", "-euo", "pipefail", "-c", step["run"]],
                cwd=caller,
                env={
                    **os.environ,
                    "PATH": f"{tools}{os.pathsep}{os.environ['PATH']}",
                    "AUTOMATION_PATH": str(action),
                    "GITHUB_REPOSITORY": "owner/caller",
                    "GITHUB_RUN_ID": "123",
                    "API_LOG": str(log),
                },
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("PR #1: success", result.stdout)
            posted = json.loads(log.read_text())
            self.assertEqual(
                posted["endpoint"], "repos/owner/caller/statuses/tested-sha"
            )
            self.assertEqual(posted["body"]["context"], "Dependency update safety")
            self.assertEqual(posted["body"]["state"], "success")

    def test_scan_saves_state_before_dispatch_and_sarif_is_optional(self):
        action = yaml.safe_load((ROOT / "actions/security-scan/action.yml").read_text())
        steps = action["runs"]["steps"]
        save = next(
            i
            for i, step in enumerate(steps)
            if "upload-artifact@" in step.get("uses", "")
        )
        dispatch = next(
            i
            for i, step in enumerate(steps)
            if "gh workflow run" in step.get("run", "")
        )
        self.assertLess(save, dispatch)
        self.assertEqual(steps[save]["with"]["name"], "maintenance-state")
        sarif = next(step for step in steps if "upload-sarif@" in step.get("uses", ""))
        self.assertEqual(sarif["if"], "inputs.upload-sarif == 'true'")
        self.assertEqual(
            steps[0]["with"]["ref"], "${{ github.event.repository.default_branch }}"
        )


if __name__ == "__main__":
    unittest.main()
