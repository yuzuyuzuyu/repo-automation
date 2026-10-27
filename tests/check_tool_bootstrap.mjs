// Real installer smoke test: pnpm 12's native bootstrap needs its own scripts,
// while Renovate separately blocks scripts in the repositories it maintains.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { spawnSync } from 'node:child_process';
import { configureRenovate } from '../src/renovate_config.mjs';

const image = JSON.parse(fs.readFileSync(new URL('../config/renovate-image.json', import.meta.url))).image;
const policy = JSON.parse(fs.readFileSync(new URL('../config/docker-policy.json', import.meta.url)));
const config = configureRenovate({}, policy);
assert.equal(config.allowScripts, false);
const env = Object.entries(config.customEnvVariables).flatMap(([key, value]) => ['--env', `${key}=${value}`]);
const script = `
install-tool node 24.22.1
install-tool pnpm 12.15.0
test "$(pnpm --version)" = 12.15.0
install-tool pnpm 12.15.0+sha512.b3951d6d6ff4a9f13dc7cf56cd28d2b8eb37dbcc9c68e52e1e1191873ea9f82a017287ab50baadc5ccc494f036c67c0e47eb13608c2fa36f47711b61ea128e61
test "$(pnpm --version)" = 12.15.0
`;
const result = spawnSync('docker', ['run', '--rm', ...env, image, 'bash', '-euc', script], { stdio: 'inherit' });
assert.equal(result.status, 0, 'Pinned engine must install and execute both plain and checksum-pinned pnpm');
console.log('Pinned engine tool bootstrap passed; repository scripts remain disabled.');
