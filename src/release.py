"""Publish immutable patch releases of tested shared code, with safe retries."""

import json
import os
import re
import subprocess
from pathlib import PurePosixPath
from typing import Any

VERSION = re.compile(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)\Z")


def version_key(tag: str) -> tuple[int, ...]:
    match = VERSION.fullmatch(tag)
    if not match:
        raise ValueError(f"Invalid release version: {tag}")
    return tuple(int(part) for part in match.groups())


def affects_consumers(filename: str) -> bool:
    path = PurePosixPath(filename)
    return (
        filename.startswith("actions/")
        or (filename.startswith("src/") and filename != "src/release.py")
        or filename == ".github/workflows/renovate-runner.yml"
        or (
            len(path.parts) == 1
            and path.suffix == ".json"
            and filename not in {"package.json", "package-lock.json", "renovate.json"}
        )
    )


def choose_version(
    tags: dict[str, str],
    published: set[str],
    head: str,
    changed: list[str],
    requested: str,
) -> str | None:
    stable = {tag: sha for tag, sha in tags.items() if VERSION.fullmatch(tag)}
    latest = max(stable, key=version_key) if stable else None
    if requested:
        version_key(requested)
        if requested in stable:
            if stable[requested] != head:
                raise ValueError("Refusing to move an existing release tag")
            return None if requested in published else requested
        if latest and version_key(requested) <= version_key(latest):
            raise ValueError("New version must be greater than existing release tags")
        return requested
    current = [tag for tag, sha in stable.items() if sha == head]
    if current:
        tag = max(current, key=version_key)
        return None if tag in published else tag
    if not any(affects_consumers(filename) for filename in changed):
        return None
    if not latest:
        raise ValueError("Create the first version with a manual release")
    major, minor, patch = version_key(latest)
    return f"v{major}.{minor}.{patch + 1}"


def command(args: list[str]) -> str:
    return subprocess.check_output(args, text=True).strip()  # noqa: S603


def api(endpoint: str, paginate: bool = False) -> Any:
    args = ["gh", "api", endpoint]
    if paginate:
        args += ["--paginate", "--slurp"]
    result = json.loads(command(args))
    return [item for page in result for item in page] if paginate else result


def tested_current_head(repository: str, branch: str, head: str) -> bool:
    if api(f"repos/{repository}/commits/{branch}")["sha"] != head:
        return False
    runs = api(
        f"repos/{repository}/actions/workflows/ci.yml/runs?head_sha={head}&event=push&per_page=100"
    )["workflow_runs"]
    return bool(
        runs and runs[0]["status"] == "completed" and runs[0]["conclusion"] == "success"
    )


def main() -> None:
    repository = os.environ["GITHUB_REPOSITORY"]
    branch = os.environ["DEFAULT_BRANCH"]
    head = os.environ["RELEASE_SHA"]
    if command(["git", "rev-parse", "HEAD"]) != head:
        raise ValueError("Checkout does not match the tested release revision")
    if not tested_current_head(repository, branch, head):
        print("Release skipped: revision is not current with successful push CI")
        return
    names = command(["git", "tag", "--list", "v*"]).splitlines()
    tags = {
        tag: command(["git", "rev-parse", f"{tag}^{{commit}}"])
        for tag in names
        if VERSION.fullmatch(tag)
    }
    releases = api(f"repos/{repository}/releases?per_page=100", paginate=True)
    published = {
        release["tag_name"]
        for release in releases
        if not release["draft"] and not release["prerelease"]
    }
    baselines = [tag for tag in tags if tag in published]
    baseline = max(baselines, key=version_key) if baselines else None
    changed = (
        command(["git", "diff", "--name-only", baseline, head]).splitlines()
        if baseline
        else command(["git", "ls-tree", "-r", "--name-only", head]).splitlines()
    )
    tag = choose_version(tags, published, head, changed, os.environ.get("VERSION", ""))
    if tag is None:
        print("Release skipped: no unpublished consumer changes")
        return
    # A queued run must not publish over a newer default-branch revision.
    if not tested_current_head(repository, branch, head):
        print("Release skipped: revision changed while preparing the release")
        return
    if tag not in tags:
        command(
            [
                "gh",
                "api",
                "--method",
                "POST",
                f"repos/{repository}/git/refs",
                "-f",
                f"ref=refs/tags/{tag}",
                "-f",
                f"sha={head}",
            ]
        )
    # If tag creation succeeded but publication failed, rerunning uses the same
    # tag and revision. Existing refs are never updated or force-pushed.
    command(
        [
            "gh",
            "release",
            "create",
            tag,
            "--repo",
            repository,
            "--verify-tag",
            "--title",
            tag,
            "--generate-notes",
        ]
    )
    print(f"Published {tag} from {head}")


if __name__ == "__main__":
    main()
