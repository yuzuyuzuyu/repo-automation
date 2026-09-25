import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "readiness",
    Path(__file__).resolve().parents[1] / "src/renovate_readiness.py",
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
PR = {
    "head": {
        "repo": {"full_name": "owner/repo"},
        "ref": "renovate/lock-file-maintenance",
    },
    "user": {"id": 42, "type": "Bot"},
}


def status(context, state="success", id=1, actor=42):
    return {"context": context, "state": state, "id": id, "creator": {"id": actor}}


class ReadinessTests(unittest.TestCase):
    def evaluate(self, statuses, pr=PR):
        return m.evaluate(pr, statuses, "owner/repo")[0]

    def test_missing_artifact_result_waits(self):
        self.assertEqual(self.evaluate([]), "pending")

    def test_lockfile_without_release_timestamp_passes(self):
        self.assertEqual(self.evaluate([status(m.ARTIFACTS)]), "success")

    def test_pending_age_blocks(self):
        self.assertEqual(
            self.evaluate(
                [status(m.ARTIFACTS), status("renovate/stability-days", "pending", 2)]
            ),
            "pending",
        )

    def test_failed_lockfile_blocks(self):
        self.assertEqual(self.evaluate([status(m.ARTIFACTS, "failure")]), "failure")

    def test_completed_age_passes(self):
        self.assertEqual(
            self.evaluate(
                [status(m.ARTIFACTS), status("renovate/stability-days", id=2)]
            ),
            "success",
        )

    def test_new_result_supersedes_old_failure(self):
        self.assertEqual(
            self.evaluate([status(m.ARTIFACTS, "failure"), status(m.ARTIFACTS, id=2)]),
            "success",
        )

    def test_new_failure_supersedes_old_success(self):
        self.assertEqual(
            self.evaluate([status(m.ARTIFACTS), status(m.ARTIFACTS, "failure", 2)]),
            "failure",
        )

    def test_spoofed_bot_status_fails(self):
        self.assertEqual(self.evaluate([status(m.ARTIFACTS, actor=123)]), "failure")

    def test_other_renovate_failure_blocks(self):
        self.assertEqual(
            self.evaluate(
                [status(m.ARTIFACTS), status("renovate/merge-confidence", "failure", 2)]
            ),
            "failure",
        )

    def test_human_pr_needs_no_renovate_status(self):
        pr = {
            "head": {"repo": {"full_name": "owner/repo"}, "ref": "fix/example"},
            "user": {"id": 99, "type": "User"},
        }
        self.assertEqual(self.evaluate([], pr), "success")

    def test_human_cannot_impersonate_renovate_branch(self):
        self.assertEqual(
            self.evaluate(
                [status(m.ARTIFACTS)], {**PR, "user": {"id": 42, "type": "User"}}
            ),
            "failure",
        )


if __name__ == "__main__":
    unittest.main()
