// Writes dist/third-party-licenses.txt: the license text of every production npm package in the bundle.
// The minifier drops license comments, so this file carries the notices MIT/BSD/ISC/Apache require.
import { existsSync, readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const seen = new Map();

function packageDir(name, from) {
  for (let dir = from; ; dir = dirname(dir)) {
    const candidate = join(dir, 'node_modules', name);
    if (existsSync(join(candidate, 'package.json'))) return candidate;
    if (dirname(dir) === dir) throw new Error(`cannot resolve ${name} from ${from}`);
  }
}

function walk(dir) {
  const pkg = JSON.parse(readFileSync(join(dir, 'package.json'), 'utf8'));
  const key = `${pkg.name}@${pkg.version}`;
  if (seen.has(key)) return;
  const files = readdirSync(dir).filter((f) => /^(licen[cs]e|copying|notice)/i.test(f)).sort();
  seen.set(key, { license: pkg.license ?? 'UNKNOWN', text: files.map((f) => readFileSync(join(dir, f), 'utf8').trim()).join('\n\n') });
  for (const dep of Object.keys(pkg.dependencies ?? {})) walk(packageDir(dep, dir));
}

const app = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'));
for (const dep of Object.keys(app.dependencies)) walk(packageDir(dep, root));

const out = [...seen].sort(([a], [b]) => a.localeCompare(b)).map(([key, { license, text }]) =>
  `${'='.repeat(78)}\n${key} (${license})\n${'='.repeat(78)}\n${text || `Licensed under ${license}; no license file in the package.`}\n`);
writeFileSync(join(root, 'dist', 'third-party-licenses.txt'), `Third-party software in the Lumina web app.\n\n${out.join('\n')}`);
console.log(`third-party-licenses.txt: ${seen.size} packages`);
