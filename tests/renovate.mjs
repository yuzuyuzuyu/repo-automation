import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { init as initLogger } from 'renovate/dist/logger/index.js';
import { init, set } from 'renovate/dist/util/cache/memory/index.js';
import { resolveConfigPresets } from 'renovate/dist/config/presets/index.js';
import { massageConfig } from 'renovate/dist/config/massage.js';
import { validateConfig } from 'renovate/dist/config/validation.js';
import { extractPackageFile as extractRegex } from 'renovate/dist/modules/manager/custom/regex/index.js';
import { extractPackageFile as extractActions } from 'renovate/dist/modules/manager/github-actions/extract.js';
import { GlobalConfig } from 'renovate/dist/config/global.js';
import { QEMU_VERSIONING } from '../src/docker_candidates.mjs';
import { mergeChildConfig } from 'renovate/dist/config/utils.js';
import { configureRenovate } from '../src/renovate_config.mjs';
import { applyPackageRules } from 'renovate/dist/util/package-rules/index.js';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const repository = 'yuzuyuzuyu/repo-automation';
export const preset = (name) => `github>${repository}:${name}#v1.0.0`;
process.env.LOG_LEVEL = 'warn';
await initLogger();
init();
// Exercise Renovate's real recursive preset resolver without fetching unpublished files.
for (const file of fs.readdirSync(root).filter((f) => f.endsWith('.json') && !['package.json', 'package-lock.json', 'renovate.json'].includes(f))) {
  const name = file.slice(0, -5);
  const data = JSON.parse(fs.readFileSync(path.join(root, file), 'utf8'));
  for (const suffix of [`:${name}`, `//${name}`, ...(name === 'default' ? [''] : [])]) {
    set(`preset:github>${repository}${suffix}#v1.0.0`, data);
  }
}
export async function resolve(config) {
  return (await resolveConfigPresets(massageConfig(structuredClone(config)))).config;
}
export async function check(config) {
  const resolved = await resolve(config);
  const { errors } = await validateConfig('repo', resolved);
  assert.deepEqual(errors, []);
  return resolved;
}
// Resolve this checkout's self-configuration through the same local preset cache.
await check(JSON.parse(fs.readFileSync(path.join(root, 'renovate.json'), 'utf8').replace(/(github>yuzuyuzuyu\/repo-automation(?::[a-z0-9-]+)?#)v\d+\.\d+\.\d+/g, '$1v1.0.0')));
const rules = ['non-major', 'major', 'pre-one', 'action-digest-review', 'renovate-engine', 'node-runtime'];
const notes = ['age-3-days', 'age-7-days'].map((name) => ({ extends: [preset(name)] }));
export function policy(profile, exceptions = []) {
  return {
    extends: [preset(profile)],
    packageRules: [...rules.map((name) => ({ extends: [preset(name)] })), ...exceptions, { extends: [preset('automation-updates')] }, ...notes],
  };
}
async function evaluate(config, overrides = {}) {
  return applyPackageRules({ ...config, manager: 'npm', datasource: 'npm', packageName: 'example', depName: 'example', currentVersion: '1.0.0', updateType: 'patch', ...overrides });
}
for (const profile of ['public', 'private']) {
  const config = await check(policy(profile));
  assert.equal(config.platformAutomerge, profile === 'public');
  assert.equal(config.lockFileMaintenance.minimumReleaseAge, null);
  assert.equal(config.lockFileMaintenance.automerge, true);
  for (const updateType of ['patch', 'minor', 'major']) {
    const effective = await evaluate(config, { updateType });
    const days = updateType === 'major' ? 7 : 3;
    assert.equal(effective.minimumReleaseAge, `${days} days`);
    assert(effective.prBodyNotes.some((note) => note.includes(`**${days} days**`)));
    assert.equal(effective.automerge, true);
  }
  for (const updateType of ['digest', 'pinDigest']) {
    assert.equal((await evaluate(config, { manager: 'github-actions', updateType })).automerge, false);
  }
}
// Notes run after local exceptions, so exempt updates never acquire an age-gate claim.
for (const age of [null, '0 days']) {
  const config = await check(policy('public', [{ matchPackageNames: ['example'], minimumReleaseAge: age }]));
  assert.equal((await evaluate(config)).prBodyNotes, undefined);
}
const guarded = await check(policy('public', [{ matchPackageNames: ['example'], matchUpdateTypes: ['major'], automerge: false }]));
assert.equal((await evaluate(guarded, { updateType: 'major' })).automerge, false);
const publicConfig = await check(policy('public'));
assert.equal(publicConfig.statusCheckWhen.artifactError, 'always');
assert.equal(publicConfig.internalChecksFilter, 'strict');
// All references in a consumer can advance together, including the public/private preset.
const matcher = new RegExp(publicConfig.customManagers.find((m) => m.depNameTemplate === repository).matchStrings[0], 'g');
assert.deepEqual([...JSON.stringify(policy('public')).matchAll(matcher)].map((m) => m.groups.currentValue), Array(10).fill('v1.0.0'));
console.log('Renovate: recursive presets, rule order, release ages, lockfile exemption, manual holds and version tracking passed.');

// The shared exception runs after general and local rules, including majors.
for (const profile of ['public', 'private']) {
  const config = await check(policy(profile, [{ matchDatasources: ['github-tags'], minimumReleaseAge: '7 days' }]));
  for (const manager of ['github-actions', 'custom.regex', 'renovate-config']) {
    for (const updateType of ['patch', 'minor', 'major', 'digest', 'pinDigest']) {
      const effective = await evaluate(config, {
        manager, datasource: 'github-tags', updateType,
        packageName: repository, depName: repository,
      });
      assert.equal(effective.minimumReleaseAge, null);
      assert.equal(effective.prBodyNotes, undefined);
      assert.equal(effective.automerge, !(manager === 'github-actions' && ['digest', 'pinDigest'].includes(updateType)));
    }
  }
  assert.equal((await evaluate(config, { datasource: 'github-tags' })).minimumReleaseAge, '7 days');
}
console.log('Shared automation updates: no age delay, unrelated ages and action digest review preserved.');

// Timestamp fallback exists only inside the runner that retains exact candidates.
const runtimePolicy = JSON.parse(fs.readFileSync(path.join(root, 'config/docker-policy.json'), 'utf8'));
await check(runtimePolicy);
for (const name of ['python', 'docker/dockerfile', 'node']) {
  const effective = await evaluate(publicConfig, { datasource: 'docker', packageName: name, depName: name, updateType: 'digest', minimumReleaseAgeBehaviour: 'timestamp-required' });
  assert.equal(effective.minimumReleaseAgeBehaviour, 'timestamp-required');
}
const runnerConfig = configureRenovate({ allowedCommands: ['^trusted-command$'] }, runtimePolicy);
assert.equal(runnerConfig.allowScripts, false);
assert.equal(runnerConfig.customEnvVariables.npm_config_ignore_scripts, undefined);
const runtimeValidation = await validateConfig('global', massageConfig(runnerConfig));
assert.deepEqual(runtimeValidation.errors, []);
assert.deepEqual(runtimeValidation.warnings, []);
assert.throws(() => configureRenovate({ force: { packageRules: [] } }, runtimePolicy), /discards repository policy/);
// Exercise Renovate's actual global -> repository merge, rather than hand-appending
// rules in an order which the runtime does not use.
const callerConfig = await check({ ...policy('public', [
  { matchPackageNames: ['python'], groupName: 'Python runtime', automerge: false },
  { matchPackageNames: ['local-hold'], enabled: false },
]), labels: ['dependencies'] });
const runtime = await resolve(mergeChildConfig(runnerConfig, callerConfig));
assert.deepEqual(runtime.allowedCommands, ['^trusted-command$']);
assert.deepEqual(runtime.labels, ['dependencies']);
assert.equal((await evaluate(runtime)).automerge, true);
assert.equal((await evaluate(runtime, { updateType: 'major' })).minimumReleaseAge, '7 days');
assert.equal((await evaluate(runtime, { manager: 'github-actions', updateType: 'digest' })).automerge, false);
assert.equal((await evaluate(runtime, { packageName: 'local-hold' })).enabled, false);
const pythonRuntime = await evaluate(runtime, { datasource: 'docker', packageName: 'python', depName: 'python', updateType: 'minor' });
assert.equal(pythonRuntime.groupName, 'Python runtime');
assert.equal(pythonRuntime.automerge, false);
assert.equal(pythonRuntime.minimumReleaseAge, '3 days');
assert.equal((await evaluate(runtime, { datasource: 'docker', packageName: 'python', updateType: 'digest', minimumReleaseAgeBehaviour: 'timestamp-required' })).minimumReleaseAgeBehaviour, 'timestamp-optional');
console.log('Runtime configuration merge: repository grouping, manual holds, action digest review, labels, and non-Docker ages preserved.');
for (const name of runtimePolicy.packageRules.at(-1).matchPackageNames) {
  for (const updateType of ['digest', 'pinDigest', 'patch', 'major']) {
    assert.equal((await evaluate(runtime, { datasource: 'docker', packageName: name, depName: name, updateType })).minimumReleaseAge, null);
  }
}
assert.equal((await evaluate(runtime, { datasource: 'docker', packageName: 'ghcr.io/attacker/pyburlybot', depName: 'ghcr.io/attacker/pyburlybot', updateType: 'digest' })).minimumReleaseAge, '3 days');
assert.equal((await evaluate(runtime, { datasource: 'docker', packageName: 'python', depName: 'python', updateType: 'major' })).minimumReleaseAge, '7 days');
const engineImage = JSON.parse(fs.readFileSync(path.join(root, 'config/renovate-image.json'))).image;
const engineVersion = JSON.parse(fs.readFileSync(path.join(root, 'package.json'))).devDependencies.renovate;
assert(engineImage.startsWith(`ghcr.io/renovatebot/renovate:${engineVersion}@sha256:`));
console.log('Runtime Docker policy: retained-candidate gating, exact own-image exemptions and matching engine pins passed.');

// Private budgeting is opt-in; verify the effective policy after consumer overrides.
const budgetPolicy = policy('private-budget', [
  { matchPackageNames: ['mariadb'], matchUpdateTypes: ['major'], automerge: false },
  { matchPackageNames: ['go', 'golang'], groupName: 'Go toolchain' },
]);
budgetPolicy.packageRules.push({ extends: [preset('private-review-rebases')] });
const budget = await check(budgetPolicy);
assert.equal(budget.prConcurrentLimit, 3);
assert.equal(budget.platformAutomerge, false);
assert.equal(budget.updateNotScheduled, false);
assert.equal(budget.internalChecksFilter, 'strict');
assert.equal(budget.vulnerabilityAlerts.updateNotScheduled, true);
assert.equal(budget.lockFileMaintenance.automerge, true);
for (const updateType of ['patch', 'minor']) {
  const effective = await evaluate(budget, { updateType });
  assert.equal(effective.groupName, 'Private npm maintenance');
  assert.equal(effective.rebaseWhen, 'behind-base-branch');
  assert.equal(effective.automerge, true);
}
assert.equal((await evaluate(budget, { updateType: 'major' })).groupName, undefined);
for (const updateType of ['digest', 'pinDigest']) {
  const effective = await evaluate(budget, { manager: 'github-actions', updateType });
  assert.equal(effective.automerge, false);
  assert.equal(effective.rebaseWhen, 'conflicted');
  assert.equal(effective.groupName, undefined);
}
const database = await evaluate(budget, { datasource: 'docker', packageName: 'mariadb', depName: 'mariadb', updateType: 'major' });
assert.equal(database.automerge, false);
assert.equal(database.rebaseWhen, 'conflicted');
const go = await evaluate(budget, { manager: 'dockerfile', datasource: 'docker', packageName: 'golang', depName: 'golang' });
assert.equal(go.groupName, 'Go toolchain');
for (const profile of ['public', 'private']) {
  const original = await check(policy(profile));
  assert.equal(original.updateNotScheduled, undefined);
  assert.equal((await evaluate(original)).groupName, undefined);
  assert.equal((await evaluate(original, { manager: 'github-actions', updateType: 'digest' })).rebaseWhen, 'behind-base-branch');
}
console.log('Private budget: grouping, manual-review rebases, fresh-base automerge, security exceptions and unchanged existing profiles passed.');

const privateAutomation = await evaluate(budget, { manager: 'github-actions', datasource: 'github-tags', packageName: repository, depName: repository });
assert.equal(privateAutomation.groupName, 'Repository automation');

// Verify extraction, not just regex text: these inputs must become actual
// Renovate dependencies, including implicit tools downloaded by pinned actions.
const buildDigest = `sha256:${'a'.repeat(64)}`;
const workflow = `jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: docker/setup-buildx-action@${'b'.repeat(40)} # v4.4.1
        with:
          version: v0.38.1
          # renovate: datasource=docker depName=moby/buildkit
          driver-opts: image=moby/buildkit:v0.34.1@${buildDigest}
      - uses: docker/setup-qemu-action@${'c'.repeat(40)} # v4.4.0
        with:
          # renovate: datasource=docker depName=tonistiigi/binfmt
          image: tonistiigi/binfmt:qemu-v10.2.3@${buildDigest}
`;
const manager = publicConfig.customManagers.find((m) => m.matchStrings?.some((pattern) => pattern.includes('driver-opts')));
const extracted = extractRegex(workflow, '.github/workflows/build.yml', manager).deps;
assert.deepEqual(extracted.map(({ depName, currentValue, currentDigest }) => ({ depName, currentValue, currentDigest })), [
  { depName: 'moby/buildkit', currentValue: 'v0.34.1', currentDigest: buildDigest },
  { depName: 'tonistiigi/binfmt', currentValue: 'qemu-v10.2.3', currentDigest: buildDigest },
]);
GlobalConfig.set({ localDir: root });
const actions = await extractActions(workflow, '.github/workflows/build.yml');
assert(actions.deps.some((dep) => dep.packageName === 'docker/buildx' && dep.currentValue === 'v0.38.1'));
const qemu = await evaluate(publicConfig, { datasource: 'docker', packageName: 'tonistiigi/binfmt', depName: 'tonistiigi/binfmt' });
assert.equal(qemu.versioning, QEMU_VERSIONING);
assert.equal((await evaluate(publicConfig, { manager: 'github-actions', datasource: 'node-version', packageName: 'actions/node-versions', depName: 'node' })).groupName, 'Node runtime');
console.log('Buildx, BuildKit, QEMU and CI runtime dependency extraction/grouping passed.');
