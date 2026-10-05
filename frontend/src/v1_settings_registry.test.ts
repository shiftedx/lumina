import { describe, expect, it } from 'vitest';

import { SETTINGS_SECTION_IDS } from './app/routes';
import { isAdvancedSection, SETTINGS_GROUPS, SETTINGS_SECTIONS, searchSettings, shownEntries, visibleSections } from './features/settings/registry';

const rows = SETTINGS_SECTIONS.flatMap((section) => section.entries.map((entry) => ({ section, entry })));
const sentences = (text: string) => (text.match(/[.!?](?=\s|$)/g) ?? []).length;

const ORDER = ['account', 'appearance', 'discovery', 'streaming', 'playback', 'downloads', 'apps', 'privacy', 'about', 'overview', 'activity', 'members', 'library', 'media', 'requests', 'transcoding', 'ai', 'tasks', 'backups', 'diagnostics'];

describe('Settings groups (owner 2026-10-01: Jellyfin-style)', () => {
  it('names three groups in order: You, Server, Advanced', () => {
    expect(SETTINGS_GROUPS.map((group) => [group.id, group.label])).toEqual([['you', 'You'], ['server', 'Server'], ['advanced', 'Advanced']]);
  });

  it('puts every Settings id in exactly one group, contiguous and in sidebar order', () => {
    expect(SETTINGS_SECTIONS.map((section) => section.id)).toEqual(ORDER);
    expect([...ORDER].sort()).toEqual([...SETTINGS_SECTION_IDS].sort());
    expect(SETTINGS_SECTIONS.map((section) => section.group)).toEqual([...Array(9).fill('you'), ...Array(8).fill('server'), ...Array(3).fill('advanced')]);
    for (const section of SETTINGS_SECTIONS) {
      expect(section.entries.length, section.id).toBeGreaterThan(0);
      expect(section.summary.trim(), section.id).not.toBe('');
    }
  });

  it('uses Jellyfin names where Lumina has the equivalent', () => {
    expect(SETTINGS_SECTIONS.map((section) => section.label)).toEqual(['Profile', 'Display', 'Home & discovery', 'Streaming', 'Playback', 'Downloads', 'Connected apps', 'Privacy & data', 'About', 'Dashboard', 'Activity', 'Members', 'Library & storage', 'Media server', 'Requests', 'Transcoding', 'AI & models', 'Tasks', 'Backups', 'Diagnostics']);
  });

  it('shows a member only the You group and an owner every group', () => {
    expect(new Set(visibleSections({ role: 'viewer' } as never).map((section) => section.group))).toEqual(new Set(['you']));
    expect(visibleSections({ role: 'admin' } as never).map((section) => section.id)).toEqual(ORDER);
  });

  it('still finds a section by its old label or its Jellyfin name', () => {
    const found = (query: string) => searchSettings(query, SETTINGS_SECTIONS).map((match) => match.section.id);
    for (const [query, id] of [['account', 'account'], ['appearance', 'appearance'], ['overview', 'overview'], ['users', 'members'], ['logs', 'diagnostics'], ['scheduled tasks', 'tasks'], ['playback & transcoding', 'transcoding'], ['metadata', 'media']]) expect(found(query), query).toContain(id);
    for (const section of SETTINGS_SECTIONS) for (const alias of section.aliases ?? []) expect(alias, section.id).toBe(alias.toLowerCase());
  });
});

describe('Settings registry', () => {
  it('gives every row a unique id under its section, a label, and ⓘ text of 2–3 plain sentences', () => {
    const ids = rows.map(({ entry }) => entry.id);
    expect(new Set(ids).size).toBe(ids.length);
    expect(ids).toContain('playback.captions');
    for (const { section, entry } of rows) {
      expect(entry.id.startsWith(`${section.id}.`), entry.id).toBe(true);
      expect(entry.label.trim(), entry.id).not.toBe('');
      expect(sentences(entry.info), `${entry.id}: ${entry.info}`).toBeGreaterThanOrEqual(2);
      expect(sentences(entry.info), `${entry.id}: ${entry.info}`).toBeLessThanOrEqual(3);
      expect(entry.info.length, entry.id).toBeLessThanOrEqual(420);
      for (const keyword of entry.keywords ?? []) expect(keyword, entry.id).toBe(keyword.toLowerCase());
    }
  });

  it('keeps row labels unique, so each ⓘ name and search result is unambiguous', () => {
    const labels = rows.map(({ entry }) => entry.label);
    expect(new Set(labels).size).toBe(labels.length);
  });
});

describe('Gallery AI feature rows', () => {
  it('names The story so far and adds switches for watched-episode summaries and key scenes, each needing the assistant', () => {
    const ai = SETTINGS_SECTIONS.find((section) => section.id === 'ai')!;
    const ids = ai.entries.map((entry) => entry.id);
    expect(ids.slice(ids.indexOf('ai.recap'), ids.indexOf('ai.recap') + 3)).toEqual(['ai.recap', 'ai.episode-summaries', 'ai.key-scenes']);
    const row = (id: string) => ai.entries.find((entry) => entry.id === id)!;
    expect(row('ai.recap').label).toBe('“The story so far” recaps');
    expect(row('ai.episode-summaries').label).toBe('Summaries of watched episodes');
    expect(row('ai.key-scenes').label).toBe('Key scenes');
    expect(row('ai.episode-summaries').info).toContain("only ever show the episode guide's teaser");
    for (const id of ['ai.episode-summaries', 'ai.key-scenes']) expect(row(id).info).toContain('assistant server');
  });
});

describe('Library & storage artwork row', () => {
  it('lists artwork preparation under Library & storage with the spec ⓘ text', () => {
    const row = rows.find(({ entry }) => entry.id === 'library.artwork');
    expect(row?.section.id).toBe('library');
    expect(row?.entry.label).toBe('Artwork preparation');
    expect(row?.entry.info).toBe('Lumina prepares small, fast copies of posters, backdrops and episode stills in the background, pausing while anyone watches something that needs converting. Failed images keep using the original file.');
  });
});

describe('Library & storage anime folders row', () => {
  it('sits between Imports and Artwork preparation with the pinned three-sentence ⓘ', () => {
    const library = SETTINGS_SECTIONS.find((section) => section.id === 'library')!;
    const ids = library.entries.map((entry) => entry.id);
    expect(ids.slice(ids.indexOf('library.imports'), ids.indexOf('library.imports') + 3)).toEqual(['library.imports', 'library.anime', 'library.editing']);
    const row = library.entries.find((entry) => entry.id === 'library.anime')!;
    expect(row.label).toBe('Anime folders');
    expect(row.layout).toBe('block');
    expect(row.keywords).toEqual(['anime', 'category', 'folder', 'tab', 'sort', 'jellyfin', 'infuse']);
    expect(row.info).toBe("Titles with a file inside a folder with one of these names appear under Anime instead of Movies or Shows, in Lumina and in Jellyfin apps. Every folder in a file's path counts, including a storage root's own folders, in any upper or lower case, so avoid a name every file passes through, such as a root's parent folder. Saving re-sorts the library in the background for everyone on this server.");
  });
});

describe('Library & storage automation row', () => {
  const library = SETTINGS_SECTIONS.find((section) => section.id === 'library')!;
  it('sits between Retention and Imports as a block row with the pinned ⓘ', () => {
    expect(library.entries.map((entry) => entry.id).filter((id) => id !== 'library.editing')).toEqual(['library.storage', 'library.retention', 'library.automation', 'library.imports', 'library.anime', 'library.artwork']);
    const row = library.entries.find((entry) => entry.id === 'library.automation')!;
    expect(row.layout).toBe('block');
    expect(row.info?.startsWith('Keeps imported folders up to date by themselves')).toBe(true);
  });
  it.each(['watch', 'nightly', 'scan now'])('is found by searching "%s"', (query) => {
    const ids = searchSettings(query, SETTINGS_SECTIONS).flatMap((match) => match.entries.map((entry) => entry.id));
    expect(ids).toContain('library.automation');
  });
});

describe('Show advanced settings (familiar before technical)', () => {
  const advanced = rows.filter(({ entry }) => entry.advanced).map(({ entry }) => entry.id);
  it('marks only technical tuning as advanced, and nothing a household member needs', () => {
    expect(advanced).toEqual([
      'playback.stream-cache', 'overview.concurrency', 'overview.per-member', 'overview.min-free', 'overview.source-ports',
      'library.retention', 'library.automation', 'library.anime', 'library.artwork',
      'media.language', 'media.introdb', 'media.jellyfin-import', 'requests.anime-language',
      'transcoding.hwaccel', 'transcoding.max-sessions', 'transcoding.cache', 'transcoding.diagnostics',
      'ai.model-threads', 'ai.assistant-address', 'ai.assistant-model', 'ai.api-key', 'ai.max-concurrency', 'ai.context-window', 'ai.embedding-model', 'ai.assistant-test', 'ai.speech-address', 'ai.speech-server-model',
      'tasks.list', 'diagnostics.report',
    ]);
  });
  it('hides only Transcoding, Tasks and Diagnostics whole, and still shows a hidden section opened by link', () => {
    expect(SETTINGS_SECTIONS.filter(isAdvancedSection).map((section) => section.id)).toEqual(['transcoding', 'tasks', 'diagnostics']);
    const transcoding = SETTINGS_SECTIONS.find((section) => section.id === 'transcoding')!;
    expect(shownEntries(transcoding, false)).toHaveLength(transcoding.entries.length);
    const playback = SETTINGS_SECTIONS.find((section) => section.id === 'playback')!;
    expect(shownEntries(playback, false).map((entry) => entry.id)).not.toContain('playback.stream-cache');
    expect(shownEntries(playback, true).map((entry) => entry.id)).toContain('playback.stream-cache');
  });
  it('search always finds an advanced row', () => {
    expect(searchSettings('hardware acceleration', SETTINGS_SECTIONS)[0].entries.map((entry) => entry.id)).toContain('transcoding.hwaccel');
  });
});
