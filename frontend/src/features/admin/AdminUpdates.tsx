import { RefreshCw } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

import { checkAdminUpdates, getAdminUpdateStatus, updateAdminUpdateSettings } from '../../api';
import type { AdminUpdateStatus } from '../../types';
import { Button, ButtonLink, StatusText, type StatusTone } from '../../ui';
import { errorMessage, formatDateTime } from '../../utils';
import { SettingSwitch } from '../settings/SettingRow';
import { useAdminResource } from './useAdminResource';

const POLL_DELAY_MS = 1_000;
const POLL_LIMIT = 30;
const UPGRADE_GUIDE = 'https://github.com/shiftedx/lumina/blob/main/docker/README.md#upgrades';

type BusyAction = 'check' | 'automatic_checks' | 'email_notifications' | null;

/** Release links are supplied by the server, but only this repository's HTTPS release pages become clickable. */
export function trustedReleaseUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    const releasePath = /^\/shiftedx\/lumina\/releases\/tag\/v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)$/;
    return url.origin === 'https://github.com'
      && !url.username
      && !url.password
      && !url.search
      && !url.hash
      && releasePath.test(url.pathname)
      ? url.href
      : null;
  } catch {
    return null;
  }
}

function statusCopy(update: AdminUpdateStatus): { tone: StatusTone; text: string } {
  if (update.checking) return { tone: 'attention', text: 'Checking for the latest release…' };
  if (update.status === 'update_available' && update.latest_release) return { tone: 'attention', text: `Lumina ${update.latest_release.version} is available.` };
  if (update.status === 'up_to_date') return { tone: update.error ? 'muted' : 'ok', text: 'The last successful check found no newer release.' };
  if (update.error) return { tone: 'muted', text: 'No successful update check has completed yet.' };
  return { tone: 'muted', text: 'Lumina has not checked for updates yet.' };
}

export function AdminUpdates() {
  const resource = useAdminResource(getAdminUpdateStatus, 'Unable to load update status.');
  const { data, error, loading, setData } = resource;
  const [busy, setBusy] = useState<BusyAction>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [pollingExhausted, setPollingExhausted] = useState(false);
  const mounted = useRef(true);
  const revision = useRef(0);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; revision.current += 1; };
  }, []);

  useEffect(() => {
    if (!data?.checking || pollingExhausted) return undefined;
    const generation = revision.current;
    let active = true;
    let timer: number | undefined;
    let polls = 0;
    const arm = () => {
      timer = window.setTimeout(async () => {
        polls += 1;
        try {
          const next = await getAdminUpdateStatus();
          if (!active || generation !== revision.current) return;
          setData(next);
          setActionError(null);
          if (next.checking && polls < POLL_LIMIT) arm();
          else setPollingExhausted(next.checking);
        } catch (pollError) {
          if (!active || generation !== revision.current) return;
          setActionError(errorMessage(pollError, 'Unable to refresh update status.'));
          if (polls < POLL_LIMIT) arm();
          else setPollingExhausted(true);
        }
      }, POLL_DELAY_MS);
    };
    arm();
    return () => { active = false; if (timer !== undefined) window.clearTimeout(timer); };
  }, [data?.checking, pollingExhausted, setData]);

  async function check() {
    const generation = ++revision.current;
    setBusy('check');
    setActionError(null);
    try {
      const next = await checkAdminUpdates();
      if (mounted.current && generation === revision.current) {
        setPollingExhausted(false);
        setData(next);
      }
    } catch (checkError) {
      if (mounted.current && generation === revision.current) setActionError(errorMessage(checkError, 'Unable to check for updates.'));
    } finally {
      if (mounted.current && generation === revision.current) setBusy(null);
    }
  }

  async function refreshStatus() {
    const generation = ++revision.current;
    setBusy('check');
    setActionError(null);
    try {
      const next = await getAdminUpdateStatus();
      if (mounted.current && generation === revision.current) {
        setPollingExhausted(false);
        setData(next);
      }
    } catch (refreshError) {
      if (mounted.current && generation === revision.current) setActionError(errorMessage(refreshError, 'Unable to refresh update status.'));
    } finally {
      if (mounted.current && generation === revision.current) setBusy(null);
    }
  }

  async function change(key: 'automatic_checks' | 'email_notifications', value: boolean) {
    const generation = ++revision.current;
    setBusy(key);
    setActionError(null);
    try {
      const next = await updateAdminUpdateSettings({ [key]: value });
      if (mounted.current && generation === revision.current) setData(next);
    } catch (saveError) {
      if (mounted.current && generation === revision.current) setActionError(errorMessage(saveError, 'Unable to save update settings.'));
    } finally {
      if (mounted.current && generation === revision.current) setBusy(null);
    }
  }

  const state = data
    ? pollingExhausted && data.checking
      ? { tone: 'attention' as const, text: 'The update check is still running.' }
      : statusCopy(data)
    : null;
  const releaseUrl = trustedReleaseUrl(data?.latest_release?.url);
  const unavailableEmail = Boolean(data && !data.email_configured);
  const controlsBusy = busy !== null || Boolean(data?.checking);

  return (
    <div aria-busy={loading || data?.checking || undefined} className="admin-overview">
      <div className="admin-toolbar">
        <div role="status">{state ? <StatusText tone={state.tone}>{state.text}</StatusText> : loading ? 'Loading update status…' : 'Update status unavailable'}</div>
        <Button
          busy={busy === 'check' || Boolean(data?.checking && !pollingExhausted)}
          disabled={!data || busy !== null || Boolean(data?.checking && !pollingExhausted)}
          icon={<RefreshCw />}
          onClick={() => { void (data?.checking ? refreshStatus() : check()); }}
          variant="primary"
        >{data?.checking ? pollingExhausted ? 'Refresh status' : 'Checking…' : 'Check for updates'}</Button>
      </div>
      {error && !data ? <p className="auth-error" role="alert">{error}</p> : null}
      {actionError || (data?.error && data.error !== actionError) ? <div role="alert"><StatusText tone="danger">{actionError ?? data?.error}</StatusText></div> : null}

      {data ? <>
        <dl className="g-stat-strip admin-update-versions">
          <div className="g-stat"><dt className="g-label">Installed</dt><dd className="g-stat-figure">{data.current_version}</dd><dd className="g-stat-note">This server</dd></div>
          <div className="g-stat"><dt className="g-label">Latest release</dt><dd className="g-stat-figure">{data.latest_release?.version ?? '—'}</dd><dd className="g-stat-note">{data.latest_release?.published_at ? `Published ${formatDateTime(data.latest_release.published_at)}` : data.status === 'not_checked' ? 'Not checked yet' : 'No release details'}</dd></div>
        </dl>
        <p className="admin-note">{data.checked_at ? `Last checked ${formatDateTime(data.checked_at)}.` : 'No update check has completed yet.'} A check reads Lumina&apos;s public GitHub releases; it never installs an update or restarts this server.</p>
        {releaseUrl ? <div className="g-actions admin-update-release"><ButtonLink href={releaseUrl} rel="noreferrer noopener" target="_blank" variant="secondary">Read {data.latest_release?.version} release notes</ButtonLink></div> : null}

        <div className="g-subrows">
          <SettingSwitch busy={controlsBusy} checked={data.automatic_checks} hint="Checks once a day without installing anything." label="Check automatically each day" onChange={(value) => { void change('automatic_checks', value); }} />
          <SettingSwitch busy={controlsBusy} checked={data.email_notifications} disabled={unavailableEmail && !data.email_notifications} hint="Sends a best-effort email to configured vault-owner addresses once per release." label="Email vault owners about new releases" onChange={(value) => { void change('email_notifications', value); }} />
          {unavailableEmail ? <p className="admin-note is-control">Set up server email under Connections before enabling update emails.</p> : null}
        </div>

        <section aria-labelledby="admin-update-install-title" className="admin-panel admin-update-install">
          <h3 id="admin-update-install-title">Install an update</h3>
          <p className="admin-note">Read the release notes first, preserve a rollback image, then follow the operator guide for your installation. Lumina leaves installation and restart timing to the server owner.</p>
          <div className="g-actions"><ButtonLink href={UPGRADE_GUIDE} rel="noreferrer noopener" target="_blank" variant="secondary">Open upgrade guide</ButtonLink></div>
        </section>
      </> : null}
    </div>
  );
}
