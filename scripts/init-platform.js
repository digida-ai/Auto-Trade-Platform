#!/usr/bin/env node
'use strict';

/**
 * Idempotent platform bootstrap.
 *
 *   npm run init                              install everything
 *   node scripts/init-platform.js --component freellmapi   install one subplatform
 *   npm start                                 runs init automatically when stale
 *   SKIP_INIT=1 npm start                     skip the check
 *
 * Guarantees:
 *   - ONE shared node_modules at the root: every JavaScript component is an
 *     npm workspace, and no component installs dependencies into its own folder
 *   - ONE shared Python environment (.venv) and ONE root requirements.txt;
 *     per-component requirements are not installed separately
 *   - a partial node_modules is detected (npm only writes
 *     node_modules/.package-lock.json on success) and removed before reinstall
 *   - the fingerprint covers nested manifests so a changed subproject retriggers init
 *   - every component gets its .env scaffolded from its own .env.example
 *   - per-subplatform agents are regenerated from their real manifests
 *   - any failing step exits non-zero; no silent "starting anyway"
 */

const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const root = path.resolve(__dirname, '..');
const cacheDir = path.join(root, '.cache');
const markerPath = path.join(root, 'orchestrator', 'state', 'init-state.json');
const envPath = path.join(root, '.env');
const sharedNodeModules = path.join(root, 'node_modules');
const sharedVenv = path.join(root, '.venv');
const constraintsPath = path.join(root, 'constraints.txt');
const SKIP_DIRS = new Set(['node_modules', '.git', '.venv', 'venv', 'dist', 'build', 'target', '__pycache__', '.next', '.turbo', 'vendor']);

const registry = JSON.parse(fs.readFileSync(path.join(root, 'agents', 'registry.json'), 'utf8'));
const subplatforms = registry.subplatforms || [];

function log(message) {
  console.log(`[init] ${message}`);
}

function walkManifests(dir, depth = 0, out = []) {
  if (depth > 3) return out;
  let entries;
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    return out;
  }
  for (const entry of entries) {
    if (entry.isDirectory()) {
      if (SKIP_DIRS.has(entry.name) || entry.name.startsWith('.')) continue;
      walkManifests(path.join(dir, entry.name), depth + 1, out);
      continue;
    }
    const name = entry.name;
    if (
      name === 'package.json' ||
      name === 'package-lock.json' ||
      name === 'requirements.txt' ||
      name === 'pyproject.toml' ||
      name === 'uv.lock' ||
      name === 'Cargo.toml' ||
      name === 'Cargo.lock' ||
      name === 'pom.xml' ||
      name === 'CMakeLists.txt'
    ) {
      out.push(path.relative(root, path.join(dir, name)));
    }
  }
  return out;
}

function manifests() {
  const list = ['scripts/init-platform.js', 'constraints.txt'];
  for (const rel of walkManifests(root)) list.push(rel);
  return [...new Set(list)];
}

function fingerprint() {
  const hash = crypto.createHash('sha256');
  for (const rel of manifests()) {
    const full = path.join(root, rel);
    if (!fs.existsSync(full)) continue;
    hash.update(rel);
    hash.update(fs.readFileSync(full));
  }
  return hash.digest('hex');
}

function envFileValue(key) {
  try {
    for (const line of fs.readFileSync(envPath, 'utf8').split(/\r?\n/)) {
      const m = line.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$/);
      if (m && m[1] === key) return m[2].replace(/^["']|["']$/g, '');
    }
  } catch {
    // .env is optional
  }
  return null;
}

// npm >= 11.7 enforces the `allow-scripts` RFC. A machine-level value in the
// operator's ~/.npmrc is inherited by `npm run`, which makes a project-scoped
// install fail outright (EALLOWSCRIPTS) and would otherwise skip the binary
// downloads for esbuild, workerd, puppeteer and friends. Mirror the operator's
// npmrc into the project cache without that key so registry/auth settings are
// preserved and the shared install is governed by the project only.
function localNpmrc() {
  const source = process.env.npm_config_userconfig || path.join(os.homedir(), '.npmrc');
  const target = path.join(cacheDir, 'npmrc');
  try {
    const filtered = fs
      .readFileSync(source, 'utf8')
      .split(/\r?\n/)
      .filter((line) => !/^\s*allow-scripts\s*[=:]/.test(line))
      .join('\n');
    fs.mkdirSync(cacheDir, { recursive: true });
    if (!fs.existsSync(target) || fs.readFileSync(target, 'utf8') !== filtered) {
      fs.writeFileSync(target, filtered, 'utf8');
    }
    return target;
  } catch {
    return null;
  }
}

function childEnv() {
  const env = {
    ...process.env,
    UV_CACHE_DIR: process.env.UV_CACHE_DIR || path.join(cacheDir, 'uv'),
    UV_PYTHON_INSTALL_DIR: process.env.UV_PYTHON_INSTALL_DIR || path.join(cacheDir, 'uv-python'),
    npm_config_cache: process.env.npm_config_cache || process.env.NPM_CONFIG_CACHE || path.join(cacheDir, 'npm'),
  };
  // `npm run` forwards its own config as npm_config_* env vars; the allow-scripts
  // value must not leak into the child install.
  delete env.npm_config_allow_scripts;
  delete env['npm_config_allow-scripts'];
  const userconfig = localNpmrc();
  if (userconfig) env.npm_config_userconfig = userconfig;
  // Prefer an explicit pin, then the project-local Node 24 toolchain, then whatever
  // Node is running us. Node 24 ships npm 11, which the workspace install expects.
  const pinnedNode = path.join(cacheDir, 'toolchains', 'node24');
  const nodeBin =
    env.PLATFORM_NODE_BIN ||
    envFileValue('PLATFORM_NODE_BIN') ||
    (fs.existsSync(pinnedNode) ? pinnedNode : path.dirname(process.execPath));
  if (nodeBin && fs.existsSync(nodeBin)) {
    const separator = process.platform === 'win32' ? ';' : ':';
    const currentPath = env.PATH || env.Path || '';
    const first = currentPath.split(separator)[0] || '';
    if (first.toLowerCase() !== nodeBin.toLowerCase()) {
      env.PATH = nodeBin + separator + currentPath;
      env.Path = env.PATH;
    }
  }
  return env;
}

function run(command, args, cwd = root, { allowFailure = false, shell = process.platform === 'win32' } = {}) {
  log(`${command} ${args.join(' ')}${cwd === root ? '' : `  (cwd ${path.relative(root, cwd) || '.'})`}`);
  const result = spawnSync(command, args, {
    cwd,
    env: childEnv(),
    stdio: 'inherit',
    // `shell: true` breaks quoted executables (e.g. "C:\\Program Files\\node.exe").
    shell,
  });
  if (result.error) throw result.error;
  if (result.status !== 0 && !allowFailure) {
    throw new Error(`${command} ${args.join(' ')} exited with ${result.status}`);
  }
  return result.status === 0;
}

function preflight() {
  let free = null;
  try {
    const stat = fs.statfsSync(root);
    free = stat.bavail * stat.bsize;
  } catch {
    // statfs is not available everywhere; skip rather than fail
  }
  if (free !== null) {
    const gb = free / (1024 ** 3);
    log(`free disk: ${gb.toFixed(1)} GB`);
    if (gb < 1) {
      log('WARNING: less than 1 GB free. npm install may fail with ENOSPC.');
    }
  }
  for (const file of ['.env', 'requirements.txt']) {
    if (!fs.existsSync(path.join(root, file))) log(`WARNING: ${file} is missing`);
  }
}

function nodeModulesComplete() {
  // npm writes node_modules/.package-lock.json only after a successful install.
  return fs.existsSync(path.join(sharedNodeModules, '.package-lock.json'));
}

function venvComplete() {
  const candidates = process.platform === 'win32'
    ? [path.join(sharedVenv, 'Scripts', 'python.exe')]
    : [path.join(sharedVenv, 'bin', 'python3'), path.join(sharedVenv, 'bin', 'python')];
  return candidates.some((candidate) => fs.existsSync(candidate));
}

function sharedInstallComplete() {
  return fs.existsSync(sharedNodeModules) && nodeModulesComplete() && venvComplete();
}

function cleanPartialNodeModules() {
  if (!fs.existsSync(sharedNodeModules)) return;
  if (nodeModulesComplete()) return;
  log('removing truncated node_modules (no .package-lock.json -> previous install did not finish)');
  fs.rmSync(sharedNodeModules, { recursive: true, force: true });
}

function provisionEnv() {
  if (!fs.existsSync(envPath)) {
    const lines = [
      '# Generated by npm run init. Safe local defaults; add your own keys below.',
      '# JEV_OFFLINE uses the bundled deterministic router instead of a paid API.',
      'JEV_OFFLINE=1',
      '',
      '# Free/local LLM endpoints (uncomment and configure if you use them):',
      '# OPENAI_BASE_URL=http://127.0.0.1:11434/v1',
      '# OPENAI_API_KEY=ollama',
    ];
    fs.writeFileSync(envPath, `${lines.join('\n')}\n`, 'utf8');
    log('created .env with free/local defaults');
  }
  // Each component reads its own .env; scaffold from its template once.
  for (const sp of subplatforms) {
    const dir = path.join(root, sp.component || '');
    if (!sp.component || !fs.existsSync(dir)) continue;
    const template = path.join(dir, '.env.example');
    const target = path.join(dir, '.env');
    if (fs.existsSync(template) && !fs.existsSync(target)) {
      fs.copyFileSync(template, target);
      log(`scaffolded ${sp.component}/.env from .env.example (values are placeholders)`);
    }
  }
}

function generateAgents() {
  run(process.execPath, [path.join('scripts', 'generate-agents.js')], root, { shell: false });
}

function npmMajorVersion() {
  const result = spawnSync('npm', ['--version'], {
    encoding: 'utf8',
    shell: process.platform === 'win32',
  });
  return Number(String(result.stdout || '').trim().split('.')[0]) || 0;
}

// npm 12 refuses non-registry tarballs and git dependencies by default:
// xlsx resolves from a CDN and Electron's node-gyp comes from a git URL.
function npmCompatArgs() {
  return npmMajorVersion() >= 12 ? ['--allow-remote=all', '--allow-git=all'] : [];
}

// npm 12 also blocks dependency lifecycle scripts until approved, which would
// silently skip the binary downloads for esbuild, workerd, puppeteer and friends.
function approveInstallScripts() {
  if (npmMajorVersion() < 12) return;
  const listed = spawnSync('npm', ['install-scripts', 'ls'], {
    cwd: root,
    encoding: 'utf8',
    shell: process.platform === 'win32',
  });
  const blocked = String(listed.stdout || '').includes('not covered by allowScripts');
  if (!blocked) return;
  log('npm 12 blocked dependency install scripts; approving them and rebuilding');
  run('npm', ['install-scripts', 'approve', '--all'], root);
  run('npm', ['rebuild'], root);
}

function componentManifest(dir) {
  if (fs.existsSync(path.join(dir, 'package.json'))) return 'package.json';
  if (fs.existsSync(path.join(dir, 'pyproject.toml'))) return 'pyproject.toml';
  if (fs.existsSync(path.join(dir, 'requirements.txt'))) return 'requirements.txt';
  return null;
}

// Components never install into their own folder. JavaScript dependencies come
// from the single root npm workspace install; Python dependencies come from the
// single root requirements.txt installed into the shared .venv. This only
// validates that the component still has a manifest the shared install covers.
function componentUsesSharedDeps(dir) {
  const manifest = componentManifest(dir);
  if (!manifest) return false;
  log(`  ${path.relative(root, dir)}: ${manifest} covered by root ${manifest === 'package.json' ? 'node_modules' : 'requirements.txt/.venv'}`);
  return true;
}

function installShared() {
  cleanPartialNodeModules();
  const npm = 'npm';
  run(npm, ['install', '--no-audit', '--no-fund', '--legacy-peer-deps', ...npmCompatArgs()]);
  if (!nodeModulesComplete()) {
    throw new Error('npm install finished but node_modules/.package-lock.json is missing; treating the install as incomplete');
  }
  approveInstallScripts();
  if (!fs.existsSync(sharedVenv)) {
    run('uv', ['venv', '--python', '3.12', '--seed', '.venv']);
  }
  if (fs.existsSync(constraintsPath)) {
    run('uv', ['pip', 'install', '--constraint', 'constraints.txt', '-r', 'requirements.txt']);
  } else {
    run('uv', ['pip', 'install', '-r', 'requirements.txt']);
  }
}

function writeMarker(extra = {}) {
  fs.mkdirSync(path.dirname(markerPath), { recursive: true });
  fs.writeFileSync(
    markerPath,
    `${JSON.stringify({
      fingerprint: fingerprint(),
      installed_at: new Date().toISOString(),
      ...extra,
    }, null, 2)}\n`,
    'utf8',
  );
}

function resolveComponent(value) {
  const needle = String(value || '').toLowerCase();
  return subplatforms.find(
    (sp) => (sp.name || '').toLowerCase() === needle || (sp.component || '').toLowerCase() === needle,
  );
}

function main() {
  const args = process.argv.slice(2);
  const auto = args.includes('--auto');
  const componentArg = args.indexOf('--component') >= 0 ? args[args.indexOf('--component') + 1] : null;

  if (auto && componentArg === null) {
    try {
      const saved = JSON.parse(fs.readFileSync(markerPath, 'utf8'));
      // A directory alone proves nothing: an interrupted install leaves a
      // truncated node_modules behind, and skipping the repair would report
      // every Node subplatform as blocked from boot. Require the success
      // marker (and a working venv) in addition to a matching fingerprint.
      const current = saved.fingerprint === fingerprint();
      const complete = saved.shared_node_modules === true && sharedInstallComplete();
      if (current && complete) {
        log('dependencies are current; skipping install');
        return;
      }
      if (!complete) {
        log('shared dependencies are incomplete; repairing before start');
      }
    } catch {
      // not installed yet
    }
  }

  fs.mkdirSync(cacheDir, { recursive: true });
  preflight();
  provisionEnv();

  if (componentArg) {
    const sp = resolveComponent(componentArg);
    if (!sp) {
      throw new Error(`unknown component '${componentArg}' (see agents/registry.json)`);
    }
    const dir = path.join(root, sp.component);
    if (!fs.existsSync(dir)) throw new Error(`component directory missing: ${sp.component}`);
    const usesShared = componentUsesSharedDeps(dir);
    generateAgents();
    writeMarker({ last_component: sp.name, uses_shared_deps: usesShared });
    log(`component ${sp.name} checked; ${usesShared ? 'shared node_modules/.venv cover its manifest' : 'toolchain-only component, no shared dependency manifest'}`);
    return;
  }

  installShared();
  generateAgents();
  writeMarker({ shared_node_modules: true, shared_requirements: true, agents_generated: true });
  log('initialization complete');
}

try {
  main();
} catch (err) {
  console.error(`[init] FAILED: ${err && err.message ? err.message : String(err)}`);
  console.error('[init] Fix the error above and run `npm run init` again.');
  process.exit(1);
}
