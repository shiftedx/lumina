import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { OnboardingSurface } from './features/onboarding/Onboarding';

const categories = [{ key: 'music', label: 'Music' }, { key: 'cooking', label: 'Cooking' }];

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
    expect(screen.getByRole('heading', { level: 1, name: 'Set up your Home' })).toBe(document.activeElement);
  });

  it('disables the controls while a decision is being saved', () => {
    render(<OnboardingSurface busy categories={categories} onContinue={vi.fn()} onSkip={vi.fn()} phase="setup" />);
    expect((screen.getByRole('button', { name: 'Continue' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: 'Skip for now' }) as HTMLButtonElement).disabled).toBe(true);
  });
});

describe('editorial onboarding', () => {
  const renderInterests = () => render(<OnboardingSurface categories={categories} onContinue={vi.fn()} onSkip={vi.fn()} phase="setup" />);

  it('heads step 1 with its kicker and a decorative progress hairline', () => {
    renderInterests();
    expect(screen.getByText('Step 1 of 2')).toBeTruthy();
    expect(screen.getByRole('heading', { level: 1, name: 'Set up your Home' })).toBeTruthy();
    expect(document.querySelector('.g-onboarding-progress')?.getAttribute('aria-hidden')).toBe('true');
  });

  it('interests are native checkboxes in a fieldset with a visible legend', async () => {
    renderInterests();
    const group = screen.getByRole('group', { name: 'Choose interests' });
    const chip = within(group).getAllByRole('checkbox')[0] as HTMLInputElement;
    expect(chip.checked).toBe(false);
    await userEvent.click(chip);
    expect(chip.checked).toBe(true);
  });

  it('preparing is a status with an indeterminate progress bar', () => {
    render(<OnboardingSurface categories={categories} onContinue={vi.fn()} onSkip={vi.fn()} phase="preparing" />);
    expect(screen.getByRole('heading', { name: 'Preparing your Home' })).toBeTruthy();
    expect(screen.getByRole('progressbar').hasAttribute('aria-valuenow')).toBe(false);
    expect(screen.getByRole('status')).toBeTruthy();
  });
});
