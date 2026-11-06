import importlib.util
import io
import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import call, patch

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


class ReadinessApiTests(unittest.TestCase):
    def setUp(self):
        self.run = self.enterContext(patch.object(m.subprocess, "run"))
        self.sleep = self.enterContext(patch.object(m.time, "sleep"))
        self.stderr = self.enterContext(patch.object(m.sys, "stderr", io.StringIO()))

    def response(self, data):
        return subprocess.CompletedProcess(["gh", "api"], 0, json.dumps(data), "")

    def error(self, detail, output=""):
        return subprocess.CalledProcessError(
            1, ["gh", "api"], output=output, stderr=detail
        )

    def test_transient_errors_retry_and_discard_partial_pagination(self):
        self.run.side_effect = [
            self.error("gh: Bad Gateway (HTTP 502)", output='[[{"id":1}]]'),
            self.error("error connecting to api.github.com"),
            self.response([[{"id": 2}], [{"id": 3}]]),
        ]
        self.assertEqual(
            m.api("commits/head/statuses", paginate=True), [{"id": 2}, {"id": 3}]
        )
        self.assertEqual(self.run.call_count, 3)
        self.assertEqual(self.sleep.call_args_list, [call(2), call(4)])
        self.assertIn("HTTP 502", self.stderr.getvalue())
        for invocation in self.run.call_args_list:
            self.assertEqual(invocation.args[0][-2:], ["--paginate", "--slurp"])
            self.assertEqual(invocation.kwargs["timeout"], 30)

    def test_exhausted_transient_error_still_fails(self):
        error = self.error("gh: Service Unavailable (HTTP 503)")
        self.run.side_effect = error
        with self.assertRaises(subprocess.CalledProcessError) as raised:
            m.api("commits/head/statuses")
        self.assertIs(raised.exception, error)
        self.assertEqual(self.run.call_count, 3)
        self.assertEqual(self.sleep.call_args_list, [call(2), call(4)])

    def test_client_and_unknown_errors_fail_without_retry(self):
        for detail in (
            "gh: Bad credentials (HTTP 401)",
            "gh: Resource not accessible by integration (HTTP 403)",
            "gh: API rate limit exceeded (HTTP 403)",
            "gh: Not Found (HTTP 404)",
            "gh: Validation Failed (HTTP 422)",
            "gh: Too Many Requests (HTTP 429)",
            "To get started with GitHub CLI, please run: gh auth login",
        ):
            with self.subTest(detail=detail):
                self.run.reset_mock()
                self.sleep.reset_mock()
                self.run.side_effect = self.error(detail)
                with self.assertRaises(subprocess.CalledProcessError):
                    m.api("commits/head/statuses")
                self.run.assert_called_once()
                self.sleep.assert_not_called()

    def test_transient_http_and_network_failures_recover(self):
        for detail in (
            "gh: Request Timeout (HTTP 408)",
            "gh: Internal Server Error (HTTP 500)",
            "gh: Gateway Timeout (HTTP 504)",
            'Get "https://api.github.com": read: connection reset by peer',
            'Get "https://api.github.com": dial tcp: lookup: no such host',
            'Get "https://api.github.com": EOF',
        ):
            with self.subTest(detail=detail):
                self.run.reset_mock()
                self.sleep.reset_mock()
                self.run.side_effect = [self.error(detail), self.response([])]
                self.assertEqual(m.api("commits/head/statuses"), [])
                self.assertEqual(self.run.call_count, 2)
                self.sleep.assert_called_once_with(2)

    def test_timeouts_retry_and_remain_failures_when_exhausted(self):
        timeout = subprocess.TimeoutExpired(["gh", "api"], 30)
        self.run.side_effect = [timeout, self.response([])]
        self.assertEqual(m.api("commits/head/statuses"), [])
        self.sleep.assert_called_once_with(2)

        self.run.reset_mock()
        self.sleep.reset_mock()
        self.run.side_effect = timeout
        with self.assertRaises(subprocess.TimeoutExpired):
            m.api("commits/head/statuses")
        self.assertEqual(self.run.call_count, 3)
        self.assertEqual(self.sleep.call_args_list, [call(2), call(4)])

    def test_status_retry_keeps_the_same_commit_and_payload(self):
        payload = {"context": m.CONTEXT, "state": "pending"}
        self.run.side_effect = [
            self.error("gh: Bad Gateway (HTTP 502)"),
            self.response({}),
        ]
        self.assertEqual(m.api("statuses/exact-head", payload), {})
        first, second = self.run.call_args_list
        self.assertEqual(first, second)
        self.assertEqual(
            first.args[0],
            ["gh", "api", "statuses/exact-head", "--method", "POST", "--input", "-"],
        )
        self.assertEqual(json.loads(first.kwargs["input"]), payload)

    def test_invalid_response_is_not_retried_or_accepted(self):
        self.run.return_value = subprocess.CompletedProcess(
            ["gh", "api"], 0, "bad json", ""
        )
        with self.assertRaises(json.JSONDecodeError):
            m.api("commits/head/statuses")
        self.run.assert_called_once()
        self.sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
