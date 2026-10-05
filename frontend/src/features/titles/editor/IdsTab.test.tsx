import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import type { TitleType } from '../../../types';
import { docFixture, fieldState } from './editorFixtures';
import IdsTab from './IdsTab';
import type { TabProps } from './editorModel';

function setup(type: TitleType = 'movie', role = 'admin', ids: Record<string, string> = { Tmdb: '438631', Imdb: 'tt1160419', Tvdb: '77' }) {
  const doc = docFixture(type);
  doc.fields.provider_ids = fieldState(ids);
  const setField = vi.fn();
  render(<IdsTab {...({ doc, draft: {}, user: { role }, setField } as unknown as TabProps)} />);
  return { setField, ids };
}
const hrefs = () => [...document.querySelectorAll('a')].map((a) => a.getAttribute('href'));

describe('IdsTab', () => {
  it('shows stored values and links out with noreferrer', () => {
    setup('movie');
    expect((screen.getByLabelText('TMDB') as HTMLInputElement).value).toBe('438631');
    expect(hrefs()).toEqual(['https://www.themoviedb.org/movie/438631', 'https://www.imdb.com/title/tt1160419/', 'https://thetvdb.com/dereferrer/movie/77']);
    expect(document.querySelector('a')!.getAttribute('rel')).toContain('noreferrer');
  });

  it('uses tv and series links for a series, and no TMDB/TVDB links for a season', () => {
    const { unmount } = render(<div />);
    unmount();
    setup('series');
    expect(hrefs()).toContain('https://www.themoviedb.org/tv/438631');
    expect(hrefs()).toContain('https://thetvdb.com/dereferrer/series/77');
  });

  it('has no TMDB or TVDB link on a season', () => {
    setup('season');
    expect(hrefs()).toEqual(['https://www.imdb.com/title/tt1160419/']);
  });

  it('warns verbatim when TMDB changes, and records the whole map against the loaded map', () => {
    const { setField, ids } = setup();
    fireEvent.change(screen.getByLabelText('TMDB'), { target: { value: '5' } });
    expect(screen.getByText('Changing the TMDB ID re-identifies this title: details from the old match are removed and fetched again.')).toBeTruthy();
    expect(setField).toHaveBeenCalledWith('t1', 'provider_ids', { ...ids, Tmdb: '5' }, ids);
  });

  it('is read-only for members, with the note', () => {
    setup('movie', 'viewer');
    expect((screen.getByLabelText('TMDB') as HTMLInputElement).readOnly).toBe(true);
    expect(screen.getByText('Only vault owners can change the TMDB match.')).toBeTruthy();
  });

  it('validates like the server and keeps invalid values out of the draft', () => {
    const { setField } = setup();
    fireEvent.change(screen.getByLabelText('IMDb'), { target: { value: 'tt12' } });
    expect(screen.getByText('Use tt followed by 7 to 10 digits.')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('TMDB'), { target: { value: '12345678901' } });
    expect(screen.getByText('Use up to 10 digits.')).toBeTruthy();
    expect(setField).not.toHaveBeenCalled();
  });

  it('adds another ID and removes it again', async () => {
    const { setField, ids } = setup();
    await userEvent.type(screen.getByLabelText('Other ID name'), 'Anidb');
    await userEvent.type(screen.getByLabelText('Other ID value'), '99');
    await userEvent.click(screen.getByRole('button', { name: 'Add another ID' }));
    expect(setField).toHaveBeenLastCalledWith('t1', 'provider_ids', { ...ids, Anidb: '99' }, ids);
    await userEvent.click(screen.getByRole('button', { name: 'Remove Anidb' }));
    expect(setField).toHaveBeenLastCalledWith('t1', 'provider_ids', ids, ids);
  });

  it.each([['Tmdb', '5', 'admin'], ['tmdb', '5', 'admin'], ['Imdb', 'nope', 'admin'], ['Tvdb', 'x', 'admin'], ['Tmdb', '5', 'viewer']])('refuses to add known key %s=%s as %s', async (key, value, role) => {
    const { setField } = setup('movie', role);
    await userEvent.type(screen.getByLabelText('Other ID name'), key);
    await userEvent.type(screen.getByLabelText('Other ID value'), value);
    await userEvent.click(screen.getByRole('button', { name: 'Add another ID' }));
    expect(setField).not.toHaveBeenCalled();
  });

  it('refuses a case-insensitive duplicate of another ID', async () => {
    const { setField } = setup('movie', 'admin', { Tmdb: '1', Anidb: '9' });
    await userEvent.type(screen.getByLabelText('Other ID name'), 'ANIDB');
    await userEvent.type(screen.getByLabelText('Other ID value'), '10');
    await userEvent.click(screen.getByRole('button', { name: 'Add another ID' }));
    expect(setField).not.toHaveBeenCalled();
  });
});
