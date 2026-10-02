"""Release selection and publication gates, including retry and loop prevention."""

import unittest
from unittest.mock import patch

from src.release import affects_consumers, choose_version, tested_current_head


class ReleaseTests(unittest.TestCase):
    def test_shared_changes_increment_highest_semantic_patch(self):
        self.assertEqual(
            choose_version(
                {"v1.0.9": "a", "v1.0.10": "b", "v2.0.0-rc.1": "c"},
                {"v1.0.9", "v1.0.10"},
                "new",
                ["default.json"],
                "",
            ),
            "v1.0.11",
        )

    def test_each_consumer_component_is_released(self):
        for filename in [
            "actions/readiness/action.yml",
            "src/maintenance.py",
            ".github/workflows/renovate-runner.yml",
            "automation-updates.json",
        ]:
            with self.subTest(filename=filename):
                self.assertTrue(affects_consumers(filename))

    def test_documentation_tests_and_self_updates_do_not_loop(self):
        changed = [
            "README.md",
            "docs/maintenance.md",
            "tests/test_release.py",
            "renovate.json",
            "package.json",
            "package-lock.json",
            ".github/workflows/ci.yml",
            ".github/workflows/release.yml",
            "src/release.py",
        ]
        self.assertIsNone(
            choose_version({"v1.0.1": "old"}, {"v1.0.1"}, "new", changed, "")
        )

    def test_repeated_success_does_not_create_another_release(self):
        self.assertIsNone(
            choose_version({"v1.0.1": "head"}, {"v1.0.1"}, "head", ["default.json"], "")
        )

    def test_partial_publication_reuses_tag(self):
        self.assertEqual(
            choose_version({"v1.0.1": "head"}, set(), "head", [], ""), "v1.0.1"
        )

    def test_manual_major_release_and_retry(self):
        self.assertEqual(
            choose_version({"v1.0.1": "old"}, {"v1.0.1"}, "head", [], "v2.0.0"),
            "v2.0.0",
        )
        self.assertEqual(
            choose_version({"v2.0.0": "head"}, set(), "head", [], "v2.0.0"), "v2.0.0"
        )

    def test_existing_tags_cannot_move_and_versions_cannot_go_back(self):
        for requested in ["v1.0.1", "v1.0.0", "v01.0.2", "latest"]:
            with self.subTest(requested=requested), self.assertRaises(ValueError):
                choose_version({"v1.0.1": "old"}, {"v1.0.1"}, "head", [], requested)

    @patch("src.release.api")
    def test_current_successful_push_ci_is_required(self, api):
        api.side_effect = [
            {"sha": "head"},
            {"workflow_runs": [{"status": "completed", "conclusion": "success"}]},
        ]
        self.assertTrue(tested_current_head("owner/repo", "main", "head"))
        self.assertIn("event=push", api.call_args.args[0])

    @patch("src.release.api")
    def test_failed_pending_or_missing_ci_blocks_release(self, api):
        for runs in [
            [],
            [{"status": "in_progress", "conclusion": None}],
            [{"status": "completed", "conclusion": "failure"}],
        ]:
            with self.subTest(runs=runs):
                api.side_effect = [{"sha": "head"}, {"workflow_runs": runs}]
                self.assertFalse(tested_current_head("owner/repo", "main", "head"))

    @patch("src.release.api", return_value={"sha": "newer"})
    def test_superseded_revision_cannot_release(self, api):
        self.assertFalse(tested_current_head("owner/repo", "main", "head"))
        api.assert_called_once()


if __name__ == "__main__":
    unittest.main()
