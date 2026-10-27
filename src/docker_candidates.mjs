import fs from 'node:fs';
import path from 'node:path';

export const SOAK_MS = 3 * 24 * 60 * 60 * 1000;
const digestPattern = /^sha256:[a-f0-9]{64}$/;
const isDockerTag = (tag) => typeof tag === 'string' && /^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$/.test(tag);
const MAX_BYTES = 20_000_000;
const HISTORY_MS = 90 * 86400000;
export const QEMU_VERSIONING = "regex:^qemu-v(?<major>\\d+)\\.(?<minor>\\d+)\\.(?<patch>\\d+)$";

// State contains registry observations only, never configuration or executable code.
export class CandidateStore {
  constructor(file, now = () => Date.now()) {
    this.file = file;
    this.now = now;
    this.state = { schema: 1, streams: {} };
    if (file && fs.existsSync(file)) {
      if (fs.statSync(file).size > MAX_BYTES) throw new Error('Docker candidate state exceeds limit');
      this.state = JSON.parse(fs.readFileSync(file, 'utf8'));
    }
    this.validate();
  }

  validate() {
    const { schema, streams } = this.state;
    if (schema !== 1 || !streams || Array.isArray(streams) || typeof streams !== 'object') throw new Error('Invalid Docker candidate state');
    for (const [key, stream] of Object.entries(streams)) {
      const parts = JSON.parse(key);
      if (!Array.isArray(parts) || parts.length !== 4 || parts.some((x) => typeof x !== 'string')) throw new Error('Invalid candidate stream');
      if (!Array.isArray(stream.candidates) || !stream.offers || Array.isArray(stream.offers) || typeof stream.offers !== 'object') throw new Error('Invalid candidate history');
      for (const timestamp of [stream.requestedAt, stream.observedAt]) {
        if (timestamp !== undefined && (!Number.isSafeInteger(timestamp) || timestamp < 0 || timestamp > this.now())) throw new Error('Invalid stream timestamp');
      }
      const seen = new Set();
      for (const candidate of stream.candidates) {
        if (!digestPattern.test(candidate.digest) || seen.has(candidate.digest) ||
            !Number.isSafeInteger(candidate.firstSeen) || candidate.firstSeen < 0 || candidate.firstSeen > this.now()) throw new Error('Invalid candidate observation');
        seen.add(candidate.digest);
      }
      for (const [base, digest] of Object.entries(stream.offers)) {
        if (base !== 'unpinned' && !digestPattern.test(base)) throw new Error('Invalid candidate base');
        if (!seen.has(digest)) throw new Error('Unknown offered candidate');
      }
    }
  }

  key(registry, repository, tag, architecture = '') {
    return JSON.stringify([registry, repository, tag, architecture]);
  }

  subscribe(key) {
    const stream = this.state.streams[key] ??= { candidates: [], offers: {} };
    stream.requestedAt = this.now();
    // Forget abandoned subscriptions, not candidates for an active dependency.
    for (const [oldKey, old] of Object.entries(this.state.streams)) {
      if (this.now() - Math.max(old.requestedAt ?? 0, old.observedAt ?? 0) > HISTORY_MS) delete this.state.streams[oldKey];
    }
    this.save();
  }

  reject(key, digest) {
    const stream = this.state.streams[key];
    stream.candidates = stream.candidates.filter((candidate) => candidate.digest !== digest);
    for (const [base, offered] of Object.entries(stream.offers)) {
      if (offered === digest) delete stream.offers[base];
    }
    this.save();
  }

  observe(key, digest) {
    if (!digestPattern.test(digest)) throw new Error('Registry returned an invalid digest');
    const stream = this.state.streams[key] ??= { candidates: [], offers: {} };
    stream.observedAt = this.now();
    if (!stream.candidates.some((candidate) => candidate.digest === digest)) {
      stream.candidates.push({ digest, firstSeen: this.now() });
    }
    this.save();
  }

  mature(key, currentDigest, minimumAge = SOAK_MS) {
    const stream = this.state.streams[key];
    if (!stream) return null;
    const base = currentDigest || 'unpinned';
    const offered = stream.candidates.find((candidate) => candidate.digest === stream.offers[base]);
    if (offered && offered.digest !== currentDigest && this.now() - offered.firstSeen >= minimumAge) return offered;
    const currentIndex = stream.candidates.findIndex((candidate) => candidate.digest === currentDigest);
    const eligible = stream.candidates.filter((candidate, index) => index > currentIndex && this.now() - candidate.firstSeen >= minimumAge);
    return eligible.at(-1) ?? stream.candidates.find((candidate) => candidate.digest === currentDigest && this.now() - candidate.firstSeen >= minimumAge) ?? null;
  }

  offer(key, currentDigest, minimumAge = SOAK_MS) {
    const candidate = this.mature(key, currentDigest, minimumAge);
    if (candidate && candidate.digest !== currentDigest) {
      this.state.streams[key].offers[currentDigest || 'unpinned'] = candidate.digest;
      this.save();
    }
    return candidate;
  }

  save() {
    if (!this.file) return;
    const serialized = JSON.stringify(this.state);
    if (Buffer.byteLength(serialized) > MAX_BYTES) throw new Error('Docker candidate state exceeds limit');
    fs.mkdirSync(path.dirname(this.file), { recursive: true });
    fs.writeFileSync(`${this.file}.tmp`, serialized, { mode: 0o644 });
    fs.renameSync(`${this.file}.tmp`, this.file);
  }
}

// Install through Renovate's datasource registry. No upstream files are patched.
// The adapter is integration-tested against the exact engine version in package.json.
export function retainedDocker(original, store, { registryRepository, versioning, versioningFor = () => versioning, ownedImages, hostError = (error) => error }) {
  const wrapper = Object.create(original);
  // Renovate's package cache would otherwise persist our time-dependent projection.
  wrapper.caching = false;
  const owned = (config) => {
    const { registryHost, dockerRepository } = registryRepository(config.packageName, config.registryUrl);
    return ownedImages.includes(`${new URL(registryHost).host}/${dockerRepository}`);
  };
  const identity = (config, tag, architecture = '') => {
    const { registryHost, dockerRepository } = registryRepository(config.packageName, config.registryUrl);
    return store.key(registryHost, dockerRepository, tag, architecture);
  };
  const age = (config, tag) => {
    const versions = versioningFor(config);
    return versions.isVersion(config.currentValue) && versions.isVersion(tag) &&
      versions.getMajor(tag) > versions.getMajor(config.currentValue) ? 7 * 86400000 : SOAK_MS;
  };

  wrapper.getReleases = async (config) => {
    const result = await original.getReleases(config);
    if (!result || owned(config)) return result;
    const current = isDockerTag(config.currentValue) ? config.currentValue : undefined;
    const versionsApi = versioningFor(config);
    if (current) store.subscribe(identity(config, current));
    const subscribed = new Set(current ? [current] : []);
    const { registryHost, dockerRepository } = registryRepository(config.packageName, config.registryUrl);
    for (const key of Object.keys(store.state.streams)) {
      const [host, repository, tag, architecture] = JSON.parse(key);
      if (host === registryHost && repository === dockerRepository && !architecture && isDockerTag(tag) &&
          store.now() - (store.state.streams[key].requestedAt ?? 0) <= HISTORY_MS) subscribed.add(tag);
    }
    const toObserve = new Set(subscribed);
    // Observe the newest two releases in each compatible major/minor series.
    // This records new versions even while Renovate is proposing an older mature one.
    for (const tag of subscribed) {
      if (!versionsApi.isVersion(tag)) continue;
      const groups = new Map();
      for (const release of result.releases) {
        const version = release.version;
        if (!versionsApi.isVersion(version) || !versionsApi.isCompatible(version, tag) || !versionsApi.isGreaterThan(version, tag)) continue;
        const group = `${versionsApi.getMajor(version)}.${versionsApi.getMinor(version)}`;
        const versions = groups.get(group) ?? [];
        versions.push(version);
        groups.set(group, versions);
      }
      for (const versions of groups.values()) {
        versions.sort((left, right) => versionsApi.sortVersions(left, right));
        versions.slice(-2).forEach((version) => toObserve.add(version));
      }
    }
    // Bounded concurrency also prevents a registry outage from spawning a request storm.
    const tags = [...toObserve];
    if (tags.length > 500) throw new Error('Too many retained Docker streams; review candidate state');
    for (let offset = 0; offset < tags.length; offset += 4) {
      await Promise.all(tags.slice(offset, offset + 4).map(async (tag) => {
        const digest = await original.getDigest({ ...config, currentDigest: undefined }, tag);
        if (!digest) throw new Error(`Could not observe Docker candidate for ${config.packageName}:${tag}`);
        store.observe(identity(config, tag), digest);
      }));
    }
    return {
      ...result,
      releases: result.releases.map((release) => {
        const key = identity(config, release.version);
        const mature = store.mature(key, undefined, age(config, release.version));
        const pending = store.state.streams[key]?.candidates[0];
        return { ...release, releaseTimestamp: new Date(mature?.firstSeen ?? pending?.firstSeen ?? store.now()).toISOString() };
      }),
    };
  };

  wrapper.getDigest = async (config, value) => {
    if (owned(config)) return original.getDigest(config, value);
    const tag = value || 'latest';
    if (!isDockerTag(tag)) return null;
    // Native release caches are package-scoped; digest calls also register other
    // current tag variants so their future versions are observed on later runs.
    if (isDockerTag(config.currentValue)) store.subscribe(identity(config, config.currentValue));
    const { registryHost, dockerRepository } = registryRepository(config.packageName, config.registryUrl);
    const architecture = config.currentDigest
      ? await original.getImageArchitecture(registryHost, dockerRepository, config.currentDigest) || ''
      : '';
    const key = identity(config, tag, architecture);
    const digest = await original.getDigest(config, tag);
    if (!digest) throw new Error(`Could not observe Docker candidate for ${config.packageName}:${tag}`);
    store.observe(key, digest);
    const candidate = store.offer(key, config.currentDigest, age(config, tag));
    if (!candidate) {
      // Same-tag no-op is safe. New tags must never inherit another tag's digest.
      if (architecture && tag !== config.currentValue) throw new Error('Architecture-specific Docker candidate is still soaking');
      return tag === config.currentValue ? config.currentDigest ?? null : null;
    }
    // Confirm retained content remains available by digest, never resolve the tag again.
    const available = await original.getDigest({ ...config, currentDigest: undefined }, candidate.digest);
    if (available !== candidate.digest) {
      store.reject(key, candidate.digest);
      throw new Error(`Retained Docker candidate is unavailable for ${config.packageName}:${tag}; discarded so a later run can select another mature candidate`);
    }
    return candidate.digest;
  };
  for (const method of ['getReleases', 'getDigest']) {
    const implementation = wrapper[method];
    wrapper[method] = async (...args) => {
      try { return await implementation(...args); }
      catch (error) { throw hostError(error); }
    };
  }
  return wrapper;
}
