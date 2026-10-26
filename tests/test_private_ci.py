"""Check that private minute savings never omit changed runtime inputs."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import private_ci

PROFILES = json.loads((private_ci.ROOT / "private/ci-profiles.json").read_text())


class SelectionTests(unittest.TestCase):
    def selected(self, profile, paths):
        return {
            key
            for key, value in private_ci.select_checks(PROFILES[profile], paths).items()
            if value
        }

    def test_docs_and_policy_only_changes_skip_expensive_checks(self):
        for profile in PROFILES:
            self.assertEqual(
                self.selected(
                    profile, ["README.md", "docs/maintenance.md", "renovate.json"]
                ),
                set(),
            )

    def test_unknown_paths_and_missing_history_run_full_checks(self):
        for profile, config in PROFILES.items():
            self.assertEqual(
                self.selected(profile, ["new-runtime-input.toml"]),
                set(config["checks"]),
            )
            self.assertEqual(self.selected(profile, None), set(config["checks"]))

    def test_ansible_shared_inputs_and_cross_service_changes(self):
        for filename in [
            "inventory/group_vars/all/vars.yml",
            "requirements.yml",
            "playbooks/services/common/tasks/guard.yml",
            ".github/workflows/ci.yml",
        ]:
            self.assertEqual(
                self.selected("yuzuyu", [filename]), set(PROFILES["yuzuyu"]["checks"])
            )
        self.assertEqual(
            self.selected("yuzuyu", ["playbooks/services/nginx/vars/service-vars.yml"]),
            {"tests", "ansible"},
        )
        self.assertEqual(
            self.selected(
                "yuzuyu", ["playbooks/services/upgallery/tasks/ownership.yml"]
            ),
            {"tests", "ansible", "upgallery"},
        )
        self.assertEqual(
            self.selected(
                "yuzuyu", ["playbooks/services/pyburlybot/container/requirements.txt"]
            ),
            {"tests", "pyburlybot"},
        )

    def test_image_inputs_and_test_only_changes(self):
        for profile, filenames in {
            "passwordreset": [
                "app/package-lock.json",
                "helper/Cargo.lock",
                ".nvmrc",
                "Dockerfile",
            ],
            "webhook-image-updater": [
                "cmd/webhook-image-updater/main.go",
                "internal/config/config.go",
                "go.mod",
                "Dockerfile",
            ],
        }.items():
            for filename in [
                *filenames,
                ".dockerignore",
                ".github/workflows/docker.yml",
            ]:
                self.assertEqual(self.selected(profile, [filename]), {"tests", "image"})
        self.assertEqual(
            self.selected("webhook-image-updater", ["internal/config/config_test.go"]),
            {"tests"},
        )
        self.assertEqual(
            self.selected("webhook-image-updater", ["Makefile"]), {"tests"}
        )

    def test_pin_scope_covers_updaters_templates_and_shared_dependencies(self):
        for filename in [
            "playbooks/services/monitoring/vars/service-vars.yml",
            "playbooks/services/convex/vars/template-vars.yml",
            "playbooks/services/when-convex/vars/service-vars.yml",
            "playbooks/services/pyburlybot/vars/service-vars.yml",
            "playbooks/services/nginx/templates/update.sh.j2",
            "inventory/group_vars/all/main.yml",
            "requirements.txt",
            "playbooks/server/tasks/sync_monitoring_pins.yml",
        ]:
            self.assertEqual(self.selected("dependency-pins", [filename]), {"pins"})
        self.assertEqual(
            self.selected(
                "dependency-pins",
                [
                    "tests/test_dependency_pins.py",
                ],
            ),
            set(),
        )

    def test_sync_baseline_requires_completed_host_job(self):
        def api(endpoint):
            if "workflows/" in endpoint:
                return {
                    "workflow_runs": [
                        {"id": 3, "head_sha": "current", "head_branch": "main"},
                        {"id": 2, "head_sha": "noop", "head_branch": "main"},
                        {"id": 1, "head_sha": "deployed", "head_branch": "main"},
                    ]
                }
            return {
                "jobs": [
                    {
                        "name": "synchronize / run",
                        "conclusion": "skipped" if "/2/" in endpoint else "success",
                    }
                ]
            }

        with patch.object(private_ci, "api", side_effect=api):
            self.assertEqual(
                private_ci.previous_pin_sync("owner/repo", "3"), "deployed"
            )

    def test_failed_diff_and_api_force_checks(self):
        with patch.object(
            private_ci, "command", side_effect=subprocess.CalledProcessError(1, "git")
        ):
            self.assertIsNone(
                private_ci.changed_paths("push", {"before": "old"}, "yuzuyu")
            )
        with (
            patch.object(private_ci, "command", return_value="current"),
            patch.object(
                private_ci,
                "previous_pin_sync",
                side_effect=subprocess.CalledProcessError(1, "gh"),
            ),
        ):
            with patch.dict(
                os.environ, {"GITHUB_REPOSITORY": "owner/repo", "GITHUB_RUN_ID": "3"}
            ):
                self.assertIsNone(
                    private_ci.changed_paths(
                        "workflow_run",
                        {"workflow_run": {"head_sha": "current"}},
                        "dependency-pins",
                    )
                )

    def test_real_git_multi_commit_push_rename_and_manual_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def git(*args):
                return subprocess.check_output(
                    ["git", *args], cwd=root, text=True
                ).strip()

            git("init", "-q")
            git("config", "user.email", "test@example.invalid")
            git("config", "user.name", "Test")
            (root / "Dockerfile").write_text("FROM scratch\n")
            git("add", ".")
            git("commit", "-qm", "base")
            base = git("rev-parse", "HEAD")
            git("mv", "Dockerfile", "README.md")
            git("commit", "-qm", "rename runtime input")
            (root / "notes.md").write_text("notes")
            git("add", ".")
            git("commit", "-qm", "documentation")

            def command(args):
                return subprocess.check_output(args, cwd=root, text=True)

            with patch.object(private_ci, "command", side_effect=command):
                paths = private_ci.changed_paths(
                    "push", {"before": base}, "passwordreset"
                )
                self.assertEqual(set(paths), {"Dockerfile", "README.md", "notes.md"})
                self.assertEqual(
                    self.selected("passwordreset", paths), {"tests", "image"}
                )
                self.assertIsNone(
                    private_ci.changed_paths("workflow_dispatch", {}, "passwordreset")
                )
                self.assertIsNone(
                    private_ci.changed_paths(
                        "push", {"before": "0" * 40}, "passwordreset"
                    )
                )
                self.assertIsNone(
                    private_ci.changed_paths("schedule", {}, "dependency-pins")
                )


if __name__ == "__main__":
    unittest.main()
