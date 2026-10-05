/**
 * The saved sign-in / credential surface is absent from the settings UI,
 * the api module, and the DTO type contract.
 *
 * The Settings surface renders zero sign-in/account controls (label absence;
 * the local session "Sign out" control survives), SourceSettings has no
 * auth-profile picker, api.ts exposes no auth-profile or live-chat functions
 * while the surviving surfaces stay, and the session/settings DTOs carry no
 * auth_profile_id / active_auth_profile_id at runtime — with a type-level
 * guard proving the fields are gone from the type (an
 * `@ts-expect-error` on a DIRECT object-literal assignment: an `as T` cast
 * would defeat the guard, so re-adding a field to the interface removes the
 * type error, leaves the directive unused, and fails `npm run typecheck`).
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import * as api from './api';
import { SettingsSurface } from './features/settings/SettingsSurface';
import { SourceSettings } from './SourceSettings';
import type { AcquisitionDefaults } from './sourcePreferences';
import type { FormatSelection, OutputProfile, SessionState, UserAutomationDefaults, UserDownloadDefaults, UserSettings, UserProfile } from './types';

const user: UserProfile = {
  id: 'member-1',
  username: 'alexandria',
  display_name: 'Alexandria',
  role: 'admin',
  is_active: true,
  onboarding_status: 'completed',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

const SIGN_IN_LABEL_PATTERNS = [/saved sign-in/i, /sign in with/i, /auth profile/i, /credential/i, /cookie file/i, /browser sign-in/i, /netrc/i] as const;

describe('settings surface: zero sign-in/account controls', () => {
  it('renders the Settings surface without any saved sign-in or account-credential control', () => {
    render(<SettingsSurface formatPreset="best_1080p" section="account" onFormatChange={vi.fn()} onLogout={vi.fn()} user={user} />);
    // The surface itself renders (positive control).
    expect(screen.getByRole('heading', { level: 1, name: /^Settings$/ })).toBeTruthy();
    // The surviving local session control stays.
    expect(screen.getByRole('button', { name: 'Sign out' })).toBeTruthy();
    // Label absence: no saved sign-in card or provider/credential control of any kind.
    // .g-info-note text is ⓘ explanation copy, not a surface label; the Password
    // row's verbatim ⓘ text legitimately mentions "sign in with" and stays hidden until opened.
    for (const pattern of SIGN_IN_LABEL_PATTERNS) {
      expect(screen.queryByText(pattern, { ignore: '.g-info-note' }), `label "${pattern}" must be absent`).toBeNull();
    }
    // Class absence: the removed Saved sign-ins card markup is gone.
    expect(document.querySelector('.saved-sign-ins-card')).toBeNull();
    // No auth-profile picker: nothing select-shaped is scoped to a sign-in label.
    expect(screen.queryByRole('combobox', { name: /sign-in|profile|credential|cookie|browser/i })).toBeNull();
  });
});

describe('SourceSettings: no auth-profile picker', () => {
  it('renders acquisition defaults only: no sign-in or profile selection control', () => {
    const value: AcquisitionDefaults = { formatPreset: 'best_1080p', outputContainer: 'mp4', downloadSubtitles: false, outputFolder: '' };
    render(<SourceSettings onChange={vi.fn()} value={value} />);
    // The download controls render (positive control).
    expect(screen.getByRole('region', { name: 'Acquisition defaults' })).toBeTruthy();
    expect(screen.getByText('Default quality')).toBeTruthy();
    // No sign-in surface anywhere in the component.
    for (const pattern of SIGN_IN_LABEL_PATTERNS) {
      expect(screen.queryByText(pattern), `label "${pattern}" must be absent`).toBeNull();
    }
    expect(screen.queryByRole('combobox', { name: /sign-in|profile|credential|cookie|browser/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /test selected sign-in|delete selected sign-in/i })).toBeNull();
  });
});

describe('removed api surface', () => {
  it('api.ts exposes no auth-profile or live-chat functions; the surviving surfaces stay', () => {
    const exported = Object.keys(api);
    const removed = [
      // Saved sign-ins (the credential surface is absent, not disabled).
      'listAuthProfiles',
      'createAuthProfile',
      'renameAuthProfile',
      'deleteAuthProfile',
      'uploadAuthProfileCookieFile',
      'replaceAuthProfileCookieFile',
      'validateAuthProfile',
      // The generic public-edge live-chat family.
      'openLiveChat',
      'pollLiveChat',
      'closeLiveChat',
    ];
    for (const name of removed) {
      expect(exported, `${name} must be removed from api.ts`).not.toContain(name);
    }
    // The surviving surfaces (local session, library, recording, chat-replay
    // read path, settings) are still there.
    for (const name of ['loginSession', 'logoutSession', 'getSession', 'previewUrl', 'getLibraryItem', 'createLiveRecording', 'stopLiveRecording', 'getChatReplay', 'loadChatReplay', 'getMySettings', 'updateMySettings']) {
      expect(exported, `${name} must survive in api.ts`).toContain(name);
    }
  });
});

describe('session/settings DTO: no credential fields', () => {
  it('the session DTO has no auth_profile_id at runtime and in the type contract', () => {
    const session: SessionState = { user };
    expect(Object.keys(session.user)).not.toContain('auth_profile_id');
    expect(Object.keys(session.user)).not.toContain('linked_identities');
    // Type-level guard: a direct object-literal assignment with the removed
    // key is a type error that @ts-expect-error consumes.
    // @ts-expect-error auth_profile_id was removed with the Saved sign-ins surface
    const withAuthProfile: UserProfile = { ...session.user, auth_profile_id: 'profile-1' };
    expect(withAuthProfile.username).toBe('alexandria');
  });

  it('the settings DTO has no auth_profile_id / active_auth_profile_id at runtime and in the type contract', () => {
    const formatSelection: FormatSelection = { preset: 'best_1080p', custom_format: null, extract_audio: false, audio_format: null, embed_thumbnail: true, embed_metadata: true, subtitles: true, output_container: 'mp4' };
    const outputProfile: OutputProfile = { base_path: null, subdir: '', template: '%(title)s.%(ext)s', organize_by: 'downloads' };
    const downloadDefaults: UserDownloadDefaults = { format_selection: formatSelection, output_profile: outputProfile };
    const automationDefaults: UserAutomationDefaults = { cron_expression: '0 */6 * * 1', auto_download: false, format_selection: formatSelection, output_profile: outputProfile, rules: { include_title: [], exclude_title: [], include_uploader: [], exclude_uploader: [], include_source: [], exclude_source: [], media_kind: 'video', min_duration: null, max_duration: null, max_age_days: null }, duplicate_policy: 'skip_same_source', max_items_per_run: 20, max_items_per_day: null, backfill_limit: 10 };
    const settings: UserSettings = {
      id: 'settings-1',
      user_id: user.id,
      download_defaults: downloadDefaults,
      automation_defaults: automationDefaults,
      ui_prefs: { sidebar_collapsed: false },
      notification_prefs: {},
      remote_playback_cache: { enabled: true, recent_video_limit: 5, storage_limit_mb: 2048 },
      resolved_download_defaults: downloadDefaults,
      resolved_automation_defaults: automationDefaults,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    };
    // Runtime key absence on every surface that once carried a credential field.
    expect(Object.keys(settings)).not.toContain('active_auth_profile_id');
    expect(Object.keys(settings.download_defaults)).not.toContain('auth_profile_id');
    expect(Object.keys(settings.automation_defaults)).not.toContain('auth_profile_id');
    // Type-level guard: direct object-literal assignment with the removed
    // top-level field is a type error that @ts-expect-error consumes.
    // @ts-expect-error active_auth_profile_id was removed with the Saved sign-ins surface
    const withActiveProfile: UserSettings = { ...settings, active_auth_profile_id: 'profile-1' };
    expect(withActiveProfile.user_id).toBe('member-1');
  });
});
