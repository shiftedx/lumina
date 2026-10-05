import type { FullConfig, Reporter, TestCase, TestResult } from '@playwright/test/reporter';
import { existsSync, readdirSync, readFileSync, renameSync, unlinkSync, writeFileSync } from 'node:fs';
import { strFromU8, strToU8, unzipSync, zipSync } from 'fflate';

const STRUCTURAL_STRING_KEYS = new Set([
  'apiName',
  'callId',
  'class',
  'frameId',
  'method',
  'pageId',
  'phase',
  'status',
  'type',
]);

function redactTraceValue(value: unknown, key = ''): unknown {
  if (typeof value === 'string') return STRUCTURAL_STRING_KEYS.has(key) ? value : '[redacted]';
  if (Array.isArray(value)) return value.map((entry) => redactTraceValue(entry));
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).map(([entryKey, entry]) => [entryKey, redactTraceValue(entry, entryKey)]));
  }
  return value;
}

function redactJsonLines(bytes: Uint8Array): Uint8Array {
  const redacted = strFromU8(bytes)
    .split('\n')
    .filter(Boolean)
    .map((line) => {
      try {
        return JSON.stringify(redactTraceValue(JSON.parse(line)));
      } catch {
        return JSON.stringify({ type: 'redacted-unparseable-trace-entry' });
      }
    })
    .join('\n');
  return strToU8(`${redacted}\n`);
}

export function redactTraceArchive(archivePath: string): void {
  const replacementPath = `${archivePath}.redacting-${process.pid}`;
  try {
    const archive = unzipSync(new Uint8Array(readFileSync(archivePath)));
    const safeEntries: Record<string, Uint8Array> = {};
    for (const [entryName, bytes] of Object.entries(archive)) {
      if (entryName.includes('/resources/') || entryName.startsWith('resources/') || entryName.endsWith('.network')) continue;
      if (entryName.endsWith('.trace')) safeEntries[entryName] = redactJsonLines(bytes);
      else if (entryName.endsWith('.stacks')) safeEntries[entryName] = strToU8('{}\n');
    }
    safeEntries['REDACTION.txt'] = strToU8(
      'Lumina removed network bodies, resource blobs, action parameters, selectors, values, URLs, and stack paths before retaining this failure trace.\n',
    );
    writeFileSync(replacementPath, zipSync(safeEntries, { level: 6 }), { mode: 0o600 });
    renameSync(replacementPath, archivePath);
  } catch (error) {
    try { unlinkSync(replacementPath); } catch { /* replacement was never written */ }
    try { unlinkSync(archivePath); } catch { /* raw archive is already absent */ }
    throw error;
  }
}

export default class RedactingArtifactReporter implements Reporter {
  private outputDirs: string[] = [];

  onBegin(config: FullConfig): void {
    this.outputDirs = config.projects.map((project) => project.outputDir);
  }

  onTestEnd(_test: TestCase, result: TestResult): void {
    for (const attachment of result.attachments) {
      if (attachment.name === 'trace' && attachment.path) redactTraceArchive(attachment.path);
    }
  }

  // Traces finished after onTestEnd (or left truncated) never reach attachments; sweep them before exit.
  async onExit(): Promise<void> {
    for (const dir of this.outputDirs.filter((entry) => existsSync(entry))) {
      for (const name of readdirSync(dir, { recursive: true, encoding: 'utf8' })) {
        if (!name.endsWith('trace.zip')) continue;
        try { redactTraceArchive(`${dir}/${name}`); } catch { /* unreadable archive was deleted */ }
      }
    }
  }
}
