#!/usr/bin/env node
'use strict';

/**
 * Start the AutoTrading Ideal Platform orchestrator from the repo root.
 *
 *   npm start                 start the server and open the dashboard
 *   npm start -- --no-open    start the server only
 *   NO_OPEN=1 npm start       same as --no-open
 *
 * The runtime is Python, so this launcher starts `python -m orchestrator` in
 * this directory, waits for /health, then opens the dashboard once.
 */

const { spawn, spawnSync } = require('node:child_process');
const http = require('node:http');
const path = require('node:path');

const host = process.env.ORCHESTRATOR_HOST || '127.0.0.1';
const port = Number(process.env.ORCHESTRATOR_PORT || 8765);
const python = process.env.PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
const url = `http://${host}:${port}/`;
const shouldOpen = process.env.NO_OPEN !== '1' && !process.argv.includes('--no-open');

function checkHealth() {
  return new Promise((resolve) => {
    const req = http.get({ host, port, path: '/health', timeout: 1500 }, (res) => {
      res.resume();
      resolve(res.statusCode === 200);
    });
    req.on('error', () => resolve(false));
    req.on('timeout', () => {
      req.destroy();
      resolve(false);
    });
  });
}

async function waitForHealth(deadline) {
  while (Date.now() < deadline) {
    if (await checkHealth()) return true;
    await new Promise((resolve) => setTimeout(resolve, 400));
  }
  return false;
}

function openBrowser(target) {
  const platform = process.platform;
  const cmd = platform === 'win32' ? 'cmd' : platform === 'darwin' ? 'open' : 'xdg-open';
  const args = platform === 'win32' ? ['/c', 'start', '""', target] : [target];
  try {
    const child = spawn(cmd, args, { detached: true, stdio: 'ignore', windowsHide: true });
    child.on('error', () => {});
    child.unref();
  } catch {
    // Opening a browser is best-effort.
  }
}

function autoInit() {
  if (process.env.SKIP_INIT === '1') {
    console.log('SKIP_INIT=1: dependency check skipped by operator.');
    return;
  }
  console.log('Checking shared dependencies (set SKIP_INIT=1 to skip)...');
  const result = spawnSync(
    process.execPath,
    [path.join(__dirname, 'init-platform.js'), '--auto'],
    { cwd: path.join(__dirname, '..'), stdio: 'inherit', env: process.env },
  );
  if (result.error || result.status !== 0) {
    console.error('Dependency check failed. Fix the error above and run `npm run init`.');
    console.error('Starting the orchestrator with uninitialized components would report false readiness.');
    process.exit(1);
  }
}

async function main() {
  autoInit();

  if (await checkHealth()) {
    console.log(`AutoTrading Ideal Platform is already running at ${url}`);
    if (shouldOpen) openBrowser(url);
    return;
  }

  console.log('Starting AutoTrading Ideal Platform orchestrator...');
  console.log(`  interpreter: ${python}`);
  console.log(`  dashboard:   ${url}`);

  const child = spawn(python, ['-m', 'orchestrator'], {
    cwd: path.join(__dirname, '..'),
    env: process.env,
    stdio: 'inherit',
  });

  let stopping = false;
  function stop(signal) {
    if (stopping) return;
    stopping = true;
    try {
      child.kill(signal);
    } catch {
      // already gone
    }
  }

  process.on('SIGINT', () => stop('SIGINT'));
  process.on('SIGTERM', () => stop('SIGTERM'));

  child.on('error', (err) => {
    console.error(`Unable to start Python (${python}): ${err.message}`);
    console.error('Install Python 3 and put it on PATH, or set PYTHON to its full path.');
    process.exit(1);
  });

  child.on('exit', (code) => {
    process.exit(code === null ? 0 : code);
  });

  if (shouldOpen) {
    const healthy = await waitForHealth(Date.now() + 15000);
    if (healthy) {
      openBrowser(url);
    } else if (!stopping) {
      console.log(`The orchestrator has not reported healthy yet; open ${url} manually once it does.`);
    }
  }
}

main().catch((err) => {
  console.error(err && err.message ? err.message : String(err));
  process.exit(1);
});
