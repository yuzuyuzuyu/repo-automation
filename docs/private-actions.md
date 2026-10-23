# Private Actions budget

This is an opt-in policy for the three private repositories. `public.json`,
`private.json`, the default preset, existing package rules, and existing public
workflow behavior are unchanged. New policy lives in `private-budget.json`,
`private-review-rebases.json`, and `private/`.

## Dependency maintenance

Use `github>yuzuyuzuyu/repo-automation:private-budget#<version>` as the root
preset. Keep the existing package rules and local exceptions. Append
`private-review-rebases` as the **last package rule**, after all local holds and
age notes. It changes only non-automerged updates to conflict-only rebasing.
Automerge still requires a branch updated against main and successful CI.

Routine updates run before 06:00 Monday and Thursday in Australia/Melbourne.
`updateNotScheduled: false` also prevents routine existing branches from being
updated outside those windows. Compatible minor/patch updates are grouped by
ecosystem, while local Node/Go grouping and shared-automation grouping remain
intact. Major updates and action digest review retain their existing rules.
There are at most three newly admitted concurrent update PRs; existing PRs are
not closed to enforce that limit. Lockfile maintenance stays weekly, without
an inappropriate release-age gate.

The generated manual Renovate dispatch passes a trusted operator override for
schedule/update timing, including package-specific schedules. It preserves age,
review and test gates. Vulnerability alerts are exempt from routine timing, but
**an exemption cannot start a runner**: automated Renovate discovery/merging still
waits for its next invocation. Use manual dispatch for an urgent dependency fix.
Daily security scans and post-build scans retain their original cadence.
Bot-managed merges are also re-evaluated only when Renovate runs: a PR whose CI
finishes after that run can wait three or four days. Rebases after another merge
can require a subsequent run, so grouped PRs and manual dispatch matter for queue
throughput as well as cost.

## Shared workflow wrappers

GitHub requires `on.schedule` and `workflow_run` in each caller; reusable workflows
cannot supply those triggers. `private/workflows.json` is the source of truth,
and `src/private_workflows.py` generates the private Renovate/watchdog wrappers
and updates yuzuyu's pin-sync fallback schedule. For example, from this checkout:

```sh
.venv/bin/python src/private_workflows.py yuzuyu ../yuzuyu \
  --ref <published-full-commit-sha> --version <published-version>
# Add --check to verify the checked-in wrappers without changing them.
```

The renderer requires a full commit SHA and a semantic version. Publish shared
changes first, then regenerate callers against that release. Private configuration
changes also qualify for automatic patch releases. Each consumer stays pinned;
editing this checkout does not silently change a consumer's policy.

The watchdog runs weekly and on first-attempt failures of listed workflows on
trusted main-branch push/schedule/manual runs. It does not subscribe to itself,
PR workflows or retry attempts. The existing controller retains its retry
allowlist and once-only retry limit. Weekly stale-update reporting is slower
than daily reporting; eligible first failures still receive prompt attention.

## Selecting CI work

`actions/private-changes` loads `private/ci-profiles.json` from the pinned action,
not from the caller checkout. Use it in the existing test job after checkout with
`fetch-depth: 0`; this keeps the required test status and avoids a separate
billable detection job. Its explicit profile input is one of `yuzuyu`,
`passwordreset`, `webhook-image-updater`, or `dependency-pins`.

PRs compare the merge base with the checked-out revision. Pushes include **all**
commits from the previous tip, and renames include both paths. Unknown inputs,
missing history and Git/API failures select full checks. Manual runs and scheduled
repair fallbacks always select full work, including manual image rebuilds.

Yuzuyu keeps full Ansible lint/syntax checking for actual Ansible and shared
inputs because playbooks import across services. Documentation and application-only
changes skip it. Upgallery integration and pyburlybot tests run for their own
inputs or shared changes. Docker inputs and build workflows still trigger builds;
Go tests/tool-only changes can skip image construction. Documentation-only changes
still complete the existing test job successfully.

The dependency-pin profile compares the triggering successful CI revision with
the last run whose `synchronize / run` job actually succeeded. A green no-op gate
is not a deployment baseline. It examines the latest 20 successful workflow runs;
if no deployed baseline can be proven, it synchronizes conservatively. Changes to
pin variables, updater templates, inventory or shared inputs still synchronize.
The existing publication gate verifies current main and successful CI. The weekly
fallback and manual dispatch repair host drift even without a Git change.

## Rollout and limits

Repository-level Actions disablement is separate from these source changes.
Updating workflows does not enable Actions. Re-enable only when ready to resume
CI, publication and maintenance. Preserve any previous SHA-pinning requirement
when restoring repository Actions permissions.

Savings depend on dependency churn and human activity; these settings are not a
hard 2,000-minute cap. Target 1,500–1,600 minutes/month and check billing after the
first active cycle. Keep the existing spending limit. Cron delivery can be delayed;
Melbourne windows are deliberately wider than the scheduled 02:xx/03:xx start.
A schedule delivered outside its window waits until a later invocation or a
manual dispatch. Existing GitHub private-plan branch-protection limitations remain.
