"""Restore observations only from this repository's trusted Renovate workflow."""

import io
import json
import os
import re
import zipfile
from pathlib import Path
from typing import Any

from src.maintenance import api, command

ARTIFACT = "docker-candidates-v1"
MAX_BYTES = 20_000_000


def trusted_run(run: dict[str, Any], repo: str, branch: str, workflow: int) -> bool:
    return (
        run.get("repository", {}).get("full_name") == repo
        and run.get("head_repository", {}).get("full_name") == repo
        and run.get("head_branch") == branch
        and run.get("workflow_id") == workflow
        and run.get("event") in {"schedule", "workflow_dispatch", "push"}
        and not run.get("pull_requests")
    )


def restore(repo: str, branch: str, current: dict[str, Any]) -> dict[str, Any]:
    workflow = current["workflow_id"]
    if not trusted_run(current, repo, branch, workflow):
        raise ValueError("Candidate state requires a trusted default-branch run")
    # Query by artifact name first: initial rollout must not inspect hundreds of
    # legacy workflow runs that predate candidate retention.
    for page in range(1, 11):
        artifacts = api(
            f"repos/{repo}/actions/artifacts?name={ARTIFACT}&per_page=100&page={page}"
        )["artifacts"]
        for artifact in artifacts:
            if artifact["name"] != ARTIFACT or artifact["expired"]:
                continue
            run_id = artifact.get("workflow_run", {}).get("id")
            if not run_id or run_id == current["id"]:
                continue
            run = api(f"repos/{repo}/actions/runs/{run_id}")
            if (
                run["status"] != "completed"
                or run["created_at"] >= current["created_at"]
                or not trusted_run(run, repo, branch, workflow)
            ):
                continue
            if artifact["size_in_bytes"] > MAX_BYTES:
                raise ValueError("Candidate archive exceeds limit")
            raw = command(
                ["gh", "api", f"repos/{repo}/actions/artifacts/{artifact['id']}/zip"]
            )
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                info = archive.getinfo("state.json")
                if info.file_size > MAX_BYTES:
                    raise ValueError("Candidate state exceeds limit")
                state = json.loads(archive.read(info))
            if not isinstance(state, dict) or state.get("schema") != 1:
                raise ValueError("Invalid candidate state schema")
            return state
        if len(artifacts) < 100:
            break
    # Missing/expired history never implies maturity: start a new observation window.
    return {"schema": 1, "streams": {}}


def main() -> None:
    repo = os.environ["GITHUB_REPOSITORY"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("Invalid repository")
    current = api(f"repos/{repo}/actions/runs/{os.environ['GITHUB_RUN_ID']}")
    branch = api(f"repos/{repo}")["default_branch"]
    state = restore(repo, branch, current)
    destination = Path(os.environ["RENOVATE_CANDIDATE_STATE"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(state))
    # The upstream unprivileged container user must write the shared scratch dir.
    # This is non-secret data in a single-job hosted runner, never executable code.
    destination.parent.chmod(0o777)
    destination.chmod(0o644)


if __name__ == "__main__":
    main()
