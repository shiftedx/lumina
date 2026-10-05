import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { CollectionDetail } from './CollectionDetail';
import { collection, entry, fakeDetailApi, libraryItem, poster } from './testFixtures';

const base = { api: fakeDetailApi(), onBack: vi.fn(), onChanged: vi.fn(), onReload: vi.fn(), owner: true };

describe('Collection detail', () => {
  it('heads the page with kicker, name and meta, and shows rule chips for a smart collection', () => {
    render(<CollectionDetail {...base} collection={collection({ name: 'Rainy Sundays', rules: { match: 'all', conditions: [{ field: 'genre', op: 'is', value: 'Drama' }] } as never, titles: [poster('t1')] })} onEditRules={vi.fn()} />);
    expect(screen.getByRole('heading', { level: 1, name: 'Rainy Sundays' })).toBeTruthy();
    expect(document.querySelector('.g-kicker')?.textContent).toBe('Smart collection');
    expect(screen.getByText(/^1 title · updated /)).toBeTruthy();
    expect(document.querySelectorAll('.g-chip').length).toBe(1);
    expect(screen.getByRole('button', { name: 'Edit rules' })).toBeTruthy();
  });

  it('the detail grid renders titles as posters and items as stills', () => {
    const rules = { match: 'all', conditions: [] } as never;
    const { unmount } = render(<CollectionDetail {...base} collection={collection({ rules, titles: [poster('t1'), poster('t2')] })} />);
    expect(document.querySelectorAll('.g-poster')).toHaveLength(2);
    unmount();
    render(<CollectionDetail {...base} collection={collection({ rules: { match: 'all', type: 'channel_video', conditions: [] } as never, items: [libraryItem('v1')] })} />);
    expect(document.querySelectorAll('.g-still-card').length).toBe(1);
  });

  it('deleting a collection asks first and names it', async () => {
    const onDelete = vi.fn();
    render(<CollectionDetail {...base} collection={collection({ name: 'Family videos' })} onDelete={onDelete} onRename={vi.fn()} />);
    await userEvent.click(screen.getByRole('button', { name: 'Delete' }));
    const dialog = screen.getByRole('dialog', { name: 'Delete “Family videos”?' });
    expect(within(dialog).getByText('The titles and videos in it stay in your library.')).toBeTruthy();
    expect(document.activeElement).toBe(within(dialog).getByRole('button', { name: 'Cancel' }));
    await userEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(onDelete).not.toHaveBeenCalled();
  });

  it('keeps a manual collection an ordered list with move, remove and no card components', () => {
    render(<CollectionDetail {...base} collection={collection({ name: 'Mix', entries: [entry('e1', 'First'), entry('e2', 'Second')] })} onDelete={vi.fn()} onRename={vi.fn()} />);
    expect(screen.getAllByRole('listitem').length).toBeGreaterThanOrEqual(2);
    expect((screen.getByRole('button', { name: 'Move First down' }) as HTMLButtonElement).disabled).toBe(false);
    expect((screen.getByRole('button', { name: 'Move First up' }) as HTMLButtonElement).disabled).toBe(true);
  });
});
