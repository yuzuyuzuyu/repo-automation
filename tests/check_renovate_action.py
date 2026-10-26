"""Run the workflow's pinned Renovate action and inspect its Docker arguments.

The real bundled action does the parsing; only the Docker executable is replaced.
This catches input-contract regressions without credentials or a Docker daemon.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/renovate-runner.yml").read_text()
    )
    steps = workflow["jobs"]["renovate"]["steps"]
    step = next(s for s in steps if s.get("uses", "").startswith("renovatebot/"))
    repository, revision = step["uses"].split("@")
    assert repository == "renovatebot/github-action"
    assert re.fullmatch(r"[0-9a-f]{40}", revision)
    image = json.loads((ROOT / "config/renovate-image.json").read_text())["image"]
    version = image.removeprefix("ghcr.io/renovatebot/renovate:")

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        workspace = root / "workspace"
        workspace.mkdir()
        (workspace / "automation").symlink_to(ROOT, target_is_directory=True)
        runner_temp = root / "runner-temp"
        runner_temp.mkdir()
        substitutions = {
            "${{ runner.temp }}": str(runner_temp),
            "${{ github.workspace }}": str(workspace),
            "${{ steps.engine.outputs.version }}": version,
            "${{ steps.app-token.outputs.token }}": "unused-test-token",
        }

        def expand(value: str) -> str:
            for expression, replacement in substitutions.items():
                value = value.replace(expression, replacement)
            assert "${{" not in value, value
            return value

        # Use the actual workflow shell to create the command file.
        subprocess.run(
            [
                "bash",
                "-euo",
                "pipefail",
                "-c",
                next(s["run"] for s in steps if s.get("id") == "engine"),
            ],
            cwd=workspace,
            env={
                **os.environ,
                "RUNNER_TEMP": str(runner_temp),
                "GITHUB_OUTPUT": str(root / "outputs"),
            },
            check=True,
        )
        bundle = root / "action.mjs"
        if len(sys.argv) == 2:
            bundle.write_bytes(Path(sys.argv[1]).read_bytes())
        else:
            bundle.write_bytes(
                subprocess.check_output(
                    [
                        "gh",
                        "api",
                        f"repos/{repository}/contents/dist/index.js?ref={revision}",
                        "-H",
                        "Accept: application/vnd.github.raw+json",
                    ]
                )
            )
        tools = root / "bin"
        tools.mkdir()
        docker = tools / "docker"
        docker.write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys\n"
            "with open(os.environ['DOCKER_ARGS_LOG'], 'a') as output:\n"
            " output.write(json.dumps(sys.argv[1:]) + '\\n')\n"
            f"if '--version' in sys.argv: print({version.split('@')[0]!r})\n"
        )
        docker.chmod(0o755)
        log = root / "docker.jsonl"
        # Do not pass operator credentials to the action under test.
        environment = {
            "PATH": f"{tools}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(root),
            "DOCKER_ARGS_LOG": str(log),
            "RENOVATE_AUTOMATION_PATH": str(workspace / "automation"),
            "RENOVATE_CANDIDATE_STATE": str(
                runner_temp / "renovate-candidates/state.json"
            ),
            **{
                f"INPUT_{key.upper()}": expand(value)
                for key, value in step["with"].items()
            },
        }
        result = subprocess.run(
            ["node", str(bundle)],
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        arguments = next(call for call in calls if call[-1] == "/renovate-command.sh")
        mounts = [
            arguments[i + 1] for i, value in enumerate(arguments) if value == "--volume"
        ]
        assert mounts == [
            f"{runner_temp}/renovate-command.sh:/renovate-command.sh",
            f"{runner_temp}:{runner_temp}",
            f"{workspace}/automation:{workspace}/automation:ro",
        ], mounts
        assert arguments[-2] == image, arguments
        for variable in ("RENOVATE_AUTOMATION_PATH", "RENOVATE_CANDIDATE_STATE"):
            assert any(
                arguments[i : i + 2] == ["--env", variable]
                for i in range(len(arguments))
            )
        assert (runner_temp / "renovate-command.sh").stat().st_mode & 0o111
        print(
            "Pinned Renovate action passes separate mounts, adapter environment, and launcher."
        )


if __name__ == "__main__":
    main()
