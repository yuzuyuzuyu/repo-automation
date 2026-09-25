import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { init as initLogger } from 'renovate/dist/logger/index.js';
import { init, set } from 'renovate/dist/util/cache/memory/index.js';
import { resolveConfigPresets } from 'renovate/dist/config/presets/index.js';
import { massageConfig } from 'renovate/dist/config/massage.js';
import { validateConfig } from 'renovate/dist/config/validation.js';
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
await check(JSON.parse(fs.readFileSync(path.join(root, 'renovate.json'), 'utf8')));
const rules = ['non-major', 'major', 'pre-one', 'action-digest-review', 'renovate-engine', 'node-runtime'];
const notes = ['age-3-days', 'age-7-days'].map((name) => ({ extends: [preset(name)] }));
export function policy(profile, exceptions = []) {
  return {
    extends: [preset(profile)],
    packageRules: [...rules.map((name) => ({ extends: [preset(name)] })), ...exceptions, ...notes],
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
assert.deepEqual([...JSON.stringify(policy('public')).matchAll(matcher)].map((m) => m.groups.currentValue), Array(9).fill('v1.0.0'));
console.log('Renovate: recursive presets, rule order, release ages, lockfile exemption, manual holds and version tracking passed.');
