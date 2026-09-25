# Repository automation

Shared GitHub Actions, Renovate policy and maintenance controllers for repositories
owned by this fleet. Consumers use a full commit SHA with a release-version
comment; Renovate proposes updates to both the actions and versioned presets.
No submodules, vendored scripts or synchronization command are needed.

## What lives here

| Component | Purpose |
| --- | --- |
| `.github/workflows/renovate-runner.yml` | App authentication, lookup token routing, keepalive, branch-protection preflight and pinned Renovate engine. |
| `actions/readiness` | Publish `Dependency update safety` from Renovate statuses on the exact PR head. No checkout or PR code execution. |
| `actions/watchdog` | Check stalled updates and retry allowlisted workflow failures once. |
| `actions/security-scan` | Install pinned Trivy, scan configured targets, reconcile issues, save reports/state, optionally upload SARIF and request a rebuild. |
| `actions/publication` | Emit `ready` only for the current default-branch revision with successful CI. Existing PR build behavior is preserved; publishers still exclude PRs themselves. |
| `actions/host-audit` | Reconcile a completed host audit without performing infrastructure operations. |
| `actions/keepalive` | Re-enable active or inactivity-disabled workflows, leaving manual disables alone. |
| `public.json`, `private.json`, `default.json` | Common Renovate policy and visibility-specific merge behavior. |
| Individual rule presets | Shared rules referenced at their original position so local exceptions and age annotations preserve precedence. |
| `src/`, `tests/` | Controller implementations, decision tests, action integration tests and Renovate policy tests. |

Repository-specific builds, package publishing, deployments, database upgrades and
infrastructure playbooks remain in their owning repositories. Each consumer keeps
its schedules, workflow/job names, permissions, `.github/maintenance.json`, scan
categories, retry allowlist and custom Renovate managers/exceptions.

See [maintenance behavior](docs/maintenance.md) for remediation windows, quiet
issue updates, lockfiles, age gates and bounded retries.

## Consumer contract

Use Linux runners with Python 3.12 or newer and `gh` available (the hosted Ubuntu
runner supplies both). The scan action installs Trivy. The publication action
expects the consumer checkout and `.github/maintenance.json` to exist; watchdog
and security-scan check out the default branch themselves. Readiness requires no
checkout. All scripts resolve from the downloaded, pinned action directory.

Keep event triggers and concurrency in the caller. In particular, readiness runs
on `pull_request_target`, Renovate `status` events, completion of the caller's
`Renovate` workflow and manual dispatch. Never check out PR code in that job.

The reusable Renovate workflow requires these named values, forwarded explicitly
so calls also work across owners:

- Input `app-client-id`: caller variable `RENOVATE_APP_CLIENT_ID`.
- Secret `RENOVATE_APP_PRIVATE_KEY`: the installed Renovate GitHub App's key.
- Secret `RENOVATE_GITHUB_LOOKUP_TOKEN`: the separate token for public GitHub tag,
  release and changelog lookups.

The App needs contents, pull requests, workflows, issues and commit statuses
write access, plus checks and vulnerability alerts read access. Credentials
remain in each consumer or its organization's Actions secret store; they are
never stored here or forwarded to other consumers. Installation tokens remain
scoped to the invoking repository.

For public repositories, `native-automerge: true` requires an enabled main-branch
ruleset with strict up-to-date checks, project CI/build checks and
`Dependency update safety` from GitHub Actions (integration ID 15368). Enable
GitHub's **Allow auto-merge** setting. The runner fails its preflight if that
protection is missing. Private repositories currently use daily bot-managed
merges and pass `native-automerge: false` because their plan lacks those rulesets.

The `renovate-config` input is trusted operator configuration, not PR input. Only
convex-googly-auth currently supplies it, to allow its exact changeset-generation
command. Other consumers use the empty default.

For security scans, retain permissions for contents, actions, issues, packages,
pull requests, checks and statuses from the existing wrapper. Set `upload-sarif`
to `true` only when code scanning is available and grant `security-events: write`.
The `rebuild-workflow` input is the local allowlisted build workflow; leave it
empty where no image rebuild is supported. State is saved before dispatch.

## Developing and releasing

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
npm ci --ignore-scripts
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy src
.venv/bin/python -m unittest discover -s tests -v
npm test
actionlint
```

CI runs these checks. Production controllers are typed and linted with the same
strict checks that caught the previous consumer CI failure. Policy tests use the
actual pinned Renovate engine, including recursive preset resolution and package
rule evaluation. The engine container and test dependency update in one PR.

After merging a change and passing main CI, run **Release shared automation**
with a new semantic version such as `v1.1.0`. It checks the selected revision is
current and tested, creates a new tag and publishes release notes. It refuses to
move an existing tag. Breaking action inputs or policy contracts require a major
version; preserve workflow/check names and SARIF categories when compatible.

Renovate tracks GitHub Action references and the custom manager in `default.json`
tracks preset tags. Consumers receive normal dependency PRs, with their usual
release-age and CI policies. Never use a floating `main` or movable `v1` reference
for privileged actions. Roll back by restoring the previous action SHA and
preset version together; old releases remain available.

## Bootstrapping this repository

This repository must remain public for cross-owner consumers to download its
code without additional credentials. Allow GitHub Actions and the third-party
actions referenced here. Existing consumers do not need a new secret just to use
these shared actions or presets.

Its own hourly Renovate job stays disabled until `RENOVATE_APP_CLIENT_ID` is set.
Install the Renovate App on this repository, then populate the two secrets and
set the client ID last. Secret values cannot be read back from other repositories
through the GitHub API. These commands prompt for values without placing them in
shell history:

```sh
gh secret set RENOVATE_APP_PRIVATE_KEY --repo yuzuyuzuyu/repo-automation
gh secret set RENOVATE_GITHUB_LOOKUP_TOKEN --repo yuzuyuzuyu/repo-automation
gh variable set RENOVATE_APP_CLIENT_ID --repo yuzuyuzuyu/repo-automation
```

For the multiline private key, file input is also supported:
`gh secret set RENOVATE_APP_PRIVATE_KEY --repo yuzuyuzuyu/repo-automation < app-key.pem`.
Keep the key file outside the checkout. Before enabling this repo's Renovate,
require `test` and `Dependency update safety` on main, require up-to-date branches,
and enable auto-merge. A manual Renovate dry run verifies the installation.

After adding or rotating credentials, run **Check Renovate credentials** (or
`gh workflow run check-credentials.yml --repo yuzuyuzuyu/repo-automation`). It
checks the lookup token and App credentials independently, including the public
Aqua Security and shared-repository tag lookups, plus the installation permissions
Renovate needs. No
secret values are printed. An `Invalid keyData` error means the App key cannot be
parsed; upload the complete PEM using file input to avoid truncated multiline
pastes. After that check passes, run Renovate with `dry-run` enabled to verify the
full configuration before its next scheduled run.

The lookup token must also satisfy the organization policy for the public shared
repository. GitHub defaults to a maximum lifetime of 366 days for fine-grained
PATs. To permit a non-expiring lookup token, the organization owner must remove
that maximum-lifetime requirement in Personal access tokens settings. An upstream
lookup succeeding does not prove organization access works; rerun the credential
check after changing either the token or its policy. Changing only the policy
does not require replacing the stored secret. If regenerating changes the token
value, replace `RENOVATE_GITHUB_LOOKUP_TOKEN` in every consumer using it.
