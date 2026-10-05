import { createEvent, fireEvent, render, screen } from '@testing-library/react';
import { createRef } from 'react';
import { describe, expect, it, vi } from 'vitest';
import type { UserProfile } from '../types';
import { AppShell } from './AppShell';

const user = { id: 'u1', username: 'a', display_name: 'A', role: 'admin', is_active: true } as UserProfile;

const shell = () => (
  <AppShell
    activity={{ state: 'ready', retrying: false, active: 0 }} announcer={null} compact={false} drawer={false} mainRef={createRef()} menuButtonRef={createRef()} mobile={false}
    moreItems={null} navigationOpen={false} onAddLink={vi.fn()} onCloseNavigation={vi.fn()} onMenu={vi.fn()} onNavigate={vi.fn()} onSearch={vi.fn()} onSettings={vi.fn()}
    onSignOut={vi.fn()} onTheme={vi.fn()} sidebarCollapsed={false} surface="home" theme="system" user={user}
  ><h1>Page</h1></AppShell>
);

const menuButton = () => document.querySelector('.g-topbar-start button') as HTMLButtonElement;
const firstItem = () => screen.getByRole('navigation', { name: 'Primary' }).querySelector('button') as HTMLButtonElement;

describe('menu button Tab', () => {
  it('moves focus into the open sidebar and consumes the Tab', () => {
    render(shell());
    menuButton().focus();
    const event = createEvent.keyDown(menuButton(), { key: 'Tab' });
    fireEvent(menuButton(), event);
    expect(document.activeElement).toBe(firstItem());
    expect(event.defaultPrevented).toBe(true);
  });

  it('never swallows a Tab it could not act on (the sidebar item cannot take focus yet)', () => {
    render(shell());
    firstItem().focus = vi.fn(); // a focus() on a still-hidden element is a silent no-op
    menuButton().focus();
    const event = createEvent.keyDown(menuButton(), { key: 'Tab' });
    fireEvent(menuButton(), event);
    expect(event.defaultPrevented).toBe(false);
  });
});
