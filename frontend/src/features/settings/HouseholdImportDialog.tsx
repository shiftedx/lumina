import type { KeyboardEvent } from 'react';
import { createPortal } from 'react-dom';

import type { HouseholdTransferMember, JellyfinImportSummary } from '../../types';
import { Button, Checkbox, Dialog } from '../../ui';
import { CopyableLink } from '../admin/AdminMembers';

function counts(summary: JellyfinImportSummary, showFavorites: boolean): string {
  const favorites = showFavorites ? `, ${summary.favorites} new favorites` : '';
  return `${summary.watched} watched, ${summary.in_progress} in progress${favorites}, ${summary.up_to_date} already up to date, ${summary.unmatched} not in Lumina`;
}

function landing(member: HouseholdTransferMember, done: boolean): string {
  if (member.action === 'skip') return `Skipped: ${member.reason ?? ''}`;
  if (done && member.error) return 'Not brought over';
  return member.action === 'create' ? `Create member ${member.lumina_username}` : `Import into ${member.lumina_username}`;
}

export function HouseholdImportDialog({ provider, members, done, chosen, busy, error, onToggle, onBringOver, onClose }: {
  provider: 'Jellyfin' | 'Plex'; members: HouseholdTransferMember[]; done: boolean;
  chosen: ReadonlySet<string>; busy: boolean; error: string | null;
  onToggle: (id: string) => void; onBringOver: () => void; onClose: () => void;
}) {
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'GoBack' || event.key === 'BrowserBack') { event.preventDefault(); if (!busy) onClose(); }
  };
  // Keep the modal outside the settings pane and catch TV Back from its header and footer too.
  return createPortal(
    <div onKeyDown={onKeyDown} style={{ display: 'contents' }}>
      <Dialog
        busy={busy}
        footer={done ? <Button onClick={onClose} variant="primary">Done</Button> : (
          <>
            <Button busy={busy} disabled={!chosen.size} onClick={onBringOver} variant="primary">{busy ? 'Bringing members over…' : 'Bring members over'}</Button>
            <Button disabled={busy} onClick={onClose} variant="quiet">Cancel</Button>
          </>
        )}
        onClose={onClose}
        open
        size="md"
        title={done ? 'Members brought over' : `Bring members over from ${provider}?`}
      >
        <ul aria-label={`${provider} users`} className="g-list">
          {members.map((member) => (
            <li className="g-list-row" key={member.source_id}>
              {done ? <strong>{member.source_name}</strong> : (
                <Checkbox
                  checked={chosen.has(member.source_id)}
                  disabled={busy || member.action === 'skip' || (provider === 'Plex' && (member.disabled || Boolean(member.error)))}
                  label={`${member.source_name}${member.disabled ? ` (disabled in ${provider})` : ''}`}
                  onChange={() => onToggle(member.source_id)}
                />
              )}
              <span> {landing(member, done)}</span>
              {member.summary ? <small> · {counts(member.summary, provider === 'Jellyfin')}</small> : null}
              {member.error ? <p className="auth-error">{member.error}</p> : null}
              {member.reset_url ? <CopyableLink label={`Password link for ${member.lumina_username}`} url={member.reset_url} /> : null}
            </li>
          ))}
        </ul>
        <p>{done
          ? 'New members choose a password with their link before they can sign in. Each link works once, for 24 hours; Create reset link on their row makes a new one.'
          : 'New members join as household members without a password and get a one-time link to choose one. Nothing anyone watched more recently in Lumina changes.'}</p>
        {error ? <p className="auth-error" role="alert">{error}</p> : null}
      </Dialog>
    </div>,
    document.body,
  );
}
