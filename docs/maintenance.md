# Automatic maintenance

Renovate is the only dependency PR writer. Dependabot provides alerts, with its
security-update PRs disabled in repository settings. Public repositories run Renovate hourly and
queues eligible PRs for GitHub-native automerge. The main-branch ruleset requires
up-to-date branches, the existing CI/build checks and `Dependency update safety`.
The safety workflow reads only pinned shared action code and PR-head statuses;
it requires Renovate's successful artifact-generation check and blocks pending
release-age checks or failed Renovate checks. Status events refresh the gate
without waiting for another Renovate run after CI passes.

Routine releases soak for three days; majors soak for a week. Lockfile maintenance
is age-exempt because it has no release timestamp, but still needs successful
artifact generation and CI. Stale branches need a rebase and fresh CI before
merging. GitHub Actions digest changes and configured runtime/data migrations
still require review. Private repositories retain bot-managed merges because
the current GitHub plan does not provide their required branch protections.
The daily watchdog raises one issue for updates stalled
longer than eight days, with 48 hours of grace after a new bot commit, and
closes it when they recover.

The shared Renovate runner retains Docker digest candidates. For each third-party
registry/repository/tag/platform, it records the first time it observes a digest.
An update must have been observed for three days (seven for a major version).
Publisher timestamps cannot shorten that window. Full version tags remain useful
for selecting a version stream, but their contents can still change; the digest
selects immutable content.

If a tag points to A on Monday and B on Wednesday, A can become eligible on
Thursday. B keeps its own clock. Once offered against a particular base digest,
A stays fixed while that PR passes CI; after it merges, another mature candidate
can follow. The runner observes newer versions while older ones mature. It need
not install every intermediate build: it selects the newest eligible candidate
when there is no outstanding offer. A registry must still serve retained content
by digest. A missing manifest fails the current run and discards that offer so a
later run can select another mature candidate.

Observations are saved in the caller's `docker-candidates-v1` Actions artifact for
90 days, including safe observations from runs that later fail. Only this
workflow's default-branch schedule, dispatch or push runs can supply state; PR
and fork artifacts are rejected. Runs are serialized. Missing history starts a
new full observation window; invalid history fails closed. Dry runs do not save
observations. Inactive subscriptions expire after 90 days; active histories remain
subject to a 20 MB safety limit.

The exact fleet-owned image allowlist in `config/docker-policy.json` bypasses this
second delay. It includes the co-admin's Console Buffet image. These images are
published from our projects after their dependency checks; this exemption does
not extend to all images under an owner or registry. Deployment timers may follow
these application tags immediately. Third-party deployments instead consume
reviewed digests, including same-tag rebuilds, after Renovate and deployment sync.

[Renovate issue #45236](https://github.com/renovatebot/renovate/issues/45236) loses
the digest timestamp during branch checks. The timestamp-optional workaround now
exists only in the runner together with the adapter that enforces age on the
exact digest. The general preset requires timestamps. The runner loads its own
pinned workflow commit, verifies the engine version against the tested dependency,
and installs the adapter through Renovate's datasource registry. Engine upgrades
must pass the integration tests; no upstream installed files are patched. Once
the upstream timestamp bug is fixed, remove the runtime fallback after checking
both mature and young digests; retention is still needed for moving tags.

Lockfile maintenance has no branch-level release date. Package resolution must
still enforce the cutoff: fleet pnpm workspaces use 4,320 minutes, npm uses three
days, and uv uses `P3D`. The runner supplies npm/uv cutoffs to artifact generation
and disables npm lifecycle scripts there. Frozen installs verify recorded hashes;
resolver age checks apply when selecting new versions. Direct pins alone do not
protect newly selected transitive dependencies. pnpm additionally blocks exotic
transitive sources and provenance downgrades, with reviewed local exceptions.

OS package updates deliberately continue using authenticated distribution
repositories during rebuilds. A base digest freezes the starting filesystem,
not packages subsequently installed with apt/apk. This permits security fixes
without fragile historical package pins; image scans and CI still apply. A delay
and content hashes reduce risk but do not authenticate the publisher. Consistent
signature/provenance verification at deployment is a separate control, including
for our own applications.

Security scans run daily and after image builds. All severities remain in the
reports; high/critical findings start a 72-hour remediation window. A matching green or
pending Renovate update extends that window to eight days; exposed credentials
need immediate attention and are redacted from saved reports. For images
owned here, a fixable finding requests one fresh uncached rebuild. Renovate and
the publication/deployment machinery get time to land dependency fixes. Findings
still present after the window produce one issue, updated without repeated
comments. A complete clean scan closes the issue. Scan, registry, authentication,
and upload errors still fail Actions; they are never interpreted as a clean scan.

Code scanning receives each completed image scan, including an empty report when
findings disappear. Historical SARIF categories are preserved, so GitHub can
mark absent findings fixed. Reports retain all severities rather than dismissing
real vulnerabilities just to reduce the count. Private repositories without code
scanning use the same issue lifecycle and downloadable JSON reports.

Retry state is a 30-day artifact from successful default-branch scans of this
workflow only. State excludes secrets; PR artifacts are never consumed. If state
expires, the next scan starts a new bounded remediation window. A failed scanner
cannot close an issue or erase the last successful state. The workflow's
concurrency group serializes scans, including scans triggered by rebuilds.

The shared implementation and policy are maintained in
[repo-automation](https://github.com/yuzuyuzuyu/repo-automation). Actions use a pinned release
commit; Renovate proposes updates. Scan targets and stable SARIF categories remain
in `.github/maintenance.json`. See the shared repository for tests and release
instructions. Repository-specific builds and deployment safeguards remain local.

The watchdog retries current-revision workflow failures once, then reports
persistent failures in one automatically resolved issue. Publication verifies
that CI passed and the revision is still current before using registry or
production credentials. Build/test failures remain blocking checks.
