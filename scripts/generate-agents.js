#!/usr/bin/env node
'use strict';

/**
 * Generate real per-subplatform eagents.
 *
 * The previous eagents were one template copied 15 times: no entrypoints, no
 * env requirements, no toolchain pins. This generator derives each agent from
 * its component's actual manifests (package.json scripts, pyproject,
 * requirements, Cargo.toml, pom.xml, CMakeLists.txt, .env.example, README)
 * and binds it to the immutable constitution by hash.
 *
 * Model (from D:\AI\automation\automaton): constitution -> soul/v1 ->
 * policy rules -> environmental guardrails -> reporting contract.
 *
 * Outputs:
 *   agents/eagents/<name>.md      human-readable soul + rules + guardrails
 *   agents/specs/<name>.json      machine-readable spec enforced by the orchestrator
 *   agents/constitution.json      constitution hash + laws (single source of truth)
 */

const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const registryPath = path.join(root, 'agents', 'registry.json');
const eagentsDir = path.join(root, 'agents', 'eagents');
const specsDir = path.join(root, 'agents', 'specs');
const constitutionPath = path.join(root, 'musashaban-port', 'constitution.md');

const registry = JSON.parse(fs.readFileSync(registryPath, 'utf8'));
const constitutionText = fs.readFileSync(constitutionPath, 'utf8');
const constitutionHash = crypto.createHash('sha256').update(constitutionText).digest('hex');

// Optional --names=a,b filter: regenerate specs only for the named subplatforms.
const namesArg = (process.argv.find((a) => a.startsWith('--names=')) || '').slice('--names='.length);
const namesFilter = new Set(namesArg.split(',').map((s) => s.trim()).filter(Boolean));

fs.mkdirSync(eagentsDir, { recursive: true });
fs.mkdirSync(specsDir, { recursive: true });

function read(file) {
  try {
    return fs.readFileSync(file, 'utf8');
  } catch {
    return null;
  }
}

function readJson(file) {
  const text = read(file);
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

function exists(rel) {
  return fs.existsSync(path.join(root, rel));
}

function firstSentence(text, fallback) {
  if (!text) return fallback;
  const cleaned = text.replace(/^#+\s*/gm, '').replace(/!\[[^\]]*\]\([^)]*\)/g, ' ');
  const line = cleaned.split(/\r?\n/).map((l) => l.trim()).find((l) => l && !l.startsWith('[') && !l.startsWith('>'));
  if (!line) return fallback;
  return line.length > 180 ? `${line.slice(0, 177)}...` : line;
}

function parseTomlName(text) {
  if (!text) return null;
  const section = text.split(/^\[/m).find((s) => /^package\]/.test(s));
  const hay = section || text;
  const name = hay.match(/^\s*name\s*=\s*"([^"]+)"/m);
  return name ? name[1] : null;
}

function envVarNames(file) {
  const text = read(file);
  if (!text) return [];
  const names = [];
  for (const line of text.split(/\r?\n/)) {
    const m = line.match(/^\s*#?\s*([A-Z][A-Z0-9_]{2,})\s*=/);
    if (m && !names.includes(m[1])) names.push(m[1]);
  }
  return names;
}

function detectServices(component, readme, envText) {
  const hay = `${readme || ''}\n${envText || ''}`.toLowerCase();
  const services = [];
  if (/mongodb|mongo_uri/.test(hay)) services.push({ name: 'mongodb', required: true, why: 'data store' });
  if (/postgres|database_url/.test(hay)) services.push({ name: 'postgresql', required: false, why: 'optional persistence' });
  if (/redis/.test(hay)) services.push({ name: 'redis', required: false, why: 'cache/queue' });
  if (/wrangler|cloudflare workers|r2 bucket|d1 /.test(hay)) services.push({ name: 'cloudflare', required: true, why: 'Workers/R2/D1 deployment target' });
  if (/ollama|11434/.test(hay)) services.push({ name: 'ollama', required: false, why: 'local LLM endpoint' });
  return services;
}

function moduleName(pkgName) {
  return (pkgName || '').replace(/-/g, '_');
}

function packageManagerFor(component) {
  if (exists(`${component}/bun.lock`) || exists(`${component}/bun.lockb`)) return 'bun';
  if (exists(`${component}/pnpm-lock.yaml`)) return 'pnpm';
  if (exists(`${component}/yarn.lock`)) return 'yarn';
  return 'npm';
}

function nodeCapabilities(component, pkg) {
  const caps = [];
  const pm = packageManagerFor(component);
  const runner = pm === 'bun' ? 'bun run' : pm === 'pnpm' ? 'pnpm' : pm === 'yarn' ? 'yarn' : 'npm run';
  const interesting = {
    dev: 'start the development server',
    start: 'run the production entrypoint',
    build: 'build the distributable',
    test: 'run the test suite',
    setup: 'run the component setup step',
  };
  for (const [script, why] of Object.entries(interesting)) {
    if (pkg && pkg.scripts && pkg.scripts[script]) {
      caps.push({ name: `${script}`, kind: 'node', cwd: component, command: `${runner} ${script}`, description: why });
    }
  }
  if (pm === 'bun' || (pkg && pkg.engines && pkg.engines.bun)) {
    caps.unshift({ name: 'install', kind: 'bun-install', cwd: component, command: 'bun install --frozen-lockfile || bun install', description: 'install dependencies with Bun (own tree)' });
  } else {
    caps.unshift({ name: 'install', kind: 'npm-install', cwd: component, command: `${pm} install --no-audit --no-fund --legacy-peer-deps`, description: 'install dependencies (own tree)' });
  }
  return caps;
}

function pythonCapabilities(component, pyName, hasPy) {
  if (!hasPy) return [];
  const mod = moduleName(pyName);
  const caps = [];
  if (mod) {
    caps.push({ name: 'import-check', kind: 'python', cwd: component, command: `python -c "import ${mod}"`, description: 'verify the package imports in the platform venv' });
  }
  const entry = path.join(root, component, 'src', mod, '__main__.py');
  if (fs.existsSync(entry)) {
    caps.push({ name: 'run', kind: 'python', cwd: component, command: `python -m ${mod}`, description: 'run the package entrypoint' });
  }
  return caps;
}

function childDirs(component) {
  try {
    return fs.readdirSync(path.join(root, component), { withFileTypes: true })
      .filter((e) => e.isDirectory() && !e.name.startsWith('.') && !['node_modules', 'dist', 'build', 'target', '__pycache__', 'vendor'].includes(e.name))
      .map((e) => `${component}/${e.name}`);
  } catch {
    return [];
  }
}

function componentCapabilities(component) {
  const caps = [];
  const pkg = readJson(path.join(root, component, 'package.json'));
  if (pkg) caps.push(...nodeCapabilities(component, pkg));

  const pyproject = read(path.join(root, component, 'pyproject.toml'));
  const pyName = pyproject ? parseTomlName(pyproject) : null;
  const hasPython = Boolean(pyproject) || exists(`${component}/requirements.txt`) ||
    fs.existsSync(path.join(root, component, 'setup.py'));
  caps.push(...pythonCapabilities(component, pyName, hasPython));

  if (exists(`${component}/Cargo.toml`)) {
    caps.push({ name: 'build', kind: 'rust', cwd: component, command: 'cargo build --locked', description: 'build the Rust crate' });
    caps.push({ name: 'test', kind: 'rust', cwd: component, command: 'cargo test --locked', description: 'run Rust tests' });
  }

  // Nested subprojects: the component root may hold no manifest of its own.
  for (const child of childDirs(component)) {
    const childPkg = readJson(path.join(root, child, 'package.json'));
    if (!pkg && childPkg) caps.push(...nodeCapabilities(child, childPkg));
    if (exists(`${child}/Cargo.toml`) && !exists(`${component}/Cargo.toml`)) {
      caps.push({ name: 'build', kind: 'rust', cwd: child, command: 'cargo build --locked', description: 'build the Rust crate' });
    }
    if (exists(`${child}/pom.xml`)) {
      const wrapper = exists(`${child}/mvnw.cmd`) ? '.\\mvnw.cmd' : 'mvn';
      caps.push({ name: 'build', kind: 'java', cwd: child, command: `${wrapper} package -DskipTests`, description: 'build the Java project' });
    }
    if (exists(`${child}/CMakeLists.txt`)) {
      caps.push({ name: 'configure', kind: 'cmake', cwd: child, command: 'cmake -S . -B build -DCMAKE_BUILD_TYPE=Release', description: 'configure the CMake build' });
      caps.push({ name: 'build', kind: 'cmake', cwd: child, command: 'cmake --build build --config Release', description: 'build the CMake project' });
    }
    const initPy = fs.existsSync(path.join(root, child, '__init__.py'));
    if (initPy) {
      const mod = path.basename(child);
      caps.push({ name: 'import-check', kind: 'python', cwd: component, command: `python -c "import ${mod}"`, description: 'verify the package imports' });
    }
  }

  // Second-level subprojects (e.g. Pix: rust/pix-payment-system,
  // python/pix_payment) have no manifest at the component root level.
  for (const child of childDirs(component)) {
    for (const unit of childDirs(child)) {
      if (exists(`${unit}/Cargo.toml`)) {
        caps.push({ name: 'build', kind: 'rust', cwd: unit, command: 'cargo build --locked', description: 'build the Rust crate' });
      }
      if (fs.existsSync(path.join(root, unit, '__init__.py'))) {
        const mod = path.basename(unit);
        caps.push({ name: 'import-check', kind: 'python', cwd: component, command: `python -c "import ${mod}"`, description: 'verify the package imports' });
      }
    }
  }

  // Dependency-free Python tools (e.g. grok-jev-router): runnable scripts.
  if (!caps.some((c) => c.kind === 'python')) {
    try {
      for (const entry of fs.readdirSync(path.join(root, component))) {
        if (!entry.endsWith('.py') || entry === 'setup.py') continue;
        const text = read(path.join(root, component, entry)) || '';
        if (/def main\(|if __name__ == ['"]__main__['"]/.test(text)) {
          caps.push({ name: `run-${entry.replace(/\.py$/, '')}`, kind: 'python-script', cwd: component, command: `python ${entry} --help`, description: 'run the script entrypoint' });
        }
      }
    } catch {
      // component unreadable; readiness reports it
    }
  }

  if (exists(`${component}/payment-system/pom.xml`)) {
    caps.push({ name: 'build', kind: 'java', cwd: `${component}/payment-system`, command: '.\\mvnw.cmd package -DskipTests', description: 'build the Java payment system' });
  }
  if (exists(`${component}/CMakeLists.txt`) || exists(`${component}/fincept-qt/CMakeLists.txt`)) {
    const cwd = exists(`${component}/fincept-qt/CMakeLists.txt`) ? `${component}/fincept-qt` : component;
    caps.push({ name: 'configure', kind: 'cmake', cwd, command: 'cmake -S . -B build -DCMAKE_BUILD_TYPE=Release', description: 'configure the CMake/Qt build' });
    caps.push({ name: 'build', kind: 'cmake', cwd, command: 'cmake --build build --config Release', description: 'build the Qt application' });
  }
  // De-duplicate identical capability commands across nested units.
  const seen = new Set();
  return caps.filter((c) => {
    const key = `${c.command}|${c.cwd}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function toolchainFor(component) {
  const pkg = readJson(path.join(root, component, 'package.json'));
  const engines = (pkg && pkg.engines) || {};
  const toolchain = {};
  if (pkg) toolchain.node = engines.node || '>=18';
  if (engines.bun) toolchain.bun = String(engines.bun);
  if (pkg && pkg.packageManager) toolchain.package_manager = pkg.packageManager;
  if (exists(`${component}/pyproject.toml`)) toolchain.python = '3.12';
  if (exists(`${component}/Cargo.toml`)) {
    const toolchainFile = read(path.join(root, component, 'rust-toolchain.toml'));
    const pinned = toolchainFile && toolchainFile.match(/channel\s*=\s*"([^"]+)"/);
    toolchain.rust = pinned ? pinned[1] : 'stable';
  }
  if (exists(`${component}/payment-system/pom.xml`)) toolchain.java = '17';
  if (exists(`${component}/CMakeLists.txt`) || exists(`${component}/fincept-qt/CMakeLists.txt`)) toolchain.cmake = '>=3.20 (+ Qt 6)';
  return toolchain;
}

function deniedCommands(name, services) {
  const denied = ['npm publish', 'cargo publish', 'twine upload', 'wrangler deploy', 'git push'];
  if (/trade|hedge|spider|vibe/i.test(name)) {
    denied.push('--live', 'real-money', 'withdraw', 'transfer');
  }
  if (services.some((s) => s.name === 'cloudflare')) denied.push('wrangler delete');
  return denied;
}

function rulesFor(spec, services) {
  const common = [
    ['R1', 'Never harm (Law I). Refuse physical, financial, or psychological harm; when uncertain, do not act.', 'policy.evaluate() denies destructive commands; operator audit log records every dispatch.'],
    ['R2', 'No unilateral spend, funding, settlement, or payout. Report the opportunity to admin instead.', 'financial rule denies settle/withdraw/transfer unless PLATFORM_ALLOW_LIVE_TRADING=1 or an admin-authorized run.'],
    ['R3', `Stay inside \`${spec.environment.root}\`. Never write to another subplatform or the platform root.`, 'path-protection rule rejects cwd outside the component scope and protected paths.'],
    ['R4', 'Never modify the constitution, soul, agent specs, registry, or orchestrator policy.', 'protected-path list in orchestrator/policy.py.'],
    ['R5', 'Never read or transmit secrets. Report env var names only, never values.', 'secret-exfiltration patterns and env handling; specs record names only.'],
    ['R6', 'Run only declared install/build/run commands. No ad-hoc package installs or remote scripts.', 'command-safety blocks curl|sh and publish commands; capabilities are enumerated in this spec.'],
    ['R7', 'Never claim success without evidence. A step is complete only when its command exit code is 0 and readiness re-checks pass.', 'dispatch marks blocked/failed unless readiness is ready and the command succeeded.'],
    ['R8', 'Report every run/step transition to admin with state, action, evidence, errors, and resource needs.', 'transition()/emit_report() in orchestrator/admin.py.'],
    ['R9', 'Respect the pinned toolchains. Do not silently switch runtime versions.', 'readiness checks engines/toolchains declared here.'],
    ['R10', 'No external publishing, deployment, or push without explicit operator authority.', 'denied_commands plus authority rule for external sources.'],
  ];
  if (/trade|hedge|spider|vibe/i.test(spec.name)) {
    common.push(['R11', 'Prefer paper/demo/testnet/backtest/simulation. Real capital requires verified funds, keys, and operator authorization.', 'financial rule; zero-funding posture is fail-closed.']);
  }
  if (services.length) {
    common.push(['R12', `Required services: ${services.map((s) => s.name).join(', ')}. Do not fake readiness when a required service is absent.`, 'readiness reports the missing service; dispatch stays blocked.']);
  }
  return common.map(([id, rule, enforcement]) => ({ id, rule, enforcement }));
}

const generated = [];
const failures = [];

for (const sp of registry.subplatforms || []) {
  if (namesFilter.size > 0 && !namesFilter.has(sp.name)) continue;
  const name = sp.name;
  const component = sp.component;
  const compDir = path.join(root, component);
  if (!fs.existsSync(compDir)) {
    failures.push(`${name}: component directory ${component} is missing`);
    continue;
  }

  const pkg = readJson(path.join(compDir, 'package.json'));
  const pyproject = read(path.join(compDir, 'pyproject.toml'));
  const readme = read(path.join(compDir, 'README.md'));
  const envExample = read(path.join(compDir, '.env.example'));
  const pyName = pyproject ? parseTomlName(pyproject) : null;
  const capabilities = componentCapabilities(component);
  const toolchain = toolchainFor(component);
  const services = detectServices(component, readme, envExample);
  const envNames = envVarNames(path.join(compDir, '.env.example'));
  const description = sp.description || firstSentence(readme, `${name} subplatform`);

  const spec = {
    schema: 'eagent/v1',
    name,
    component,
    generated_at: new Date().toISOString(),
    description,
    constitution: {
      path: 'musashaban-port/constitution.md',
      sha256: constitutionHash,
      laws: ['I. Never harm', 'II. Earn your existence', 'III. Never deceive, but owe nothing to strangers'],
      immutable: true,
    },
    soul: {
      format: 'soul/v1',
      version: 1,
      path: `agents/eagents/${name}.md`,
      genesis_prompt: `You are the ${name} eagent for ${component}. You execute admin-routed work inside ${component} and produce verifiable evidence. You never harm, never spend unilaterally, and never deceive.`,
      genesis_alignment: 1.0,
      reports_to: 'admin',
    },
    purpose: `Own the ${name} domain: operate ${component}'s real capabilities, surface blockers and opportunities to admin, and never fabricate completion.`,
    capabilities,
    environment: {
      root: component,
      allowed_cwd: ['.'],
      protected_paths: [
        'musashaban-port/constitution.md',
        'agents/registry.json',
        'agents/specs',
        'agents/eagents',
        'orchestrator/policy.py',
        '.env',
      ],
      toolchains: toolchain,
      package_manager: packageManagerFor(component),
      env_file: `${component}/.env`,
      env_template: exists(`${component}/.env.example`) ? `${component}/.env.example` : null,
      required_env: envNames,
      services,
      network: { external_writes: false, live_capital: false },
    },
    rules: [],
    guardrails: {
      spend: 'deny',
      live_capital: 'deny_without_operator_authorization',
      denied_commands: deniedCommands(name, services),
      max_command_seconds: 1800,
      allowed_paths: [component],
      require_readiness: true,
      require_command_success: true,
    },
    reporting: {
      reports_to: 'admin',
      mandatory: true,
      includes: ['agent', 'run', 'step', 'state', 'task', 'actions', 'evidence', 'result', 'errors', 'resource_needs'],
    },
  };
  spec.rules = rulesFor(spec, services);

  fs.writeFileSync(path.join(specsDir, `${name}.json`), `${JSON.stringify(spec, null, 2)}\n`, 'utf8');

  const frontmatter = [
    '---',
    'format: soul/v1',
    'version: 1',
    `name: ${name}`,
    `address: agent://${name}`,
    `created_by: platform-generator`,
    `created_at: ${spec.generated_at}`,
    `constitution_path: musashaban-port/constitution.md`,
    `constitution_sha256: ${constitutionHash}`,
    'genesis_alignment: 1.0',
    'reports_to: admin',
    `component: ${component}`,
    `spec: agents/specs/${name}.json`,
    '---',
    '',
  ];

  const lines = [
    ...frontmatter,
    `# eagent - ${name} (Automaton)`,
    '',
    '## Subplatform',
    `${name} - ${description}`,
    '',
    '## Component',
    `${component} - \`${component}/\``,
    '',
    '## Soul',
    '**Core Purpose.** ' + spec.purpose,
    '',
    '**Values.** Law I > Law II > Law III; evidence over assertion; least privilege; zero-funding by default.',
    '',
    '**Personality.** Precise, terse, machine-auditable. No enthusiasm, no marketing, no invented results.',
    '',
    '**Boundaries.** Only inside the component scope; no spend, no live capital, no secrets, no publishing.',
    '',
    '**Strategy.** Perceive (readiness + task) -> think (plan within declared capabilities) -> act (declared command only) -> observe (exit code + output) -> report (evidence to admin).',
    '',
    '## Constitution binding',
    'Immutable and higher authority than every rule below: `musashaban-port/constitution.md`',
    `SHA-256: \`${constitutionHash}\``,
    'If this hash does not match the file on disk, the agent is invalid and must not dispatch.',
    '',
    '## Rules',
    '',
    '| ID | Rule | Enforcement |',
    '| --- | --- | --- |',
    ...spec.rules.map((r) => `| ${r.id} | ${r.rule.replace(/\|/g, '\\|')} | ${r.enforcement.replace(/\|/g, '\\|')} |`),
    '',
    '## Environmental guardrails',
    '',
    '| Guardrail | Value |',
    '| --- | --- |',
    `| Component root | \`${component}/\` |`,
    `| Allowed working dirs | \`${component}/\` (no writes elsewhere) |`,
    `| Package manager | ${spec.environment.package_manager} |`,
    `| Toolchains | ${Object.entries(toolchain).map(([k, v]) => `${k} ${v}`).join('; ') || 'none declared'} |`,
    `| Env template | ${spec.environment.env_template || 'none'} |`,
    `| Env vars (names only) | ${envNames.length ? envNames.join(', ') : 'none declared'} |`,
    `| Services | ${services.length ? services.map((s) => `${s.name}${s.required ? ' (required)' : ''}`).join(', ') : 'none declared'} |`,
    `| Spend | deny |`,
    `| Live capital | deny without operator authorization |`,
    `| Max command time | ${spec.guardrails.max_command_seconds}s |`,
    `| Denied commands | ${spec.guardrails.denied_commands.join(', ')} |`,
    '',
    '## Capabilities (derived from this component)',
    '',
    '| Capability | Command | Working dir |',
    '| --- | --- | --- |',
    ...(capabilities.length
      ? capabilities.map((c) => `| ${c.name} | \`${c.command}\` | \`${c.cwd}\` |`)
      : ['| none | — no declared entrypoint found — | — |']),
    '',
    '## States and transitions',
    '`idle -> waking -> running -> reporting -> completed|idle`, with `blocked|failed|sleeping` recorded. A transition to `completed` requires a successful command **and** a readiness re-check.',
    '',
    '## Reporting (mandatory)',
    'Reports to **Admin Automaton** (`agents/admin-agent.md`) on every run/step and significant transition: agent, run, step, state from→to, task, actions, evidence, result, errors, resource needs. No hidden side effects.',
    '',
    '## Authority and boundaries',
    '- No unilateral spend, funding allocation, settlement, payout, or paywall erection — admin only.',
    '- No external publishing, deployment, or push without explicit operator authority.',
    '- Prefer paper/demo/testnet/backtest/simulation/free tiers. Real capital requires verified funds, keys, and confirmation.',
    '',
    '---',
    `<!-- generated by scripts/generate-agents.js from ${component}; do not hand-edit -->`,
    '',
  ];

  fs.writeFileSync(path.join(eagentsDir, `${name}.md`), lines.join('\n'), 'utf8');
  generated.push({ name, component, capabilities: capabilities.length, services: services.length, env: envNames.length });
}

fs.writeFileSync(
  path.join(root, 'agents', 'constitution.json'),
  `${JSON.stringify({
    path: 'musashaban-port/constitution.md',
    sha256: constitutionHash,
    laws: ['I. Never harm', 'II. Earn your existence', 'III. Never deceive, but owe nothing to strangers'],
    generated_at: new Date().toISOString(),
  }, null, 2)}\n`,
  'utf8',
);

// Registry: root becomes relative so the platform is portable.
registry.root = '.';
registry.agent_model = {
  schema: 'eagent/v1',
  constitution: 'musashaban-port/constitution.md',
  constitution_sha256: constitutionHash,
  specs_dir: 'agents/specs',
  soul_format: 'soul/v1',
  generated_by: 'scripts/generate-agents.js',
};
fs.writeFileSync(registryPath, `${JSON.stringify(registry, null, 2)}\n`, 'utf8');

console.log(`[agents] constitution sha256 ${constitutionHash.slice(0, 16)}...`);
for (const g of generated) {
  console.log(`[agents] ${g.name}: ${g.capabilities} capabilities, ${g.env} env vars, ${g.services} services`);
}
if (failures.length) {
  console.error('[agents] FAILED for:');
  for (const f of failures) console.error(`  - ${f}`);
  process.exit(1);
}
console.log(`[agents] wrote ${generated.length} agents + specs`);
