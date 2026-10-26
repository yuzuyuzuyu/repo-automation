import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { CandidateStore, retainedDocker, QEMU_VERSIONING } from './docker_candidates.mjs';

// The upstream shell launcher initializes containerbase before starting Node.
// Only its Renovate process installs the adapter; child package managers do not.
const ownImport = `--import=${import.meta.url}`;
if (process.env.NODE_OPTIONS) {
  process.env.NODE_OPTIONS = process.env.NODE_OPTIONS.split(' ').filter((option) => option !== ownImport && option !== `--import=file:${fileURLToPath(import.meta.url)}`).join(' ');
}
if (process.argv[1] && path.basename(process.argv[1]) === 'renovate.js') {
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
  const dist = path.dirname(fs.realpathSync(process.argv[1]));
  const load = (file) => import(pathToFileURL(path.join(dist, file)).href);
  const expected = JSON.parse(fs.readFileSync(path.join(root, 'package.json'), 'utf8')).devDependencies.renovate;
  const actual = JSON.parse(fs.readFileSync(path.join(dist, '..', 'package.json'), 'utf8')).version;
  if (actual !== expected) throw new Error(`Renovate runtime ${actual} differs from tested engine ${expected}`);
  if (!process.env.RENOVATE_CANDIDATE_STATE) throw new Error('Docker candidate state path is required');

  const [{ getDatasources }, { getRegistryRepository }, { api: versioning }, { ExternalHostError }, { get: getVersioning }] = await Promise.all([
    load('modules/datasource/index.js'), load('modules/datasource/docker/common.js'),
    load('modules/versioning/docker/index.js'), load('types/errors/external-host-error.js'),
    load('modules/versioning/index.js'),
  ]);
  const policy = JSON.parse(fs.readFileSync(path.join(root, 'config', 'docker-policy.json'), 'utf8'));
  const ownedImages = policy.packageRules.at(-1).matchPackageNames;
  const store = new CandidateStore(process.env.RENOVATE_CANDIDATE_STATE);
  store.save();
  const datasources = getDatasources();
  datasources.set('docker', retainedDocker(datasources.get('docker'), store, {
    registryRepository: getRegistryRepository, versioning, ownedImages,
    versioningFor: (config) => config.packageName === 'tonistiigi/binfmt' ? getVersioning(QEMU_VERSIONING) : versioning,
    hostError: (error) => new ExternalHostError(error, 'docker'),
  }));

  const config = JSON.parse(process.env.RENOVATE_CONFIG || '{}');
  // Only this runner may use the missing branch-timestamp workaround: the adapter
  // above has already enforced first-observed age on the exact returned digest.
  config.customEnvVariables = { ...config.customEnvVariables, UV_EXCLUDE_NEWER: 'P3D', npm_config_min_release_age: '3', npm_config_ignore_scripts: 'true' };
  config.force = {
    ...config.force,
    packageRules: [...(config.force?.packageRules || []), ...policy.packageRules],
  };
  process.env.RENOVATE_CONFIG = JSON.stringify(config);
}
