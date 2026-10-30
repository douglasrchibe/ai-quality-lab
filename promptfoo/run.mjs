// Runs the locally installed (pinned) Promptfoo CLI with this project's settings:
//   - PROMPTFOO_PYTHON points at the project's virtual environment, where ai_quality_lab is installed
//   - telemetry, update checks, sharing and remote generation are disabled
//   - reports/promptfoo/ exists so `-o reports/promptfoo/...` can write results
// Usage: node promptfoo/run.mjs <promptfoo arguments>   (normally via the npm scripts)

import { spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

const venvPython =
  process.platform === 'win32'
    ? path.join(root, '.venv', 'Scripts', 'python.exe')
    : path.join(root, '.venv', 'bin', 'python');
const python = process.env.PROMPTFOO_PYTHON || venvPython;
if (!existsSync(python)) {
  console.error(`Python not found at ${python}.`);
  console.error('Create the virtual environment first (see README: Setup), or set PROMPTFOO_PYTHON.');
  process.exit(2);
}

// Promptfoo's package "exports" do not expose package.json, so read it from the local install.
const pkgPath = path.join(root, 'node_modules', 'promptfoo', 'package.json');
let cli;
try {
  const pkg = JSON.parse(readFileSync(pkgPath, 'utf8'));
  const bin = typeof pkg.bin === 'string' ? pkg.bin : pkg.bin.promptfoo;
  cli = path.join(path.dirname(pkgPath), bin);
} catch {
  console.error('Promptfoo is not installed. Run `npm ci` in the project root first.');
  process.exit(2);
}

mkdirSync(path.join(root, 'reports', 'promptfoo'), { recursive: true });

const env = {
  ...process.env,
  PROMPTFOO_PYTHON: python,
  PROMPTFOO_DISABLE_TELEMETRY: '1',
  PROMPTFOO_DISABLE_UPDATE: '1',
  PROMPTFOO_DISABLE_SHARING: '1',
  PROMPTFOO_DISABLE_REMOTE_GENERATION: '1',
};

const run = spawnSync(process.execPath, [cli, ...process.argv.slice(2)], {
  cwd: root,
  env,
  stdio: 'inherit',
});
if (run.error) {
  console.error(run.error.message);
  process.exit(1);
}
process.exit(run.status ?? 1);
