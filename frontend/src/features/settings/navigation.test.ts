import { describe, expect, it } from 'vitest';

import { parseRoute, routePath } from '../../app/routes';
import { categoryForPage, pageForRow, resolveSettingsPage, SETTINGS_CATEGORIES, SETTINGS_PAGES } from './navigation';
import { SETTINGS_SECTIONS } from './registry';

describe('separate settings spaces', () => {
  it('has six categories in each space and a valid default page for every category', () => {
    for (const space of ['personal', 'server']) expect(SETTINGS_CATEGORIES.filter((category) => category.space === space)).toHaveLength(6);
    for (const category of SETTINGS_CATEGORIES) expect(SETTINGS_PAGES.find((page) => page.id === category.page)?.category).toBe(category.id);
  });

  it('gives every existing control exactly one destination, without introducing or losing rows', () => {
    const rows = SETTINGS_SECTIONS.flatMap((section) => section.entries.map((entry) => entry.id));
    const destinations = SETTINGS_PAGES.flatMap((page) => page.rows);
    expect(new Set(destinations).size).toBe(destinations.length);
    expect([...destinations].sort()).toEqual([...rows].sort());
    expect(new Set(SETTINGS_PAGES.map((page) => page.id)).size).toBe(SETTINGS_PAGES.length);
    for (const section of SETTINGS_SECTIONS) for (const entry of section.entries) {
      const page = pageForRow(entry.id)!;
      expect(page.section).toBe(section.id);
      expect(categoryForPage(page).space).toBe(section.group === 'you' ? 'personal' : 'server');
    }
  });

  it('round-trips every subpage and keeps old settings, admin and member links working', () => {
    for (const page of SETTINGS_PAGES) {
      const route = { surface: 'settings' as const, section: page.section, page: page.id, space: categoryForPage(page).space };
      const url = new URL(routePath(route), 'https://example.com');
      expect(parseRoute(url.pathname, url.search)).toEqual(route.space === 'personal' ? { surface: route.surface, section: route.section, page: route.page } : route);
    }
    for (const section of SETTINGS_SECTIONS) {
      expect(parseRoute(`/settings/${section.id}`, '')).toEqual({ surface: 'settings', section: section.id });
      expect(resolveSettingsPage(section.id).section).toBe(section.id);
    }
    expect(parseRoute('/manage', '')).toEqual({ surface: 'settings', space: 'server' });
    expect(parseRoute('/manage/members/u1', '')).toEqual({ surface: 'settings', space: 'server', section: 'members', memberId: 'u1' });
    expect(resolveSettingsPage('media').id).toBe('metadata');
    expect(resolveSettingsPage('requests').id).toBe('sonarr');
    expect(parseRoute('/admin/imports', '')).toEqual({ surface: 'settings', section: 'library', page: 'imports' });
    expect(parseRoute('/admin/ai', '')).toEqual({ surface: 'settings', section: 'ai' });
  });

  it('rejects unknown or cross-section subpages and malformed member ids', () => {
    expect(parseRoute('/manage/ai', '?page=email')).toEqual({ surface: 'settings', space: 'server', section: 'ai' });
    expect(parseRoute('/manage/members/%ZZ', '')).toEqual({ surface: 'settings', space: 'server', section: 'members' });
    expect(parseRoute('/manage/ai', '?page=constructor')).toEqual({ surface: 'settings', space: 'server', section: 'ai' });
  });

  it('keeps essential AI setup visible while technical tuning stays local', () => {
    const assistant = resolveSettingsPage('ai', 'assistant');
    expect(assistant.essential).toContain('ai.assistant-address');
    expect(assistant.essential).not.toContain('ai.context-window');
    expect(pageForRow('requests.smtp')?.id).toBe('email');
    expect(pageForRow('requests.policies')?.category).toBe('household');
    expect(pageForRow('members.public-address')?.category).toBe('connections');
    expect(pageForRow('diagnostics.updates')?.id).toBe('updates');
    expect(resolveSettingsPage('diagnostics').id).toBe('diagnostics');
  });
});
