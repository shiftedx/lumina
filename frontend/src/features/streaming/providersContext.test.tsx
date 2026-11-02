import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { StreamingProvidersProvider, useStreamingProviders } from './providers';

it('suppresses providers while the server is off without discarding member choices', () => {
  const onChange = vi.fn();
  function Consumer() {
    const { providers, setEnabled } = useStreamingProviders();
    return <><span>{providers.join(',') || 'none'}</span><button onClick={() => void setEnabled('kick', true)} type="button">Enable Kick</button></>;
  }
  const view = render(<StreamingProvidersProvider enabled={['twitch']} onChange={onChange} serverEnabled={false}><Consumer /></StreamingProvidersProvider>);
  expect(screen.getByText('none')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Enable Kick' }));
  expect(onChange).toHaveBeenCalledWith(['twitch', 'kick']);
  view.rerender(<StreamingProvidersProvider enabled={['twitch']} onChange={onChange} serverEnabled><Consumer /></StreamingProvidersProvider>);
  expect(screen.getByText('youtube,twitch')).toBeTruthy();
});
