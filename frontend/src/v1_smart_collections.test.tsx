import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { HouseholdCollection, SmartCollectionRule } from './types';

const { parseConditionValue, ruleChipLabel } = await import('./features/collections/SmartCollectionBuilder');
const { CollectionDetail } = await import('./features/collections/CollectionDetail');

afterEach(() => { vi.resetAllMocks(); });

const rule: SmartCollectionRule = { type: 'movie', match: 'all', conditions: [{ field: 'genre', op: 'is', value: 'Comedy' }, { field: 'year', op: 'gte', value: 1990 }, { field: 'watched', op: 'is', value: 'unwatched' }], limit: 100 };

describe('smart rule chips', () => {
  it('reads rules in plain words and parses typed values by field', () => {
    expect(rule.conditions.map(ruleChipLabel)).toEqual(['Genre is Comedy', 'Year at least 1990', 'Watched is Unwatched']);
    expect(ruleChipLabel({ field: 'added', op: 'within_days', value: 30 })).toBe('Added within the last 30 days');
    expect(ruleChipLabel({ field: 'genre', op: 'in', value: ['Comedy', 'Drama'] })).toBe('Genre is any of Comedy, Drama');
    expect(parseConditionValue('year', 'gte', ' 1990 ')).toBe(1990);
    expect(parseConditionValue('year', 'gte', 'nineties')).toBeNull();
    expect(parseConditionValue('genre', 'in', 'Comedy, , Drama')).toEqual(['Comedy', 'Drama']);
    expect(parseConditionValue('genre', 'is', '   ')).toBeNull();
  });
});

describe('CollectionDetail for a smart collection', () => {
  it('shows its rules and titles, and no reorder or remove controls', async () => {
    const onOpenTitle = vi.fn();
    const title = { id: 'm1', type: 'movie', name: 'Groundhog Day', year: 1993, genres: [], added_at: '2026-09-01T00:00:00Z', user_data: { played: false, is_favorite: false, position_seconds: 0 } } as const;
    const collection = { id: 'c1', owner_user_id: 'u1', name: 'Nineties comedies', visibility: 'private', revision: 1, item_count: 1, items: [], entries: [], rules: rule, titles: [title], created_at: '', updated_at: '' } as unknown as HouseholdCollection;
    render(<CollectionDetail collection={collection} onChanged={vi.fn()} onOpenTitle={onOpenTitle} onReload={vi.fn()} owner />);
    expect(screen.getByRole('list', { name: 'Rules' }).textContent).toContain('Year at least 1990');
    await userEvent.click(screen.getByRole('button', { name: /^Groundhog Day/ }));
    expect(onOpenTitle).toHaveBeenCalledWith(title);
    expect(screen.queryByRole('button', { name: /Move|Remove/ })).toBeNull();
    expect(screen.queryByPlaceholderText('Paste a public video link')).toBeNull();
  });

  it('shows channel-video matches as library items, not titles', async () => {
    const onOpenLibrary = vi.fn();
    const channelRule: SmartCollectionRule = { type: 'channel_video', match: 'all', conditions: [{ field: 'channel', op: 'is', value: 'Some Channel' }], limit: 100 };
    const item = { id: 'lib1', title: 'Some Channel Video' };
    const collection = { id: 'c2', owner_user_id: 'u1', name: 'Channel picks', visibility: 'private', revision: 1, item_count: 1, items: [item], entries: [], rules: channelRule, titles: [], created_at: '', updated_at: '' } as unknown as HouseholdCollection;
    render(<CollectionDetail collection={collection} onChanged={vi.fn()} onOpenLibrary={onOpenLibrary} onReload={vi.fn()} owner />);
    expect(screen.getByRole('list', { name: 'Rules' }).textContent).toContain('Channel is Some Channel');
    await userEvent.click(screen.getByRole('button', { name: /^Some Channel Video/ }));
    expect(onOpenLibrary).toHaveBeenCalledWith('lib1');
  });
});
