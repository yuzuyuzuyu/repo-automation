"""A workflow succeeding is not sufficient permission to publish stale code."""

import importlib.util
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "publication", Path(__file__).parents[1] / "src" / "publication.py"
)
publication = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publication)


class PublicationTests(unittest.TestCase):
    def decision(self, replies, event=None):
        env = {
            "GITHUB_EVENT_NAME": "push",
            "GITHUB_REPOSITORY": "o/r",
            "GITHUB_EVENT_PATH": "event.json",
            "GITHUB_SHA": "current",
        }
        with (
            patch.dict(os.environ, env),
            patch.object(
                publication.Path,
                "read_text",
                side_effect=[json.dumps(event or {}), '{"ci_workflow":"ci.yml"}'],
            ),
            patch.object(publication, "api", side_effect=replies),
        ):
            return publication.ready()

    def test_current_revision_with_successful_ci_can_publish(self):
        self.assertTrue(
            self.decision(
                [
                    {"default_branch": "main"},
                    {"sha": "current"},
                    {
                        "workflow_runs": [
                            {"status": "completed", "conclusion": "success"}
                        ]
                    },
                ]
            )
        )

    def test_failed_ci_blocks_publication(self):
        self.assertFalse(
            self.decision(
                [
                    {"default_branch": "main"},
                    {"sha": "current"},
                    {
                        "workflow_runs": [
                            {"status": "completed", "conclusion": "failure"}
                        ]
                    },
                ]
            )
        )

    def test_superseded_revision_is_skipped(self):
        self.assertFalse(self.decision([{"default_branch": "main"}, {"sha": "newer"}]))

    def test_workflow_run_uses_tested_sha_not_workflow_context_sha(self):
        self.assertFalse(
            self.decision(
                [{"default_branch": "main"}, {"sha": "current"}],
                {"workflow_run": {"head_sha": "old"}},
            )
        )

    def test_api_error_is_not_a_clean_skip(self):
        with self.assertRaises(RuntimeError):
            self.decision([RuntimeError("API unavailable")])


if __name__ == "__main__":
    unittest.main()
