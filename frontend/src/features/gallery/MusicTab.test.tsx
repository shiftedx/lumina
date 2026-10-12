import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from '../../api';
import { albumSummary, librarySections, stillItem, titlePage } from '../../test/galleryFixtures';
import type { LibrarySections } from '../../types';
import { resetImageLoader } from './imageLoader';
import { MusicTab, parseMusicState, serializeMusicState } from './MusicTab';
import { forgetWallStores } from './wallPages';

function tab(state = '', sections: LibrarySections | null = librarySections()) {
  const onWallChange = vi.fn();
  const onPlay = vi.fn();
  render(<MusicTab canDelete={() => false} lenses={<nav aria-label="Library" />} onItemChanged={vi.fn()} onOpen={vi.fn()} onPlay={onPlay} onWallChange={onWallChange} sections={sections} state={state} />);
  return { onWallChange, onPlay };
}

beforeEach(() => {
  forgetWallStores();
  resetImageLoader();
  window.scrollTo = vi.fn() as unknown as typeof window.scrollTo;
});
afterEach(() => vi.restoreAllMocks());

describe('MusicTab', () => {
  it('renders the Music masthead under the tab row, counting albums and artists and naming the sort', async () => {
    vi.spyOn(api, 'listTitles').mockResolvedValue(titlePage([albumSummary()], { total: 1 }));
    vi.spyOn(api, 'listLibrary').mockResolvedValue({ items: [], next_cursor: null });
    tab();
    expect(screen.getByRole('navigation', { name: 'Library' })).toBeTruthy();
    expect(screen.getByRole('heading', { level: 1, name: 'Music' })).toBeTruthy();
    expect(screen.getByText('110 albums · 108 artists · Sorted by recently added')).toBeTruthy();
    expect(screen.getByRole('group', { name: 'Show' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Albums' }).getAttribute('aria-pressed')).toBe('true');
    expect(await screen.findByRole('button', { name: 'Album One by Artist A, 2019, 12 tracks' })).toBeTruthy();
  });

  it('switches views through the address, keeping each view’s own wall keys', async () => {
    const list = vi.spyOn(api, 'listTitles').mockResolvedValue(titlePage([], { total: 0 }));
    vi.spyOn(api, 'listLibrary').mockResolvedValue({ items: [], next_cursor: null });
    const { onWallChange } = tab('view=artists&sort=created');
    expect(screen.getByRole('button', { name: 'Artists' }).getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByText('110 albums · 108 artists · Sorted by recently added')).toBeTruthy();
    expect(list.mock.calls[0][0]).toMatchObject({ type: 'artist', sort: 'created' });
    expect(await screen.findByText('No artists yet')).toBeTruthy();
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Sort' }), 'name');
    expect(onWallChange).toHaveBeenLastCalledWith('view=artists');
    await userEvent.click(screen.getByRole('button', { name: 'Albums' }));
    expect(onWallChange).toHaveBeenLastCalledWith('');
    await userEvent.click(screen.getByRole('button', { name: 'Saved audio' }));
    expect(onWallChange).toHaveBeenLastCalledWith('view=saved');
  });

  it('drops an unknown view and keeps Saved audio only when there is saved audio', () => {
    expect(parseMusicState('view=bogus&sort=year')).toEqual({ view: 'albums', wall: 'sort=year' });
    expect(parseMusicState(undefined)).toEqual({ view: 'albums', wall: '' });
    expect(serializeMusicState('artists', 'sort=created')).toBe('view=artists&sort=created');
    expect(serializeMusicState('albums', 'sort=year')).toBe('sort=year');
    vi.spyOn(api, 'listTitles').mockResolvedValue(titlePage([], { total: 0 }));
    const library = vi.spyOn(api, 'listLibrary').mockResolvedValue({ items: [], next_cursor: null });
    tab('', librarySections({ saved_audio: 0 }));
    expect(screen.queryByRole('button', { name: 'Saved audio' })).toBeNull();
    expect(library).not.toHaveBeenCalled();
  });

  it('shows the newest 24 saved audio items on the Albums view, plays one, and See all opens the saved view', async () => {
    const saved = [stillItem('audio-1', { kind: 'audio', title: 'Harbor walk (audio)' })];
    const library = vi.spyOn(api, 'listLibrary').mockResolvedValue({ items: saved, next_cursor: null });
    vi.spyOn(api, 'listTitles').mockResolvedValue(titlePage([albumSummary()], { total: 1 }));
    const { onPlay, onWallChange } = tab();
    await userEvent.click(await screen.findByRole('button', { name: /^Harbor walk \(audio\)/ }));
    expect(onPlay).toHaveBeenCalledWith(saved[0]);
    expect(library).toHaveBeenCalledWith({ kind: 'audio', sort: 'recent', limit: 24 });
    expect(screen.getByRole('heading', { level: 2, name: 'Saved audio' })).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'See all' }));
    expect(onWallChange).toHaveBeenLastCalledWith('view=saved');
  });

  it('shows the saved view as the square still wall under the Music masthead, and names only counts before the sort exists', () => {
    vi.spyOn(api, 'listLibrary').mockResolvedValue({ items: [], next_cursor: null });
    tab('view=saved');
    expect(screen.getByRole('heading', { level: 1, name: 'Music' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Saved audio' }).getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByText('110 albums · 108 artists')).toBeTruthy();
    expect(screen.queryByRole('heading', { level: 2, name: 'Saved audio' })).toBeNull();
  });
});
