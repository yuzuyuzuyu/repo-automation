import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { CandidateStore, SOAK_MS, retainedDocker, QEMU_VERSIONING } from '../src/docker_candidates.mjs';
import { api as versioning } from 'renovate/dist/modules/versioning/docker/index.js';
import { get as getVersioning } from 'renovate/dist/modules/versioning/index.js';
import { getRegistryRepository } from 'renovate/dist/modules/datasource/docker/common.js';
import { getDatasources } from 'renovate/dist/modules/datasource/index.js';
import { lookupUpdates } from 'renovate/dist/workers/repository/process/lookup/index.js';
import { getConfig } from 'renovate/dist/config/defaults.js';
import { init as initMemory } from 'renovate/dist/util/cache/memory/index.js';
import { GlobalConfig } from 'renovate/dist/config/global.js';
import { init as initLogger } from 'renovate/dist/logger/index.js';
import { resolveConfigPresets } from 'renovate/dist/config/presets/index.js';

process.env.LOG_LEVEL = 'fatal';
await initLogger();
const digest = (character) => `sha256:${character.repeat(64)}`;
const A = digest('a'), B = digest('b'), C = digest('c');
let now = Date.parse('2026-01-01T00:00:00Z');
const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'docker-candidates-'));
const file = path.join(directory, 'state.json');
let store = new CandidateStore(file, () => now);
const key = store.key('https://index.docker.io', 'library/python', '3.14.7-slim-trixie');
store.observe(key, A);
assert.equal(store.offer(key, B), null);
now += SOAK_MS - 1;
store.observe(key, B);
assert.equal(store.offer(key, C), null);
now += 1;
assert.equal(store.offer(key, C).digest, A);
now += SOAK_MS;
store.observe(key, C);
assert.equal(store.offer(key, C).digest, A, 'An offered candidate stays fixed until the base changes');
store = new CandidateStore(file, () => now);
assert.equal(store.offer(key, A).digest, B, 'A new run retains earlier observations');
assert.equal(store.offer(key, B).digest, B, 'Already-current image is a no-op');
now += SOAK_MS;
assert.equal(store.offer(key, B).digest, C, 'A no-op must not freeze future updates');
assert.equal(store.offer(key, C).digest, A, 'Existing offer survives an identical base');
const fresh = new CandidateStore(null, () => now);
fresh.observe(key, C);
assert.equal(fresh.offer(key, A), null, 'Lost history requires another complete soak');
const invalid = JSON.parse(fs.readFileSync(file, 'utf8'));
invalid.streams[key].candidates[0].firstSeen = now + 1;
fs.writeFileSync(file, JSON.stringify(invalid));
assert.throws(() => new CandidateStore(file, () => now), /Invalid candidate/);

// Exercise Renovate's actual lookup, including strict age filtering, digest
// replacement, branch timestamp handling, and a moving upstream tag.
now = Date.parse('2026-02-01T00:00:00Z');
store = new CandidateStore(null, () => now);
let upstream = A;
const config = { datasource: 'docker', packageName: 'python', depName: 'python', currentValue: '3.14.7-slim-trixie', currentDigest: C };
const source = {
  supportsCustomRegistry() { return true; },
  id: 'docker', defaultVersioning: 'docker', defaultRegistryUrls: ['https://index.docker.io'], registryStrategy: 'first', caching: false,
  async getReleases() { return { releases: [{ version: config.currentValue, releaseTimestamp: new Date(now).toISOString() }] }; },
  async getDigest(_config, tag) { return tag.startsWith('sha256:') ? tag : upstream; },
  async getImageArchitecture() { return null; },
};
Object.setPrototypeOf(source, getDatasources().get('docker'));
const ownedImages = ['ghcr.io/clammet/pyburlybot'];
const adapter = retainedDocker(source, store, { registryRepository: getRegistryRepository, versioning, ownedImages });
const datasources = getDatasources();
const original = datasources.get('docker');
datasources.set('docker', adapter);
const nativeNow = Date.now;
Date.now = () => now;
try {
  const defaults = getConfig();
  GlobalConfig.set({ ...defaults, cacheDir: directory });
  const policy = JSON.parse(fs.readFileSync(new URL('../config/docker-policy.json', import.meta.url)));
  const effective = (await resolveConfigPresets({ ...defaults, ...policy })).config;
  async function lookup(overrides = {}) {
    initMemory();
    const result = await lookupUpdates({ ...effective, ...config, manager: 'dockerfile', versioning: 'docker', pinDigests: true, ...overrides });
    const { val, err } = result.unwrap();
    assert.equal(err, undefined);
    assert.notEqual(val.skipReason, 'internal-error');
    return val;
  }
  assert.deepEqual((await lookup()).updates, []);
  now += 2 * 86400000;
  upstream = B;
  assert.deepEqual((await lookup()).updates, []);
  now += 86400000;
  const update = (await lookup()).updates.find((item) => item.updateType === 'digest');
  assert.equal(update?.newDigest, A, 'Mature A is proposed even though upstream now points to young B');
  assert.notEqual(update.pendingChecks, true);
  now += 3 * 86400000;
  assert.equal((await lookup()).updates[0].newDigest, A, 'Retain proposed A while its PR passes CI');
  assert.equal((await lookup({ currentDigest: A })).updates[0].newDigest, B, 'After A lands, propose mature B');
  assert.equal(await adapter.getDigest({ packageName: ownedImages[0], registryUrl: 'https://index.docker.io', currentValue: 'latest' }, 'latest'), B);
  assert.equal(await adapter.getDigest({ packageName: 'ghcr.io/attacker/pyburlybot', registryUrl: 'https://index.docker.io', currentValue: 'latest', currentDigest: C }, 'latest'), C);

  // New versions must accumulate observations before strict native filtering
  // exposes them. Custom version schemes must participate in the same policy.
  for (const custom of [false, true]) {
    now = Date.parse('2026-03-01T00:00:00Z');
    const base = custom ? 'qemu-v10.2.3' : '3.14.7-slim-trixie';
    const patch = custom ? 'qemu-v10.2.4' : '3.14.8-slim-trixie';
    const major = custom ? 'qemu-v11.0.0' : '4.0.0-slim-trixie';
    const packageName = custom ? 'tonistiigi/binfmt' : 'python';
    const versions = custom ? getVersioning(QEMU_VERSIONING) : versioning;
    const candidates = new CandidateStore(null, () => now);
    const registry = Object.create(source);
    const tags = { [base]: C, [patch]: A, [major]: B };
    registry.getReleases = async () => ({ releases: Object.keys(tags).map((version) => ({ version })) });
    registry.getDigest = async (_config, tag) => tag.startsWith('sha256:') ? tag : tags[tag];
    const wrapped = retainedDocker(registry, candidates, { registryRepository: getRegistryRepository, versioning, versioningFor: () => versions, ownedImages });
    datasources.set('docker', wrapped);
    const options = { currentValue: base, packageName, depName: packageName, versioning: custom ? QEMU_VERSIONING : 'docker' };
    assert.deepEqual((await lookup(options)).updates, []);
    now += SOAK_MS;
    const patches = (await lookup(options)).updates;
    assert.equal(patches.find((u) => u.updateType === 'patch')?.newDigest, A);
    assert.equal(patches.some((u) => u.updateType === 'major'), false);
    now += 4 * 86400000;
    assert.equal((await lookup(options)).updates.find((u) => u.updateType === 'major')?.newDigest, B);

    // A removed manifest must fail this run but cannot freeze all future runs.
    tags[patch] = B;
    now += SOAK_MS;
    await wrapped.getReleases({ ...options, registryUrl: 'https://index.docker.io' });
    now += SOAK_MS;
    registry.getDigest = async (_config, tag) => tag === A ? null : tag.startsWith('sha256:') ? tag : tags[tag];
    const digestConfig = { ...options, registryUrl: 'https://index.docker.io', currentDigest: C };
    await assert.rejects(wrapped.getDigest(digestConfig, patch), /unavailable/);
    assert.equal(await wrapped.getDigest(digestConfig, patch), B);
  }

  // Index observations cannot be mistaken for a platform-specific digest.
  now = Date.parse('2026-04-01T00:00:00Z');
  const platformStore = new CandidateStore(null, () => now);
  const platformSource = Object.create(source);
  platformSource.getImageArchitecture = async () => 'arm64';
  platformSource.getDigest = async (c, tag) => tag.startsWith('sha256:') ? tag : c.currentDigest ? A : B;
  const platformAdapter = retainedDocker(platformSource, platformStore, { registryRepository: getRegistryRepository, versioning, ownedImages });
  const platformConfig = { ...config, registryUrl: 'https://index.docker.io' };
  await platformAdapter.getReleases(platformConfig);
  assert.equal(await platformAdapter.getDigest(platformConfig, config.currentValue), C);
  now += SOAK_MS;
  assert.equal(await platformAdapter.getDigest(platformConfig, config.currentValue), A);
  await assert.rejects(platformAdapter.getDigest(platformConfig, '3.14.8-slim-trixie'), /still soaking/);

  const obsolete = platformStore.key('https://index.docker.io', 'library/python', '2.0.0');
  platformStore.subscribe(obsolete);
  now += 91 * 86400000;
  platformStore.subscribe(key);
  assert.equal(platformStore.state.streams[obsolete], undefined, 'Abandoned subscriptions expire');

} finally {
  datasources.set('docker', original);
  Date.now = nativeNow;
  fs.rmSync(directory, { recursive: true, force: true });
}
console.log('Docker candidates: retained age, frozen offers, no-op progress, state validation, real Renovate version/digest lookup, major delay, custom versions, availability recovery, architecture isolation and exact own-image exemptions passed.');
