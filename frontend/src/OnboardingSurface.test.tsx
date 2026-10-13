import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { OnboardingSurface, WelcomeSurface } from './features/onboarding/Onboarding';

const categories = [{ key: 'music', label: 'Music' }, { key: 'cooking', label: 'Cooking' }];

describe('new-member welcome', () => {
  it('introduces factual vault capabilities before personalization', async () => {
    const browser = userEvent.setup();
    const onBegin = vi.fn();
    render(<WelcomeSurface displayName="Dana" onBegin={onBegin} onSkip={vi.fn()} />);

    expect(screen.getByRole('heading', { level: 1, name: 'Welcome to Lumina' })).toBe(document.activeElement);
    expect(screen.getByText('The vault is open, Dana.')).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Your library' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'A Home of your own' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Ready on any screen' })).toBeTruthy();

    await browser.click(screen.getByRole('button', { name: 'Personalize my Home' }));
    expect(onBegin).toHaveBeenCalledTimes(1);
  });

  it('keeps a clear durable skip and disables both decisions while saving', async () => {
    const browser = userEvent.setup();
    const onSkip = vi.fn();
    const view = render(<WelcomeSurface onBegin={vi.fn()} onSkip={onSkip} />);
    await browser.click(screen.getByRole('button', { name: 'Explore without setup' }));
    expect(onSkip).toHaveBeenCalledTimes(1);

    view.rerender(<WelcomeSurface busy onBegin={vi.fn()} onSkip={onSkip} />);
    expect((screen.getByRole('button', { name: 'Personalize my Home' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: 'Explore without setup' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('uses the approved vault artwork decoratively and surfaces failures', () => {
    const { container } = render(<WelcomeSurface error="Setup is unavailable." onBegin={vi.fn()} onSkip={vi.fn()} reducedMotion />);
    const artwork = container.querySelector<HTMLImageElement>('img.g-onboarding-vault-art');
    expect(artwork?.getAttribute('src')).toBe('/onboarding/vault-arrival-v2.jpg');
    expect(artwork?.getAttribute('alt')).toBe('');
    expect(screen.getByRole('alert').textContent).toContain('Setup is unavailable.');
    expect(container.querySelector('.reduced-motion')).toBeTruthy();
  });
});

describe('first-run onboarding interaction', () => {
  it('continues with the interests a member selects', async () => {
    const browser = userEvent.setup();
    const onContinue = vi.fn();
    render(<OnboardingSurface categories={categories} onContinue={onContinue} onSkip={vi.fn()} phase="setup" />);

    await browser.click(screen.getByRole('checkbox', { name: 'Music' }));
    await browser.click(screen.getByRole('button', { name: 'Continue' }));

    expect(onContinue).toHaveBeenCalledWith(['music']);
  });

  it('lets a member continue with none selected', async () => {
    const browser = userEvent.setup();
    const onContinue = vi.fn();
    render(<OnboardingSurface categories={categories} onContinue={onContinue} onSkip={vi.fn()} phase="setup" />);

    await browser.click(screen.getByRole('button', { name: 'Continue' }));

    expect(onContinue).toHaveBeenCalledWith([]);
  });

  it('lets a member explicitly skip first-run setup', async () => {
    const browser = userEvent.setup();
    const onSkip = vi.fn();
    render(<OnboardingSurface categories={categories} onContinue={vi.fn()} onSkip={onSkip} phase="setup" />);

    await browser.click(screen.getByRole('button', { name: 'Skip for now' }));

    expect(onSkip).toHaveBeenCalledTimes(1);
  });

  it('lands keyboard focus on the surface heading so setup is reachable', () => {
    render(<OnboardingSurface categories={categories} onContinue={vi.fn()} onSkip={vi.fn()} phase="setup" />);
    expect(screen.getByRole('heading', { level: 1, name: 'What draws you in?' })).toBe(document.activeElement);
  });

  it('disables the controls while a decision is being saved', () => {
    render(<OnboardingSurface busy categories={categories} onContinue={vi.fn()} onSkip={vi.fn()} phase="setup" />);
    expect((screen.getByRole('button', { name: 'Continue' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: 'Skip for now' }) as HTMLButtonElement).disabled).toBe(true);
  });
});

describe('editorial onboarding', () => {
  it('starts with a manageable set and makes every interest reachable', async () => {
    const many = [...categories, ...Array.from({ length: 9 }, (_, i) => ({ key: `interest-${i}`, label: `Interest ${i}` }))];
    render(<OnboardingSurface categories={many} onContinue={vi.fn()} onSkip={vi.fn()} phase="setup" />);
    expect(screen.getAllByRole('checkbox')).toHaveLength(2);
    await userEvent.click(screen.getByRole('button', { name: 'Explore all 11 interests' }));
    expect(screen.getAllByRole('checkbox')).toHaveLength(11);
    await userEvent.click(screen.getByRole('checkbox', { name: 'Interest 8' }));
    expect(screen.getByRole('checkbox', { name: 'Interest 8' }).getAttribute('type')).toBe('checkbox');
    expect(screen.getByText('1 selected — a place to start.')).toBeTruthy();
  });

  const renderInterests = () => render(<OnboardingSurface categories={categories} onContinue={vi.fn()} onSkip={vi.fn()} phase="setup" />);

  it('heads step 1 with its kicker and a decorative progress hairline', () => {
    const { container } = renderInterests();
    expect(screen.getByText('Step 1 of 2 · Your taste')).toBeTruthy();
    expect(screen.getByRole('heading', { level: 1, name: 'What draws you in?' })).toBeTruthy();
    expect(document.querySelector('.g-onboarding-progress')?.getAttribute('aria-hidden')).toBe('true');
    expect(container.querySelector<HTMLImageElement>('.g-onboarding-interest-art img')?.getAttribute('src')).toBe('/onboarding/listening-room-v1.jpg');
  });

  it('interests are native checkboxes in a fieldset with a visible legend', async () => {
    renderInterests();
    const group = screen.getByRole('group', { name: 'Choose interests' });
    const chip = within(group).getAllByRole('checkbox')[0] as HTMLInputElement;
    expect(chip.checked).toBe(false);
    await userEvent.click(chip);
    expect(chip.checked).toBe(true);
    expect(screen.getByText('1 selected — a place to start.')).toBeTruthy();
  });

  it('preparing is a status with an indeterminate progress bar', () => {
    const { container } = render(<OnboardingSurface categories={categories} onContinue={vi.fn()} onSkip={vi.fn()} phase="preparing" />);
    expect(screen.getByRole('heading', { name: 'Preparing your Home' })).toBeTruthy();
    expect(screen.getByRole('progressbar').hasAttribute('aria-valuenow')).toBe(false);
    expect(screen.getByRole('status')).toBeTruthy();
    expect(container.querySelector<HTMLImageElement>('img.g-onboarding-vault-art')?.getAttribute('src')).toBe('/onboarding/vault-arrival-v2.jpg');
  });
});
