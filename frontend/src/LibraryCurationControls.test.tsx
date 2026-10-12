import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { LibraryCurationControls } from './LibraryCurationControls';

const item = { id: 'item-1', title: 'Family film', visibility: 'shared' as const, ownerDisplayName: 'Sam', ownedByCurrentMember: false };

describe('LibraryCurationControls', () => {
  it('explains ownership and prevents a non-owner from changing item visibility', () => {
    render(
      <LibraryCurationControls
        canManageItem={false}
        item={item}
        onAddTag={vi.fn().mockResolvedValue(undefined)}
        onDeleteTag={vi.fn().mockResolvedValue(undefined)}
        onVisibilityChange={vi.fn().mockResolvedValue(undefined)}
        tags={[]}
      />,
    );

    expect(screen.getByText('Sam owns this Library item. It is shared with the household.')).not.toBeNull();
    expect(screen.getByText('Only Sam can change who sees it.')).not.toBeNull();
    expect(screen.getByRole('combobox', { name: 'Who can see Family film' }).hasAttribute('disabled')).toBe(true);
  });

  it('provides keyboard-friendly tag controls', async () => {
    const browser = userEvent.setup();
    const onAddTag = vi.fn().mockResolvedValue(undefined);
    render(
      <LibraryCurationControls
        canManageItem
        item={{ ...item, ownerDisplayName: 'You', ownedByCurrentMember: true }}
        onAddTag={onAddTag}
        onDeleteTag={vi.fn().mockResolvedValue(undefined)}
        onVisibilityChange={vi.fn().mockResolvedValue(undefined)}
        tags={[]}
      />,
    );

    await browser.type(screen.getByRole('textbox', { name: 'Add a private tag' }), 'family favorite');
    await browser.click(screen.getByRole('button', { name: 'Add tag' }));
    expect(onAddTag).toHaveBeenCalledWith('family favorite');
  });

  it('retains the tag draft when persistence fails', async () => {
    const browser = userEvent.setup();
    render(<LibraryCurationControls
      canManageItem
      error="Could not save changes"
      item={{ ...item, ownedByCurrentMember: true }}
      onAddTag={vi.fn().mockRejectedValue(new Error('failed'))}
      onDeleteTag={vi.fn().mockResolvedValue(undefined)}
      onVisibilityChange={vi.fn().mockResolvedValue(undefined)}
      tags={[]}
    />);
    await browser.type(screen.getByRole('textbox', { name: 'Add a private tag' }), 'favorite');
    await browser.click(screen.getByRole('button', { name: 'Add tag' }));
    expect((screen.getByRole('textbox', { name: 'Add a private tag' }) as HTMLInputElement).value).toBe('favorite');
    expect(screen.getByRole('alert').textContent).toContain('Could not save changes');
  });
});
