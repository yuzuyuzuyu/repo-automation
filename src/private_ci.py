"""Select private CI work conservatively from full Git history, never PR-supplied code."""

import fnmatch
import json
import os
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUTPUTS = ("tests", "ansible", "upgallery", "pyburlybot", "image", "pins")


def command(args: list[str]) -> str:
    return subprocess.check_output(args, text=True)  # noqa: S603


def api(endpoint: str) -> Any:
    return json.loads(command(["gh", "api", endpoint]))


def select_checks(
    profile: dict[str, Any], changed: list[str] | None
) -> dict[str, bool]:
    """Unknown paths or unavailable history select every check in the profile."""
    selected: set[str] = set()
    if changed is None:
        selected.update(profile["checks"])
    else:
        for filename in changed:
            if any(fnmatch.fnmatchcase(filename, p) for p in profile["ignore"]):
                continue
            for rule in profile["rules"]:
                if any(fnmatch.fnmatchcase(filename, p) for p in rule["patterns"]):
                    selected.update(rule["checks"])
                    break
            else:
                selected.update(profile["checks"])
    return {key: key in selected for key in OUTPUTS}


def previous_pin_sync(repository: str, current_run: str) -> str | None:
    """A green no-op gate is not evidence that the host was synchronized."""
    runs = api(
        f"repos/{repository}/actions/workflows/dependency-pins.yml/runs?status=success&per_page=20"
    )["workflow_runs"]
    for run in runs:
        if str(run["id"]) == current_run or run["head_branch"] != "main":
            continue
        jobs = api(f"repos/{repository}/actions/runs/{run['id']}/jobs?per_page=100")[
            "jobs"
        ]
        if any(
            j["name"] == "synchronize / run" and j["conclusion"] == "success"
            for j in jobs
        ):
            return str(run["head_sha"])
    return None


def changed_paths(
    event_name: str, event: dict[str, Any], profile_name: str
) -> list[str] | None:
    """Use both sides of renames, merge-base PR diffs, and every commit in a push."""
    try:
        head = command(["git", "rev-parse", "HEAD"]).strip()
        if event_name == "pull_request":
            base = event["pull_request"]["base"]["sha"]
            separator = "..."
        elif event_name == "push":
            base = event.get("before")
            separator = ".."
        elif event_name == "workflow_run" and profile_name == "dependency-pins":
            # Caller checks out exactly this CI revision; publication checks it is still current.
            if head != event["workflow_run"]["head_sha"]:
                raise ValueError("Checkout differs from the triggering CI revision")
            base = previous_pin_sync(
                os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_RUN_ID"]
            )
            separator = ".."
        else:
            # Manual security rebuilds and the weekly repair fallback always run in full.
            return None
        if not base or set(base) == {"0"}:
            return None
        return [
            name
            for name in command(
                [
                    "git",
                    "diff",
                    "--name-only",
                    "--no-renames",
                    "-z",
                    f"{base}{separator}{head}",
                    "--",
                ]
            ).split("\0")
            if name
        ]
    except (subprocess.CalledProcessError, KeyError, ValueError) as error:
        print(f"Change detection unavailable; running full checks: {error}")
        return None


def main() -> None:
    profile_name = os.environ["PRIVATE_CI_PROFILE"]
    profiles = json.loads((ROOT / "private/ci-profiles.json").read_text())
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    changes = changed_paths(os.environ["GITHUB_EVENT_NAME"], event, profile_name)
    selected = select_checks(profiles[profile_name], changes)
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
        for name, enabled in selected.items():
            output.write(f"{name}={str(enabled).lower()}\n")
    print(
        "Selected checks: "
        + ", ".join(name for name, enabled in selected.items() if enabled)
    )


if __name__ == "__main__":
    main()
