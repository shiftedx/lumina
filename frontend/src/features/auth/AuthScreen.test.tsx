import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '../../api';
import type { DeviceMember } from '../../types';
import { AuthScreen, type AuthScreenProps } from './AuthScreen';
import { resetDeviceRing } from './deviceRing';

const props = (patch: Partial<AuthScreenProps> = {}): AuthScreenProps => ({ stage: 'login', busy: false, error: null, onLogin: vi.fn(), onSetup: vi.fn(), onRetrySession: vi.fn(), onSwitched: vi.fn(), sessionProblem: null, ...patch });
beforeEach(() => { resetDeviceRing(); vi.spyOn(api, 'getDeviceMembers').mockResolvedValue([]); });
afterEach(() => vi.restoreAllMocks());
const describedBy = (el: HTMLElement) => (el.getAttribute('aria-describedby') ?? '').split(' ').map((id) => document.getElementById(id)?.textContent).join(' ');

describe('AuthScreen', () => {
  it('login: fields, autocomplete tokens, remember off by default, and the heading id', async () => {
    const p = props();
    render(<AuthScreen {...p} />);
    expect((await screen.findByRole('heading', { level: 1, name: 'Welcome home' })).getAttribute('id')).toBe('auth-title');
    expect(document.querySelector('.g-kicker')?.textContent).toBe('Sign in');
    expect(screen.getByLabelText('Username').getAttribute('autocomplete')).toBe('username');
    expect(screen.getByLabelText('Password').getAttribute('autocomplete')).toBe('current-password');
    const remember = screen.getByRole('checkbox', { name: "Show me in Who's watching on this device" }) as HTMLInputElement;
    expect(remember.checked).toBe(false);
    await userEvent.type(screen.getByLabelText('Username'), ' Dana ');
    await userEvent.type(screen.getByLabelText('Password'), 'secret-passphrase');
    await userEvent.click(remember);
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(p.onLogin).toHaveBeenCalledWith(' Dana ', 'secret-passphrase', true);
  });

  it('shows a sign-in error on the password field and never renders it as HTML', async () => {
    render(<AuthScreen {...props({ error: '<b>Invalid username or password</b>' })} />);
    expect((await screen.findByLabelText('Password')).getAttribute('aria-invalid')).toBe('true');
    expect(screen.getByText('<b>Invalid username or password</b>')).toBeTruthy();
  });

  it('setup, invite and reset stages have their fields, new-password tokens and copy', () => {
    const { rerender } = render(<AuthScreen {...props({ stage: 'setup' })} />);
    expect(screen.getByRole('heading', { name: 'Make Lumina yours' })).toBeTruthy();
    expect(screen.getByLabelText('Display name').getAttribute('autocomplete')).toBe('name');
    expect(screen.getByLabelText('Password').getAttribute('autocomplete')).toBe('new-password');
    expect(describedBy(screen.getByLabelText('Password'))).toBe('At least 12 characters.');
    expect(screen.getByRole('button', { name: 'Create household vault' })).toBeTruthy();
    expect(screen.queryByRole('checkbox')).toBeNull();
    rerender(<AuthScreen {...props({ stage: 'invite', onCancel: vi.fn() })} />);
    expect(screen.getByRole('heading', { name: 'Join this household' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Back to sign in' })).toBeTruthy();
    rerender(<AuthScreen {...props({ stage: 'reset', onCancel: vi.fn() })} />);
    expect(screen.queryByLabelText('Username')).toBeNull();
    expect(screen.getByLabelText('New password').getAttribute('autocomplete')).toBe('new-password');
    expect(screen.getByRole('button', { name: 'Set new password' })).toBeTruthy();
  });

  it('loading shows a hairline skeleton; a session problem offers to try again', async () => {
    const onRetrySession = vi.fn();
    const { rerender } = render(<AuthScreen {...props({ stage: 'loading' })} />);
    expect(screen.getByRole('status').textContent).toContain('Preparing your collection…');
    rerender(<AuthScreen {...props({ onRetrySession, sessionProblem: { kind: 'timeout', message: 'Lumina took too long to answer.', requiresSignIn: false, retryOperation: 'load' } })} />);
    expect(screen.getByRole('alert').textContent).toContain('The vault did not respond');
    await userEvent.click(screen.getByRole('button', { name: 'Try opening the vault again' }));
    expect(onRetrySession).toHaveBeenCalledOnce();
  });

  it('keeps library artwork off the page before sign-in', () => {
    const { container } = render(<AuthScreen {...props()} />);
    expect(container.querySelector('img')).toBeNull();
  });

  it('asks nobody for the ring on setup, invite or reset', () => {
    render(<AuthScreen {...props({ stage: 'setup' })} />);
    expect(api.getDeviceMembers).not.toHaveBeenCalled();
  });

  it('shows "Who\'s watching?" when the ring has members, and the form under "Someone else"', async () => {
    const ring: DeviceMember[] = [{ user_id: 'm1', display_name: 'Dana', username: 'dana', role: 'viewer', switch: 'instant', active: false }];
    vi.mocked(api.getDeviceMembers).mockResolvedValue(ring);
    const switchMember = vi.spyOn(api, 'switchMember').mockResolvedValue({ user: { id: 'm1' } } as never);
    const p = props();
    render(<AuthScreen {...p} />);
    expect((await screen.findByRole('heading', { level: 1, name: "Who's watching?" })).getAttribute('id')).toBe('auth-title');
    expect(screen.queryByLabelText('Username')).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: 'Someone else' }));
    expect(screen.getByRole('heading', { level: 1, name: 'Welcome home' })).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: "Choose who's watching" }));
    await userEvent.click(screen.getByRole('button', { name: 'Continue as Dana' }));
    expect(switchMember).toHaveBeenCalledWith('m1');
    expect(p.onSwitched).toHaveBeenCalledOnce();
  });
});

describe('AuthScreen release showcase', () => {
  const slide = (title: string, extra: Partial<api.ShowcaseSlide> = {}): api.ShowcaseSlide => ({ backdrop_url: `/api/public/showcase/art/${title.length}${title[0]}`, title, caption: 'In cinemas Friday', kind: 'movie', ...extra });
  beforeEach(() => { HTMLImageElement.prototype.decode = () => Promise.resolve(); });

  it('paints the calm page first and keeps it when there are no slides', async () => {
    vi.spyOn(api, 'getShowcase').mockResolvedValue({ slides: [] });
    render(<AuthScreen {...props()} />);
    expect(await screen.findByText('Your films, your shows, your household.')).toBeTruthy();
    await vi.waitFor(() => expect(api.getShowcase).toHaveBeenCalled());
    expect(document.querySelector('.has-art')).toBeNull();
  });

  it('never asks while a signed-in member boots', async () => {
    vi.spyOn(api, 'getShowcase').mockResolvedValue({ slides: [] });
    render(<AuthScreen {...props({ stage: 'loading' })} />);
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(api.getShowcase).not.toHaveBeenCalled();
  });

  it('shows public release art behind the form, with pips after the form in tab order', async () => {
    vi.spyOn(api, 'getShowcase').mockResolvedValue({ slides: [
      slide('Dune: Part Three', { logo_url: '/api/public/showcase/art/logo' }), slide('Frieren', { caption: 'New this season', kind: 'anime' }),
      slide('Elsewhere', { backdrop_url: 'https://image.tmdb.org/t/p/w1280/x.jpg' }), // never an upstream URL
    ] });
    const p = props();
    render(<AuthScreen {...p} />);
    expect(await screen.findByRole('img', { name: 'Dune: Part Three' })).toBeTruthy();
    expect(document.querySelector('.g-auth')?.className).toContain('has-art');
    expect(screen.getByText('In cinemas Friday')).toBeTruthy();
    const pips = screen.getByRole('group', { name: 'Featured releases' }).querySelectorAll('button');
    expect([...pips].map((b) => b.getAttribute('aria-label'))).toEqual(['Dune: Part Three', 'Frieren']);
    expect(pips[0].getAttribute('aria-current')).toBe('true');
    expect(screen.getByLabelText('Username').compareDocumentPosition(pips[0]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    pips[0].focus();
    await userEvent.keyboard('{ArrowRight}');
    expect(await screen.findByText('Frieren', { selector: '.g-show-title' })).toBeTruthy();
    expect(document.activeElement).toBe(pips[1]);
    await userEvent.click(screen.getByRole('button', { name: 'Pause the showcase' }));
    expect(screen.getByRole('button', { name: 'Play the showcase' })).toBeTruthy();

    await userEvent.type(screen.getByLabelText('Username'), 'dana');
    await userEvent.type(screen.getByLabelText('Password'), 'secret-passphrase');
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(p.onLogin).toHaveBeenCalledWith('dana', 'secret-passphrase', false);
  });
});
