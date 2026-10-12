import { fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { EpisodeTable, EpisodeTableRow } from '../../../types';
import { docFixture } from './editorFixtures';
import type { Draft, TabProps } from './editorModel';

const api = vi.hoisted(() => ({ listMetadataEpisodes: vi.fn(), getTitle: vi.fn() }));
vi.mock('../../../api', async (importOriginal) => ({ ...(await importOriginal<typeof import('../../../api')>()), ...api }));
const { default: EpisodesTab } = await import('./EpisodesTab');

const row = (n: number, patch: Partial<EpisodeTableRow> = {}): EpisodeTableRow => ({
  title_id: `ep-${n}`, index_number: n, index_number_end: null, name: `Episode ${n}`, premiered: '2022-02-18', runtime_minutes: 45,
  overview: `About ${n}`, community_rating: 8.1, locked_fields: [], locked: false, still_url: null, ...patch,
});
const seasons = [
  { id: 's1', name: 'Season 1', index_number: 1, episode_count: 2 },
  { id: 's2', name: 'Season 2', index_number: 2, episode_count: 1 },
];
const table = (episodes: EpisodeTableRow[], season = seasons[0]): EpisodeTable => ({ season, seasons, episodes });

function props(patch: Partial<TabProps> = {}, draft: Draft = {}): TabProps {
  return {
    doc: docFixture('series'), draft, user: {} as never, reload: vi.fn(), episodesRevision: 0,
    setField: vi.fn(), togglePin: vi.fn(), setItemLock: vi.fn(), discardField: vi.fn(),
    ...patch,
  };
}
afterEach(() => { vi.resetAllMocks(); vi.unstubAllGlobals(); });

function stubNarrow() {
  vi.stubGlobal('matchMedia', () => ({ matches: true, addEventListener: () => undefined, removeEventListener: () => undefined }));
}

describe('EpisodesTab', () => {
  it('loads the season named in the segmented control and shows one row per episode, ordered by number', async () => {
    api.getTitle.mockResolvedValue({ children: [{ id: 's1', type: 'season', name: 'Season 1', index_number: 1 }, { id: 's2', type: 'season', name: 'Season 2', index_number: 2 }] });
    api.listMetadataEpisodes.mockImplementation((_: string, id: string) => Promise.resolve(id === 's1' ? table([row(2), row(1)]) : table([row(3)], seasons[1])));
    const { Component } = { Component: EpisodesTab };
    render(<Component {...props()} />);
    await screen.findByLabelText('Name of Episode 1');
    expect(api.listMetadataEpisodes).toHaveBeenCalledWith('t1', 's1');
    const names = screen.getAllByLabelText(/^Name of/).map((el) => (el as HTMLInputElement).value);
    expect(names).toEqual(['Episode 1', 'Episode 2']);
    fireEvent.click(screen.getByRole('radio', { name: 'Season 2' }));
    await screen.findByLabelText('Name of Episode 3');
    expect(api.listMetadataEpisodes).toHaveBeenCalledWith('t1', 's2');
  });

  it('a season doc shows its own season only, with no season control', async () => {
    api.listMetadataEpisodes.mockResolvedValue(table([row(1)]));
    render(<EpisodesTab {...props({ doc: docFixture('season', { title_id: 's1', parent: { id: 't1', type: 'series', name: 'Severance' } }) })} />);
    await screen.findByLabelText('Name of Episode 1');
    expect(api.listMetadataEpisodes).toHaveBeenCalledWith('t1', 's1');
    expect(api.getTitle).not.toHaveBeenCalled();
    expect(screen.queryByRole('radio')).toBeNull();
  });

  it('editing a name records a change on that episode title id with the row value as base', async () => {
    api.getTitle.mockResolvedValue({ children: [{ id: 's1', type: 'season', name: 'Season 1', index_number: 1 }] });
    api.listMetadataEpisodes.mockResolvedValue(table([row(1), row(2)]));
    const setField = vi.fn();
    render(<EpisodesTab {...props({ setField })} />);
    fireEvent.change(await screen.findByLabelText('Name of Episode 2'), { target: { value: 'The Lamp' } });
    expect(setField).toHaveBeenCalledWith('ep-2', 'name', 'The Lamp', 'Episode 2');
  });

  it('refuses a blank name and an out-of-range runtime inline without recording a change', async () => {
    api.getTitle.mockResolvedValue({ children: [{ id: 's1', type: 'season', name: 'Season 1', index_number: 1 }] });
    api.listMetadataEpisodes.mockResolvedValue(table([row(1)]));
    const setField = vi.fn();
    render(<EpisodesTab {...props({ setField })} />);
    fireEvent.change(await screen.findByLabelText('Name of Episode 1'), { target: { value: ' ' } });
    fireEvent.change(screen.getByLabelText('Runtime of Episode 1'), { target: { value: '5000' } });
    for (const [, , value, base] of setField.mock.calls) expect(value).toBe(base);
    expect(screen.getByLabelText('Name of Episode 1').getAttribute('aria-invalid')).toBe('true');
    expect(screen.getByLabelText('Runtime of Episode 1').getAttribute('aria-invalid')).toBe('true');
  });

  it('a keystroke sequence that turns invalid drops the earlier valid prefix from the draft', async () => {
    api.getTitle.mockResolvedValue({ children: [{ id: 's1', type: 'season', name: 'Season 1', index_number: 1 }] });
    api.listMetadataEpisodes.mockResolvedValue(table([row(1)]));
    const calls: unknown[][] = [];
    render(<EpisodesTab {...props({ setField: ((...args: unknown[]) => { calls.push(args); }) as never })} />);
    const name = await screen.findByLabelText('Name of Episode 1');
    fireEvent.change(name, { target: { value: 'L' } });
    fireEvent.change(name, { target: { value: '' } });
    expect(calls.at(-1)).toEqual(['ep-1', 'name', 'Episode 1', 'Episode 1']);
    expect(name.getAttribute('aria-invalid')).toBe('true');
    expect((name as HTMLInputElement).value).toBe('');
    const runtime = screen.getByLabelText('Runtime of Episode 1');
    fireEvent.change(runtime, { target: { value: '500' } });
    fireEvent.change(runtime, { target: { value: '5000' } });
    expect(calls.at(-1)?.[2]).toBe(row(1).runtime_minutes);
  });

  it('edited cells carry the word Edited for screen readers and the is-edited class', async () => {
    api.getTitle.mockResolvedValue({ children: [{ id: 's1', type: 'season', name: 'Season 1', index_number: 1 }] });
    api.listMetadataEpisodes.mockResolvedValue(table([row(1)]));
    const draft: Draft = { 'ep-1': { changes: { name: { value: 'The Lamp', base: 'Episode 1' } }, pin: [] } };
    render(<EpisodesTab {...props({}, draft)} />);
    const input = (await screen.findByLabelText('Name of Episode 1')) as HTMLInputElement;
    expect(input.value).toBe('The Lamp');
    const cell = input.closest('td') as HTMLElement;
    expect(cell.classList.contains('is-edited')).toBe(true);
    expect(within(cell).getByText('Edited')).toBeTruthy();
  });

  it('Enter moves down the same column; on the last row it stays put', async () => {
    api.getTitle.mockResolvedValue({ children: [{ id: 's1', type: 'season', name: 'Season 1', index_number: 1 }] });
    api.listMetadataEpisodes.mockResolvedValue(table([row(1), row(2)]));
    render(<EpisodesTab {...props()} />);
    const first = await screen.findByLabelText('Name of Episode 1');
    first.focus();
    fireEvent.keyDown(first, { key: 'Enter' });
    const second = screen.getByLabelText('Name of Episode 2');
    expect(document.activeElement).toBe(second);
    fireEvent.keyDown(second, { key: 'Enter' });
    expect(document.activeElement).toBe(second);
  });

  it('Overview is two lines until focused, then a textarea; Enter inside it adds a line instead of moving down', async () => {
    api.getTitle.mockResolvedValue({ children: [{ id: 's1', type: 'season', name: 'Season 1', index_number: 1 }] });
    api.listMetadataEpisodes.mockResolvedValue(table([row(1), row(2)]));
    render(<EpisodesTab {...props()} />);
    const button = await screen.findByRole('button', { name: 'Overview of Episode 1' });
    expect(button.textContent).toBe('About 1');
    fireEvent.focus(button);
    const area = screen.getByLabelText('Overview of Episode 1');
    expect(area.tagName).toBe('TEXTAREA');
    area.focus();
    fireEvent.keyDown(area, { key: 'Enter' });
    expect(document.activeElement).toBe(area);
  });

  it('the row lock toggles the item lock in the draft', async () => {
    api.getTitle.mockResolvedValue({ children: [{ id: 's1', type: 'season', name: 'Season 1', index_number: 1 }] });
    api.listMetadataEpisodes.mockResolvedValue(table([row(1, { locked: true }), row(2)]));
    const setItemLock = vi.fn();
    render(<EpisodesTab {...props({ setItemLock })} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Unlock Episode 1' }));
    expect(setItemLock).toHaveBeenCalledWith('ep-1', false, true);
    fireEvent.click(screen.getByRole('button', { name: 'Lock Episode 2' }));
    expect(setItemLock).toHaveBeenCalledWith('ep-2', true, false);
  });

  it('shows a lock glyph with hidden text Locked in a cell whose field is in locked_fields', async () => {
    api.getTitle.mockResolvedValue({ children: [{ id: 's1', type: 'season', name: 'Season 1', index_number: 1 }] });
    api.listMetadataEpisodes.mockResolvedValue(table([row(1, { locked_fields: ['name'] })]));
    render(<EpisodesTab {...props()} />);
    const cell = (await screen.findByLabelText('Name of Episode 1')).closest('td') as HTMLElement;
    expect(within(cell).getByText('Locked')).toBeTruthy();
    expect(within((screen.getByLabelText('Runtime of Episode 1')).closest('td') as HTMLElement).queryByText('Locked')).toBeNull();
  });

  it('reloads when the episodes revision changes', async () => {
    api.getTitle.mockResolvedValue({ children: [{ id: 's1', type: 'season', name: 'Season 1', index_number: 1 }] });
    api.listMetadataEpisodes.mockResolvedValue(table([row(1)]));
    const { rerender } = render(<EpisodesTab {...props()} />);
    await screen.findByLabelText('Name of Episode 1');
    rerender(<EpisodesTab {...props({ episodesRevision: 1 })} />);
    await vi.waitFor(() => expect(api.listMetadataEpisodes).toHaveBeenCalledTimes(2));
  });

  it('on a narrow screen shows a list; selecting a row opens a sheet with the fields and returns focus on close', async () => {
    stubNarrow();
    api.getTitle.mockResolvedValue({ children: [{ id: 's1', type: 'season', name: 'Season 1', index_number: 1 }] });
    api.listMetadataEpisodes.mockResolvedValue(table([row(1), row(2)]));
    const setField = vi.fn();
    render(<EpisodesTab {...props({ setField })} />);
    const open = await screen.findByRole('button', { name: /Episode 2/ });
    open.focus();
    fireEvent.click(open);
    const dialog = await screen.findByRole('dialog', { name: 'Episode 2' });
    for (const label of ['Name', 'Air date', 'Runtime', 'Rating', 'Overview', 'Ends at', 'Number']) expect(within(dialog).getByLabelText(label)).toBeTruthy();
    fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: 'Lamp' } });
    expect(setField).toHaveBeenCalledWith('ep-2', 'name', 'Lamp', 'Episode 2');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Close' }));
    await vi.waitFor(() => expect(document.activeElement).toBe(open));
  });

  it('shows skeleton, empty and error states', async () => {
    api.getTitle.mockResolvedValue({ children: [{ id: 's1', type: 'season', name: 'Season 1', index_number: 1 }] });
    api.listMetadataEpisodes.mockReturnValueOnce(new Promise(() => undefined));
    const first = render(<EpisodesTab {...props()} />);
    expect((await screen.findByRole('status')).textContent).toBe('Loading episodes');
    first.unmount();
    api.listMetadataEpisodes.mockResolvedValueOnce(table([]));
    const second = render(<EpisodesTab {...props()} />);
    expect(await screen.findByText('No episodes in this season yet.')).toBeTruthy();
    second.unmount();
    api.listMetadataEpisodes.mockRejectedValueOnce(new Error('boom')).mockResolvedValueOnce(table([row(1)]));
    render(<EpisodesTab {...props()} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Try again' }));
    await screen.findByLabelText('Name of Episode 1');
  });

  it('says when a season has 500 episodes that only the first 500 are shown', async () => {
    api.getTitle.mockResolvedValue({ children: [{ id: 's1', type: 'season', name: 'Season 1', index_number: 1 }] });
    api.listMetadataEpisodes.mockResolvedValue(table(Array.from({ length: 500 }, (_, i) => row(i + 1))));
    render(<EpisodesTab {...props()} />);
    expect(await screen.findByText('Only the first 500 episodes are shown.')).toBeTruthy();
  });
});
