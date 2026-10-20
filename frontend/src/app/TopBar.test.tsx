import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createRef } from 'react';
import { describe, expect, it, vi } from 'vitest';
import type { LibraryActivity, UserProfile } from '../types';
import { EDIT_HOME_EVENT, MEMBER_PICKER_EVENT, onCommand } from './commands';
import { TopBar, type TopBarProps } from './TopBar';

// The ring size comes from the gallery styles: pinned here so this test does not depend on them.
vi.mock('../features/auth/memberPicker', () => ({ useDeviceRingSize: () => 1 }));

const user = { id: 'u1', username: 'dana', display_name: 'Dana Lee', role: 'admin', is_active: true } as UserProfile;
const library = (patch: Partial<LibraryActivity> = {}): LibraryActivity => ({
  scanning_roots: 0, discovering_roots: 0, indexing_roots: 0, preparing_roots: 0, stalled_roots: 0, waiting_files: 0, waiting_error_files: 0,
  discovered_files: 0, completed_files: 0, total_files: null, progress: null, inspected_files: 0, directories_listed: 0, directories_total: null, ...patch,
});
const props = (patch: Partial<TopBarProps> = {}): TopBarProps => ({
  user, mobile: false, compact: false, menuLabel: 'Open navigation', menuExpanded: true, menuButtonRef: createRef(),
  activity: { state: 'ready', retrying: false, active: 2 }, libraryActivity: { state: 'ready', activity: library() }, theme: 'system',
  onMenu: vi.fn(), onHome: vi.fn(), onSearch: vi.fn(), onAddLink: vi.fn(), onActivity: vi.fn(), onSettings: vi.fn(), onTheme: vi.fn(), onSignOut: vi.fn(),
  ...patch,
});

describe('TopBar', () => {
  it('has the menu button, a home brand link and a search trigger named with the shortcut', async () => {
    const p = props();
    render(<TopBar {...p} />);
    expect(screen.getByRole('button', { name: 'Open navigation' }).getAttribute('aria-expanded')).toBe('true');
    expect(screen.getByRole('link', { name: 'Lumina' }).getAttribute('href')).toBe('/');
    await userEvent.click(screen.getByRole('button', { name: /^Search Lumina, (⌘K|Ctrl K)$/ }));
    expect(p.onSearch).toHaveBeenCalledOnce();
  });

  it('keeps the activity labels and opens Downloads', async () => {
    const p = props();
    render(<TopBar {...p} />);
    await userEvent.click(screen.getByRole('button', { name: '2 downloads' }));
    const panel = screen.getByRole('dialog', { hidden: true });
    expect(panel.getAttribute('aria-label')).toBe('Activity');
    expect(panel.textContent).toContain('No library scan running');
    expect(panel.textContent).not.toContain('up to date');
    expect(within(panel).getByText('No library scan running').closest('.g-status')?.classList.contains('is-muted')).toBe(true);
    await userEvent.click(screen.getByRole('button', { name: 'Open Downloads', hidden: true }));
    expect(p.onActivity).toHaveBeenCalledOnce();
  });

  it('keeps the shell usable while a ready activity snapshot is empty or from an older server', () => {
    const base = props({ activity: { state: 'empty', retrying: false, active: 0 }, libraryActivity: { state: 'ready', activity: null } });
    const { rerender } = render(<TopBar {...base} />);
    expect(screen.getByRole('button', { name: 'No active downloads' })).toBeTruthy();

    const legacy: Partial<LibraryActivity> = library({ scanning_roots: 1 });
    delete legacy.stalled_roots;
    delete legacy.waiting_error_files;
    rerender(<TopBar {...props({ activity: base.activity, libraryActivity: { state: 'ready', activity: legacy as LibraryActivity } })} />);
    expect(screen.getByRole('button', { name: 'Library scanning' }).querySelector('.g-activity-ring')?.classList.contains('is-indeterminate')).toBe(true);
  });

  it('explains stale activity in a popover', async () => {
    const onActivity = vi.fn();
    render(<TopBar {...props({ activity: { state: 'stale', retrying: false, active: 0 }, onActivity })} />);
    await userEvent.click(screen.getByRole('button', { name: 'Download activity may be out of date' }));
    // jsdom treats the popover as hidden, which empties its computed name: assert the label attribute instead.
    const panel = screen.getByRole('dialog', { hidden: true });
    expect(panel.getAttribute('aria-label')).toBe('Activity');
    expect(panel.textContent).toContain('Download activity may be out of date');
    await userEvent.click(screen.getByRole('button', { name: 'Open Downloads', hidden: true })); // the way out of a stale state stays reachable
    expect(onActivity).toHaveBeenCalledOnce();
  });

  it('combines download and library work without exposing location details', async () => {
    render(<TopBar {...props({ libraryActivity: { state: 'ready', activity: library({ scanning_roots: 2, waiting_files: 3, inspected_files: 37 }) } })} />);
    const trigger = screen.getByRole('button', { name: '2 downloads · Library scanning, 37 files checked' });
    expect(trigger.querySelector('.g-activity-ring')?.classList.contains('is-indeterminate')).toBe(true);
    await userEvent.click(trigger);
    const panel = screen.getByRole('dialog', { hidden: true });
    expect(panel.textContent).toContain('Scanning library');
    expect(panel.textContent).toContain('3 files are still copying or stabilizing');
    expect(panel.textContent).toContain('New files appear in the library after indexing.');
    expect(panel.textContent).not.toMatch(/\/Volumes\/|root-|title-/);
  });

  it('shows reliable indexing progress and keeps discovery indeterminate', () => {
    const { rerender } = render(<TopBar {...props({ activity: { state: 'ready', retrying: false, active: 0 }, libraryActivity: { state: 'ready', activity: library({ scanning_roots: 1, indexing_roots: 1, completed_files: 4, total_files: 10, progress: 40 }) } })} />);
    const determinate = screen.getByRole('button', { name: 'Indexing library, 4 of 10 checked' });
    expect(determinate.querySelector('.g-activity-ring')?.classList.contains('is-indeterminate')).toBe(false);
    expect(determinate.querySelector('.g-activity-ring')?.getAttribute('style')).toContain('144deg');

    rerender(<TopBar {...props({ activity: { state: 'ready', retrying: false, active: 0 }, libraryActivity: { state: 'ready', activity: library({ scanning_roots: 1, discovering_roots: 1, discovered_files: 4 }) } })} />);
    expect(screen.getByRole('button', { name: 'Discovering files, 4 found' }).querySelector('.g-activity-ring')?.classList.contains('is-indeterminate')).toBe(true);

    rerender(<TopBar {...props({ activity: { state: 'ready', retrying: false, active: 0 }, libraryActivity: { state: 'ready', activity: library({ scanning_roots: 1, indexing_roots: 1, completed_files: 6380 }) } })} />);
    expect(screen.getByRole('button', { name: 'Indexing library, 6380 files checked' }).querySelector('.g-activity-ring')?.classList.contains('is-indeterminate')).toBe(true);
  });

  it('shows waiting and failed file checks without running the scan spinner', async () => {
    render(<TopBar {...props({ activity: { state: 'empty', retrying: false, active: 0 }, libraryActivity: { state: 'ready', activity: library({ waiting_files: 3, waiting_error_files: 1 }) } })} />);
    const trigger = screen.getByRole('button', { name: '1 file waiting to be checked' });
    expect(trigger.querySelector('.g-activity-ring')?.classList.contains('is-indeterminate')).toBe(false);
    await userEvent.click(trigger);
    const panel = screen.getByRole('dialog', { hidden: true });
    expect(panel.textContent).toContain('2 files are still copying or stabilizing');
    expect(panel.textContent).toContain('1 file could not be checked yet and will be retried');
    expect(panel.textContent).not.toContain('No library scan running');
  });

  it('keeps thumbnail preparation visible when file scanning is idle', async () => {
    render(<TopBar {...props({ activity: { state: 'empty', retrying: false, active: 0 }, libraryActivity: { state: 'ready', activity: library({ artwork_pending: 3, artwork_missing: 2, artwork_failed: 1 }) } })} />);
    const trigger = screen.getByRole('button', { name: 'Preparing 3 thumbnails' });
    expect(trigger.querySelector('.g-activity-ring')?.classList.contains('is-indeterminate')).toBe(true);
    await userEvent.click(trigger);
    const panel = screen.getByRole('dialog', { hidden: true });
    expect(panel.textContent).toContain('No library scan running');
    expect(panel.textContent).toContain('Preparing thumbnails');
    expect(panel.textContent).toContain('3 thumbnails are queued for preparation.');
    expect(panel.textContent).toContain('2 images still need a thumbnail.');
    expect(panel.textContent).toContain('1 thumbnail could not be prepared.');
    expect(panel.textContent).not.toContain('Artwork needs attention');
  });

  it('reports paused thumbnail work without a spinner', async () => {
    render(<TopBar {...props({ activity: { state: 'empty', retrying: false, active: 0 }, libraryActivity: { state: 'ready', activity: library({ artwork_pending: 1, artwork_missing: 0, artwork_failed: 0, artwork_paused: true }) } })} />);
    const trigger = screen.getByRole('button', { name: '1 thumbnail queued' });
    expect(trigger.querySelector('.g-activity-ring')?.classList.contains('is-indeterminate')).toBe(false);
    await userEvent.click(trigger);
    const panel = screen.getByRole('dialog', { hidden: true });
    expect(panel.textContent).toContain('Thumbnail preparation paused');
    expect(panel.textContent).toContain('Paused while a video is being converted.');
  });

  it('replaces a stale scan spinner with a safe attention state', async () => {
    render(<TopBar {...props({ activity: { state: 'empty', retrying: false, active: 0 }, libraryActivity: { state: 'ready', activity: library({ scanning_roots: 1, stalled_roots: 1, discovered_files: 12 }) } })} />);
    const trigger = screen.getByRole('button', { name: 'Library scan needs attention' });
    expect(trigger.querySelector('.g-activity-ring')?.classList.contains('is-indeterminate')).toBe(false);
    await userEvent.click(trigger);
    expect(screen.getByRole('dialog', { hidden: true }).textContent).toContain('One scan has stopped reporting progress');
  });

  it('opens phase details with true indexing progress and discovery counts only', async () => {
    const { rerender } = render(<TopBar {...props({ activity: { state: 'empty', retrying: false, active: 0 }, libraryActivity: { state: 'ready', activity: library({ scanning_roots: 1, discovering_roots: 1, discovered_files: 18 }) } })} />);
    await userEvent.click(screen.getByRole('button', { name: 'Discovering files, 18 found' }));
    const discovery = screen.getByRole('dialog', { hidden: true });
    expect(discovery.textContent).toContain('18 files found. Counting candidates before indexing begins.');
    expect(within(discovery).queryByRole('progressbar', { hidden: true })).toBeNull();

    rerender(<TopBar {...props({ activity: { state: 'empty', retrying: false, active: 0 }, libraryActivity: { state: 'ready', activity: library({ scanning_roots: 1, indexing_roots: 1, completed_files: 18, total_files: 24, progress: 75 }) } })} />);
    const indexing = screen.getByRole('dialog', { hidden: true });
    const progress = within(indexing).getByRole('progressbar', { name: 'Library indexing progress', hidden: true });
    expect(progress.getAttribute('value')).toBe('75');
    expect(indexing.textContent).toContain('18 of 24 checked · 75%');
  });

  it('keeps Downloads reachable when library activity is loading or unavailable', async () => {
    const p = props({ activity: { state: 'empty', retrying: false, active: 0 }, libraryActivity: { state: 'loading', activity: null } });
    const { rerender } = render(<TopBar {...p} />);
    await userEvent.click(screen.getByRole('button', { name: 'Checking library activity' }));
    await userEvent.click(screen.getByRole('button', { name: 'Open Downloads', hidden: true }));
    expect(p.onActivity).toHaveBeenCalledOnce();

    rerender(<TopBar {...props({ activity: { state: 'empty', retrying: false, active: 0 }, libraryActivity: { state: 'failed', activity: null } })} />);
    expect(screen.getByRole('button', { name: 'Library activity unavailable' })).toBeTruthy();
  });

  it('offers the profile menu: switch member, settings, theme and sign out', async () => {
    const p = props({ theme: 'dark' });
    const picker = vi.fn();
    const stop = onCommand(MEMBER_PICKER_EVENT, picker);
    render(<TopBar {...p} />);
    await userEvent.click(screen.getByRole('button', { name: 'Dana Lee, account menu' }));
    expect(screen.getByRole('group', { name: 'Dana Lee · Vault owner', hidden: true })).toBeTruthy();
    expect(screen.getByRole('menuitemradio', { name: /Dark/, hidden: true }).getAttribute('aria-checked')).toBe('true');
    await userEvent.click(screen.getByRole('menuitem', { name: 'Switch member…', hidden: true }));
    expect(picker).toHaveBeenCalledOnce();
    await userEvent.click(screen.getByRole('button', { name: 'Dana Lee, account menu' }));
    await userEvent.click(screen.getByRole('menuitemradio', { name: /Light/, hidden: true }));
    expect(p.onTheme).toHaveBeenCalledWith('light');
    await userEvent.click(screen.getByRole('button', { name: 'Dana Lee, account menu' }));
    await userEvent.click(screen.getByRole('menuitemradio', { name: /OLED/, hidden: true }));
    expect(p.onTheme).toHaveBeenLastCalledWith('oled');
    await userEvent.click(screen.getByRole('button', { name: 'Dana Lee, account menu' }));
    await userEvent.click(screen.getByRole('menuitem', { name: 'Sign out', hidden: true }));
    expect(p.onSignOut).toHaveBeenCalledOnce();
    stop();
  });

  it('opens Home before requesting its editor from the account menu', async () => {
    const p = props();
    const edit = vi.fn(() => expect(p.onHome).toHaveBeenCalledOnce());
    const stop = onCommand(EDIT_HOME_EVENT, edit);
    render(<TopBar {...p} />);
    await userEvent.click(screen.getByRole('button', { name: 'Dana Lee, account menu' }));
    await userEvent.click(screen.getByRole('menuitem', { name: 'Edit home', hidden: true }));
    await waitFor(() => expect(edit).toHaveBeenCalledOnce());
    stop();
  });

  it('is icon-only for Add link on phones and shows only the sun when compact', () => {
    render(<TopBar {...props({ mobile: true, compact: true })} />);
    expect(screen.getByRole('button', { name: 'Add a link' }).textContent).not.toContain('Add link');
    expect(screen.getByText('Lumina').className).toContain('sr-only');
  });
});
