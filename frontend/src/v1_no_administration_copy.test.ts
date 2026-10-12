import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { expect, it } from 'vitest';

/** Every source file under src/, tests excluded. */
function sources(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return sources(path);
    return /\.tsx?$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name) ? [path] : [];
  });
}

it('never sends anyone to "Administration": it is Settings now', () => {
  const root = __dirname;
  const offenders = sources(root).filter((path) => {
    const code = readFileSync(path, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');  // comments are not copy
    return /Administration/.test(code);
  });
  expect(offenders.map((path) => path.slice(root.length + 1))).toEqual([]);
});
