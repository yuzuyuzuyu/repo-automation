"""Publish a native-automerge gate using Renovate's statuses on the PR's exact SHA.

Run only from the default branch; never import or execute pull-request code.
"""

import json
import os
import re
import subprocess
import sys
import time
from typing import Any

CONTEXT = "Dependency update safety"
ARTIFACTS = "renovate/artifacts"


def retryable_api_error(error: subprocess.CalledProcessError) -> bool:
    detail = (error.stderr or "").lower()
    status = re.search(r"\bhttp (\d{3})\b", detail)
    if status:
        # Rate limits require response-specific delays. Leave those, permission
        # errors and other client errors visible for the next workflow run.
        return status[1] == "408" or status[1].startswith("5")
    return any(
        message in detail
        for message in (
            "error connecting to ",
            "connection reset",
            "connection refused",
            "i/o timeout",
            "tls handshake timeout",
            "temporary failure in name resolution",
            "no such host",
            "unexpected eof",
            "context deadline exceeded",
        )
    ) or detail.rstrip().endswith(": eof")


def evaluate(
    pr: dict[str, Any], statuses: list[dict[str, Any]], repository: str
) -> tuple[str, str]:
    head = pr["head"]
    if (head.get("repo") or {}).get("full_name") != repository or not head[
        "ref"
    ].startswith("renovate/"):
        return "success", "Not a Renovate branch; required CI still applies"
    if pr["user"]["type"] != "Bot":
        return "failure", "Renovate branch was not opened by a bot"
    # Status IDs increase; evaluate the latest result for each context, including
    # later failures. Never accept a different actor spoofing a bot's success.
    latest: dict[str, dict[str, Any]] = {}
    for status in sorted(statuses, key=lambda item: item["id"], reverse=True):
        if status["context"].startswith("renovate/"):
            latest.setdefault(status["context"], status)
    if ARTIFACTS not in latest:
        return "pending", "Waiting for Renovate to verify generated lockfiles/artifacts"
    for context, status in latest.items():
        if status["creator"]["id"] != pr["user"]["id"]:
            return "failure", "Renovate status was written by an unexpected actor"
        if status["state"] in ("failure", "error"):
            return "failure", f"Blocked by {context}"
    if any(status["state"] != "success" for status in latest.values()):
        return "pending", "Waiting for Renovate release-age or other internal checks"
    # Age-exempt updates (including lockfile maintenance) have no stability
    # status. The explicit successful artifacts check is still mandatory.
    return "success", "Renovate release-age and artifact checks are satisfied"


def api(
    path: str, payload: dict[str, str] | None = None, paginate: bool = False
) -> Any:
    command = ["gh", "api", path]
    if paginate:
        command += ["--paginate", "--slurp"]
    if payload is not None:
        command += ["--method", "POST", "--input", "-"]
    # Fixed executable and argument list, with JSON on stdin; no shell or PR code.
    attempt = 1
    while True:
        try:
            result = subprocess.run(  # noqa: S603
                command,
                input=json.dumps(payload) if payload is not None else None,
                text=True,
                capture_output=True,
                check=True,
                timeout=30,
            )
            break
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            detail = (
                error.stderr or f"GitHub CLI exited with code {error.returncode}"
                if isinstance(error, subprocess.CalledProcessError)
                else "GitHub CLI timed out after 30 seconds"
            )
            print(
                f"GitHub API {path} failed (attempt {attempt}/3): {detail}",
                file=sys.stderr,
            )
            if attempt == 3 or (
                isinstance(error, subprocess.CalledProcessError)
                and not retryable_api_error(error)
            ):
                raise
            delay = 2**attempt
            print(f"Retrying GitHub API in {delay} seconds", file=sys.stderr)
            time.sleep(delay)
            attempt += 1
    data = json.loads(result.stdout)
    return [item for page in data for item in page] if paginate else data


def main() -> None:
    repository = os.environ["GITHUB_REPOSITORY"]
    # Reconcile every open PR each time. Actions coalesces pending runs in a
    # concurrency group, so checking only the event's PR could lose an update.
    prs = api(f"repos/{repository}/pulls?state=open&per_page=100", paginate=True)
    for pr in prs:
        sha = pr["head"]["sha"]
        statuses = api(
            f"repos/{repository}/commits/{sha}/statuses?per_page=100", paginate=True
        )
        state, description = evaluate(pr, statuses, repository)
        previous = next((s for s in statuses if s["context"] == CONTEXT), None)
        if (
            previous
            and previous.get("creator", {}).get("login") == "github-actions[bot]"
            and previous["state"] == state
            and previous["description"] == description
        ):
            continue
        api(
            f"repos/{repository}/statuses/{sha}",
            {
                "context": CONTEXT,
                "state": state,
                "description": description,
                "target_url": f"https://github.com/{repository}/actions/runs/{os.environ['GITHUB_RUN_ID']}",
            },
        )
        print(f"PR #{pr['number']}: {state}: {description}")


if __name__ == "__main__":
    main()
