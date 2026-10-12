import { createContext, createElement, type ReactNode, useContext, useMemo } from 'react';

/** Persisted under the member-owned `ui_prefs` object, like `theater_mode`. */
export const STREAMING_PROVIDERS_UI_PREF_KEY = 'streaming_providers';

export type OptionalProvider = 'twitch' | 'kick';
export type StreamingProvider = 'youtube' | OptionalProvider;

const OPTIONAL: readonly OptionalProvider[] = ['twitch', 'kick'];
/** Twitch on, Kick off: what a member with no saved choice (or no loaded settings) sees. */
export const DEFAULT_OPTIONAL_PROVIDERS: readonly OptionalProvider[] = ['twitch'];

const asRecord = (uiPrefs: unknown): Record<string, unknown> =>
  uiPrefs && typeof uiPrefs === 'object' && !Array.isArray(uiPrefs) ? uiPrefs as Record<string, unknown> : {};

/** The optional providers a member turned on, or undefined when there is no usable saved choice. */
export function optionalProvidersFromUiPrefs(uiPrefs: unknown): OptionalProvider[] | undefined {
  const stored = asRecord(uiPrefs)[STREAMING_PROVIDERS_UI_PREF_KEY];
  if (!Array.isArray(stored)) return undefined;
  return OPTIONAL.filter((provider) => stored.includes(provider));
}

/** YouTube first, then the enabled optional providers; defaults when nothing valid is saved. */
export function resolveStreamingProviders(uiPrefs: unknown): StreamingProvider[] {
  return ['youtube', ...(optionalProvidersFromUiPrefs(uiPrefs) ?? DEFAULT_OPTIONAL_PROVIDERS)];
}

/** Adds the choice without discarding other current or future UI preferences. */
export function withStreamingProviders(uiPrefs: unknown, enabled: OptionalProvider[]): Record<string, unknown> {
  return { ...asRecord(uiPrefs), [STREAMING_PROVIDERS_UI_PREF_KEY]: OPTIONAL.filter((provider) => enabled.includes(provider)) };
}

/** Hiding only: sources other than Twitch and Kick (YouTube, SoundCloud, anything else) are never filtered. */
export const isProviderVisible = (providers: readonly StreamingProvider[], source: string | null | undefined): boolean =>
  !(OPTIONAL as readonly string[]).includes(source ?? '') || providers.includes(source as OptionalProvider);

type Value = { providers: StreamingProvider[]; setEnabled(p: OptionalProvider, on: boolean): Promise<void> };

const DEFAULTS: Value = { providers: ['youtube', ...DEFAULT_OPTIONAL_PROVIDERS], setEnabled: async () => undefined };
const Context = createContext<Value>(DEFAULTS);

/** Supplies the member's choice (the app shell owns persistence); without one, hooks read the defaults. */
export function StreamingProvidersProvider({ enabled, onChange, children }: {
  enabled: readonly OptionalProvider[];
  onChange: (next: OptionalProvider[]) => void;
  children: ReactNode;
}) {
  const value = useMemo<Value>(() => ({
    providers: ['youtube', ...enabled],
    setEnabled: async (provider, on) => {
      const without = enabled.filter((entry) => entry !== provider);
      onChange(OPTIONAL.filter((entry) => (entry === provider ? on : without.includes(entry))));
    },
  }), [enabled, onChange]);
  return createElement(Context.Provider, { value }, children);
}

export function useStreamingProviders(): Value {
  return useContext(Context);
}
