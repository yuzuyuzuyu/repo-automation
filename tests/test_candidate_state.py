import io
import json
import unittest
import zipfile
from unittest.mock import patch

from src.candidate_state import restore, trusted_run


class CandidateStateTests(unittest.TestCase):
    def run_record(self, **changes):
        return {
            "id": 20,
            "repository": {"full_name": "owner/repo"},
            "head_repository": {"full_name": "owner/repo"},
            "head_branch": "main",
            "workflow_id": 7,
            "event": "schedule",
            "pull_requests": [],
            "status": "completed",
            "conclusion": "failure",
            "created_at": "2026-01-01T00:00:00Z",
            **changes,
        }

    def test_rejects_pr_fork_branch_and_different_workflow_state(self):
        for changes in (
            {"event": "pull_request"},
            {"event": "pull_request_target"},
            {"head_branch": "feature"},
            {"head_repository": {"full_name": "attacker/repo"}},
            {"repository": {"full_name": "attacker/repo"}},
            {"workflow_id": 8},
            {"pull_requests": [{"number": 1}]},
        ):
            self.assertFalse(
                trusted_run(self.run_record(**changes), "owner/repo", "main", 7)
            )

    def test_later_renovate_failure_does_not_erase_observations(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(
                "state.json", json.dumps({"schema": 1, "streams": {"observed": {}}})
            )
        current = self.run_record(id=21, created_at="2026-01-02T00:00:00Z")
        responses = [
            {
                "artifacts": [
                    {
                        "id": 4,
                        "name": "docker-candidates-v1",
                        "expired": False,
                        "size_in_bytes": 100,
                        "workflow_run": {"id": 19},
                    },
                    {
                        "id": 3,
                        "name": "docker-candidates-v1",
                        "expired": False,
                        "size_in_bytes": 100,
                        "workflow_run": {"id": 20},
                    },
                ]
            },
            self.run_record(id=19, event="pull_request"),
            self.run_record(),
        ]
        with (
            patch("src.candidate_state.api", side_effect=responses),
            patch("src.candidate_state.command", return_value=buffer.getvalue()),
        ):
            self.assertEqual(
                restore("owner/repo", "main", current)["streams"], {"observed": {}}
            )

    def test_missing_history_restarts_soak(self):
        with patch("src.candidate_state.api", return_value={"artifacts": []}):
            self.assertEqual(
                restore("owner/repo", "main", self.run_record()),
                {"schema": 1, "streams": {}},
            )

    def test_current_run_must_be_default_branch(self):
        with self.assertRaises(ValueError):
            restore("owner/repo", "main", self.run_record(head_branch="feature"))
