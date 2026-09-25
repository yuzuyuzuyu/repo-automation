"""Maintenance decisions must never turn a broken scan into a clean verdict."""

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "fleet_maintenance", Path(__file__).parents[1] / "src" / "maintenance.py"
)
maintenance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(maintenance)


class MaintenanceTests(unittest.TestCase):
    def report(self, severity="HIGH", installed="1", fixed="2"):
        return {
            "SchemaVersion": 2,
            "ArtifactName": "image",
            "Results": [
                {
                    "Target": "os",
                    "Vulnerabilities": [
                        {
                            "PkgName": "lib",
                            "VulnerabilityID": "CVE-1",
                            "Severity": severity,
                            "InstalledVersion": installed,
                            "FixedVersion": fixed,
                        }
                    ],
                }
            ],
        }

    def test_collects_resolved_inventory_images_and_omits_owned_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "vars.yml"
            path.write_text(
                'repo: ghcr.io/owner/app\napp_image: "{{ repo }}:{{ tag }}"\n'
                "cosign_image: ghcr.io/sigstore/cosign:v3\n"
                'effective_image: "{{ cosign_image }}"\n'
            )
            self.assertEqual(
                maintenance.collect_images(
                    [path], ["ghcr.io/owner/app"], {"tag": "latest"}
                ),
                ["ghcr.io/sigstore/cosign:v3"],
            )

    def test_unresolved_image_is_an_error_not_silent_omission(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "vars.yml"
            path.write_text('app_image: "{{ unknown }}:latest"\n')
            with self.assertRaises(ValueError):
                maintenance.collect_images([path], [], {})

    def test_malformed_report_is_not_clean(self):
        for report in [
            {},
            {"SchemaVersion": 2},
            {"SchemaVersion": 2, "ArtifactName": "i", "Results": None},
        ]:
            with self.assertRaises(ValueError):
                maintenance.findings(report, "image")

    def test_clean_scratch_image_is_valid(self):
        self.assertEqual(
            maintenance.findings({"SchemaVersion": 2, "ArtifactName": "scratch"}, "i"),
            {},
        )

    def test_preserves_scanner_status_without_inventing_a_fixed_version(self):
        for status in ("affected", "fix_deferred", "will_not_fix", None):
            with self.subTest(status=status):
                report = self.report()
                vulnerability = report["Results"][0]["Vulnerabilities"][0]
                del vulnerability["FixedVersion"]
                if status is not None:
                    vulnerability["Status"] = status
                current = maintenance.findings(report, "image")
                finding = next(iter(current.values()))
                self.assertEqual(finding["status"], status or "unknown")
                self.assertEqual(finding["fixed"], "")
                state, _, rebuild = maintenance.plan(current, {}, 0, True)
                self.assertFalse(rebuild)
                self.assertEqual(
                    next(iter(state.values()))["status"], status or "unknown"
                )

    def test_pending_update_grace_is_bounded(self):
        current = maintenance.findings(self.report(), "image")
        state, _, _ = maintenance.plan(current, {}, 0, True)
        _, actionable, _ = maintenance.plan(current, state, 4 * 86400, True, {"lib"})
        self.assertFalse(actionable)
        _, actionable, _ = maintenance.plan(current, state, 8 * 86400, True, {"lib"})
        self.assertTrue(actionable)

    def test_escalated_finding_stays_open_while_a_new_update_runs(self):
        current = maintenance.findings(self.report(), "image")
        state, _, _ = maintenance.plan(current, {}, 0, True)
        state, actionable, _ = maintenance.plan(current, state, 4 * 86400, True)
        self.assertTrue(actionable)
        _, actionable, _ = maintenance.plan(current, state, 5 * 86400, True, {"lib"})
        self.assertTrue(actionable)

    def test_secret_is_redacted_and_escalated_immediately(self):
        report = self.report("LOW")
        report["Results"][0]["Secrets"] = [
            {
                "RuleID": "key",
                "StartLine": 1,
                "Match": "sensitive",
                "Code": {"Lines": [{"Content": "sensitive"}]},
            }
        ]
        report = maintenance.redact_secrets(report)
        self.assertNotIn("sensitive", str(report))
        current = maintenance.findings(report, "image")
        _, actionable, rebuild = maintenance.plan(current, {}, 0, True)
        self.assertTrue(actionable)
        self.assertFalse(rebuild)

    def test_low_findings_do_not_escalate(self):
        self.assertEqual(maintenance.findings(self.report("LOW"), "image"), {})

    def test_rebuild_once_then_escalate_and_resolve(self):
        current = maintenance.findings(self.report(), "image")
        state, actionable, rebuild = maintenance.plan(current, {}, 0, True)
        self.assertTrue(rebuild)
        self.assertEqual(actionable, {})
        state, actionable, rebuild = maintenance.plan(current, state, 3600, True)
        self.assertFalse(rebuild)
        self.assertEqual(actionable, {})
        state, actionable, rebuild = maintenance.plan(current, state, 72 * 3600, True)
        self.assertEqual(len(actionable), 1)
        self.assertEqual(maintenance.plan({}, state, 73 * 3600, True), ({}, {}, False))

    def test_installed_version_change_does_not_restart_clock(self):
        current = maintenance.findings(self.report(), "image")
        state, _, _ = maintenance.plan(current, {}, 0, True)
        current = maintenance.findings(self.report(installed="1.5"), "image")
        _, actionable, rebuild = maintenance.plan(current, state, 72 * 3600, True)
        self.assertEqual(len(actionable), 1)
        self.assertFalse(rebuild)

    def test_new_fix_requests_rebuild(self):
        current = maintenance.findings(self.report(fixed=""), "image")
        state, _, rebuild = maintenance.plan(current, {}, 0, True)
        self.assertFalse(rebuild)
        current = maintenance.findings(self.report(), "image")
        _, _, rebuild = maintenance.plan(current, state, 3600, True)
        self.assertTrue(rebuild)

    def test_external_image_cannot_dispatch_local_build(self):
        current = maintenance.findings(self.report(), "image")
        _, _, rebuild = maintenance.plan(current, {}, 0, False)
        self.assertFalse(rebuild)

    def test_unchanged_issue_is_quiet(self):
        issue = {
            "number": 1,
            "title": "title",
            "body": "<!-- fleet-maintenance:security -->\n\nbody",
            "user": {"type": "Bot"},
        }
        with (
            patch.object(maintenance, "pages", return_value=[issue]),
            patch.object(maintenance, "api") as api,
        ):
            maintenance.reconcile_issue("owner/repo", "security", "title", "body")
            api.assert_not_called()
            maintenance.reconcile_issue("owner/repo", "security", "title", None)
            self.assertEqual(api.call_args.args[2]["state"], "closed")

    def test_old_update_gets_grace_only_after_a_new_head_commit(self):
        pr = {
            "user": {"type": "Bot"},
            "head": {"ref": "renovate/lib", "sha": "revision"},
            "created_at": "2026-01-01T00:00:00Z",
            "title": "Update lib",
            "html_url": "https://github.com/owner/repo/pull/1",
        }
        now = maintenance.epoch("2026-01-20T00:00:00Z")
        for date, should_escalate in [
            ("2026-01-19T00:00:00Z", False),
            ("2026-01-18T00:00:00Z", True),
        ]:
            with (
                patch.dict(maintenance.os.environ, {"GITHUB_REPOSITORY": "owner/repo"}),
                patch.object(maintenance, "pages", return_value=[pr]),
                patch.object(maintenance.time, "time", return_value=now),
                patch.object(
                    maintenance,
                    "api",
                    return_value={"commit": {"committer": {"date": date}}},
                ),
                patch.object(maintenance, "recover_workflows"),
                patch.object(maintenance, "reconcile_issue") as reconcile,
            ):
                maintenance.stalled_updates({})
                self.assertEqual(
                    reconcile.call_args.args[3] is not None, should_escalate
                )

    def test_does_not_modify_human_issue(self):
        issue = {
            "number": 1,
            "body": "<!-- fleet-maintenance:security -->",
            "user": {"type": "User"},
        }
        with (
            patch.object(maintenance, "pages", return_value=[issue]),
            patch.object(maintenance, "api") as api,
        ):
            maintenance.reconcile_issue("owner/repo", "security", "title", None)
            api.assert_not_called()


if __name__ == "__main__":
    unittest.main()
