/**
 * The remote identity parity table: what the client files remote progress under.
 * The backend's Python mirror (remote_annotation.remote_source_identity) is proved against the same file.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

import { remoteSourceIdentity } from '../playbackModel';

type Case = { provider: string; id: string | null; webpage_url: string | null; identity: string | null };
const cases = JSON.parse(readFileSync(join(__dirname, '../../../backend/tests/remote_identity_cases.json'), 'utf8')) as Case[];

describe('remote source identity parity table', () => {
  it.each(cases)('$webpage_url ($provider) is $identity', ({ provider, id, webpage_url, identity }) => {
    expect(remoteSourceIdentity({ id, source: provider, webpage_url })).toBe(identity);
  });
});
