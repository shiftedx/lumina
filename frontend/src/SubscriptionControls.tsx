import { useEffect, useRef, useState, type KeyboardEvent } from 'react';

import { useCanDownload } from './features/access/access';
import {
  channelSubscriptionStatus,
  isKickAddress,
  type ChannelSubscriptionCommands,
  type UnfollowConfirmation,
} from './channelSubscriptions';
import { acquisitionFormatOptions } from './mediaAcquisition';
import type { SourceAutomation } from './types';

/** What turning automatic saving on does, before the member opts in: quality and limits. */
export function automaticSavingSummary(automation: SourceAutomation): string {
  const preset = automation.format_selection?.preset;
  const quality = acquisitionFormatOptions.find(([value]) => value === preset)?.[1] || 'Best available';
  const perCheck = automation.max_items_per_run ? `up to ${automation.max_items_per_run} per check` : 'every new video';
  const perDay = automation.max_items_per_day ? `, ${automation.max_items_per_day} per day` : '';
  return `${automation.auto_download ? 'Saving' : 'When on, saves'} ${quality}, ${perCheck}${perDay}. Each video is queued at most once and counts toward your download queue limit.`;
}

export function SubscriptionControls({
  automation,
  commands,
  onChange,
  onRecover,
  onRemoved,
}: {
  automation: SourceAutomation;
  commands: ChannelSubscriptionCommands;
  onChange: (automation: SourceAutomation) => void;
  onRecover: (automation: SourceAutomation) => Promise<void>;
  onRemoved: (automationId: string) => void;
}) {
  const canDownload = useCanDownload();
  const [busy, setBusy] = useState<'lifecycle' | 'automatic' | 'recover' | 'unfollow' | null>(null);
  const [confirmation, setConfirmation] = useState<UnfollowConfirmation | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const confirmationRef = useRef<HTMLElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const unfollowTriggerRef = useRef<HTMLButtonElement>(null);
  const restoreAfterCloseRef = useRef(false);
  const removeAfterCloseRef = useRef(false);
  const status = channelSubscriptionStatus(automation);
  const kick = isKickAddress(automation.source_url);
  const titleId = `subscription-${automation.id}-title`;
  const confirmationTitleId = `subscription-${automation.id}-unfollow-title`;
  const confirmationDescriptionId = `subscription-${automation.id}-unfollow-description`;

  useEffect(() => {
    if (confirmation) cancelRef.current?.focus();
    if (!confirmation && restoreAfterCloseRef.current) {
      restoreAfterCloseRef.current = false;
      if (removeAfterCloseRef.current) {
        removeAfterCloseRef.current = false;
        onRemoved(automation.id);
      } else {
        unfollowTriggerRef.current?.focus();
      }
    }
  }, [automation.id, confirmation, onRemoved]);

  async function update(action: () => Promise<SourceAutomation>, kind: 'lifecycle' | 'automatic') {
    setBusy(kind);
    setActionError(null);
    try {
      onChange(await action());
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') return;
      setActionError(error instanceof Error ? error.message : 'This channel could not be updated.');
    } finally {
      setBusy(null);
    }
  }

  async function confirmUnfollow() {
    if (!confirmation) return;
    setBusy('unfollow');
    setActionError(null);
    try {
      await commands.confirmUnfollow(automation, confirmation);
      restoreAfterCloseRef.current = true;
      removeAfterCloseRef.current = true;
      setConfirmation(null);
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') return;
      setActionError(error instanceof Error ? error.message : 'This channel could not be unfollowed.');
      restoreAfterCloseRef.current = true;
      setConfirmation(null);
    } finally {
      setBusy(null);
    }
  }

  function closeConfirmation() {
    restoreAfterCloseRef.current = true;
    setConfirmation(null);
  }

  function handleConfirmationKeyDown(event: KeyboardEvent<HTMLElement>) {
    if (event.key === 'Escape' && busy === null) {
      event.preventDefault();
      closeConfirmation();
      return;
    }
    if (event.key !== 'Tab') return;
    const first = cancelRef.current;
    const last = confirmRef.current;
    if (!first || !last) return;
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  async function recover(force = false) {
    if (!force && !status.recovery) return;
    setBusy('recover');
    setActionError(null);
    try {
      await onRecover(automation);
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') return;
      setActionError(error instanceof Error ? error.message : 'This channel could not be checked again.');
    } finally {
      setBusy(null);
    }
  }

  return (
    <article aria-labelledby={titleId} className="subscription-control">
      <header className="subscription-control-header">
        <p className="eyebrow">Follow settings</p>
        <h2 id={titleId}>{automation.label}</h2>
        <p aria-live="polite" className={`subscription-control-state subscription-control-state-${status.state}`}>{status.label}</p>
      </header>

      <div className="subscription-control-actions">
        <button
          aria-label={`Check ${automation.label} now`}
          className="g-button"
          disabled={busy !== null}
          onClick={() => void recover(true)}
          type="button"
        >
          {busy === 'recover' ? 'Checking…' : 'Check now'}
        </button>
        <button
          aria-label={`${automation.active ? 'Pause checks for' : 'Resume checks for'} ${automation.label}`}
          className="g-button"
          disabled={busy !== null}
          onClick={() => void update(
            () => automation.active ? commands.pause(automation) : commands.resume(automation),
            'lifecycle',
          )}
          type="button"
        >
          {automation.active ? 'Pause' : 'Resume'}
        </button>
      </div>

      {status.error ? (
        <div className="subscription-control-failure" role="alert">
          <strong>{status.label}</strong>
          <p>{status.error}</p>
          {status.recovery ? (
            <button className="g-button" disabled={busy !== null} onClick={() => void recover()} type="button">
              {busy === 'recover' ? 'Checking…' : 'Try check again'}
            </button>
          ) : null}
        </div>
      ) : null}

      {canDownload ? <>
      <label className="subscription-control-automatic">
        <input
          checked={automation.auto_download}
          disabled={busy !== null || (kick && !automation.auto_download)}
          onChange={(event) => void update(
            () => commands.setAutomaticAcquisition(automation, event.currentTarget.checked),
            'automatic',
          )}
          type="checkbox"
        />
        <span>Download new videos automatically</span>
      </label>
      <p className="subscription-control-note">{kick && !automation.auto_download ? 'Kick downloads are not supported yet. Following still shows when this channel is live.' : automation.auto_download ? 'New matching videos go to your download queue.' : 'New videos stay in Subscriptions until you choose to download them.'}</p>
      {kick ? null : <p className="subscription-auto-details">{automaticSavingSummary(automation)}</p>}
      {automation.auto_download ? <p className="subscription-auto-details">Automatic downloads use the choices captured when you followed this channel. Later changes to your acquisition defaults do not update this Source automation.</p> : null}
      </> : null}

      <details className="subscription-control-details">
        <summary>More automation options</summary>
        <dl>
          <div><dt>Check schedule</dt><dd>{automation.cron_expression}</dd></div>
          <div><dt>Source address</dt><dd><code>{automation.source_url}</code></dd></div>
          <div>
            <dt>Last result</dt>
            <dd>{status.summary.discovered} found · {status.summary.queued} queued · {status.summary.failed} failed</dd>
          </div>
        </dl>
      </details>

      <button
        aria-label={`Unfollow ${automation.label}`}
        className="g-button is-quiet subscription-control-unfollow"
        disabled={busy !== null}
        onClick={() => setConfirmation(commands.prepareUnfollow(automation))}
        ref={unfollowTriggerRef}
        type="button"
      >
        Unfollow
      </button>

      {confirmation ? (
        <section
          aria-describedby={confirmationDescriptionId}
          aria-modal="true"
          aria-labelledby={confirmationTitleId}
          className="subscription-control-confirmation"
          ref={confirmationRef}
          role="alertdialog"
          onKeyDown={handleConfirmationKeyDown}
        >
          <h3 id={confirmationTitleId}>Unfollow {automation.label}?</h3>
          <p id={confirmationDescriptionId}>Lumina stops checking this channel and saves nothing new from it. Videos already in your library stay; this channel’s check history is removed. Nothing changes on the platform itself.</p>
          <button className="g-button" disabled={busy !== null} onClick={closeConfirmation} ref={cancelRef} type="button">Keep following</button>
          <button
            aria-label={`Confirm unfollow ${automation.label}`}
            className="g-button is-danger subscription-control-danger"
            disabled={busy !== null}
            onClick={() => void confirmUnfollow()}
            ref={confirmRef}
            type="button"
          >
            {busy === 'unfollow' ? 'Unfollowing…' : 'Confirm unfollow'}
          </button>
        </section>
      ) : null}
      {actionError ? <p className="subscription-control-error" role="alert">{actionError}</p> : null}
    </article>
  );
}
