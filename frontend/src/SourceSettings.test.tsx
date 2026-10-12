/* @vitest-environment jsdom */

import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { SourceSettings } from './SourceSettings';
import type { AcquisitionDefaults } from './sourcePreferences';

const defaults: AcquisitionDefaults = {
  formatPreset: 'best',
  outputContainer: 'mp4',
  downloadSubtitles: true,
  outputFolder: 'family/videos',
};

describe('SourceSettings', () => {
  it('edits the one global set of acquisition defaults', async () => {
    const browser = userEvent.setup();
    const onChange = vi.fn();
    render(<SourceSettings
      onChange={onChange}
      value={defaults}
    />);

    expect(screen.getByRole('region', { name: 'Acquisition defaults' })).not.toBeNull();
    expect(screen.queryByRole('checkbox', { name: /Use acquisition defaults for/ })).toBeNull();
    expect(screen.queryByText(/Quality for/)).toBeNull();
    // Nothing hides behind a disclosure: every choice is one row away.
    expect(document.querySelector('details')).toBeNull();
    expect(screen.getByRole('combobox', { name: 'Container' })).not.toBeNull();
    await browser.selectOptions(screen.getByRole('combobox', { name: 'Default quality' }), 'best_1080p');
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ formatPreset: 'best_1080p' }));
    await browser.selectOptions(screen.getByRole('combobox', { name: 'Default quality' }), 'Editable (H.264)');
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ formatPreset: 'best_editable' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Folder inside the Library' }), { target: { value: 'channels/mars' } });
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ outputFolder: 'channels/mars' }));

    const callsBeforeUnsafeFolder = onChange.mock.calls.length;
    fireEvent.change(screen.getByRole('textbox', { name: 'Folder inside the Library' }), { target: { value: '../private' } });
    expect(screen.getByRole('alert').textContent).toContain('inside the Library');
    expect(onChange).toHaveBeenCalledTimes(callsBeforeUnsafeFolder);
  });
});
