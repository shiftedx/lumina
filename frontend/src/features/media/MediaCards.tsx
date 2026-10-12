import { type RefObject } from 'react';
import { Film } from 'lucide-react';
import { type YouTubeSearchResult } from '../../types';
import { type CollectionLoadProblem } from '../../workspace';
import { Button, clampServerText, TextButton } from '../../ui';

export function formatPublished(value: string | null | undefined): string | null {
  if (!value) return null;
  const normalized = /^\d{8}$/.test(value) ? `${value.slice(0, 4)}-${value.slice(4, 6)}-${value.slice(6, 8)}` : value;
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', year: date.getFullYear() === new Date().getFullYear() ? undefined : 'numeric' }).format(date);
}

export function itemKey(item: YouTubeSearchResult, index = 0): string {
  return item.id || item.webpage_url || `${item.title || 'video'}:${index}`;
}

export function EmptyShelf({ icon: Icon, title, body, action }: { icon: typeof Film; title: string; body: string; action?: { label: string; onClick: () => void } }) {
  return (
    <div className="empty-shelf">
      <span><Icon /></span><div><strong>{title}</strong><p>{body}</p></div>
      {action ? <button className="g-button" onClick={action.onClick} type="button">{action.label}</button> : null}
    </div>
  );
}

export function CollectionRecovery({ label, state, problem, retrying, onRetry, onSignIn, buttonRef }: {
  label: string;
  state: 'offline' | 'failed';
  problem: CollectionLoadProblem | null;
  retrying: boolean;
  onRetry: () => void;
  onSignIn: () => void;
  buttonRef?: RefObject<HTMLButtonElement | null>;
}) {
  const titleId = `${label}-recovery-title`;
  return (
    <section aria-labelledby={titleId} className={`collection-recovery collection-recovery-${state}`}>
      <div>
        <strong id={titleId}>{state === 'offline' ? `${label} is unavailable` : `${label} could not load`}</strong>
        <p>{clampServerText(problem?.message || 'Lumina could not load this collection. Your vault data has not been replaced.')}</p>
      </div>
      <Button busy={retrying} data-focus-item onClick={problem?.requiresSignIn ? onSignIn : onRetry} ref={buttonRef}>
        {retrying ? 'Trying again' : problem?.requiresSignIn ? 'Sign in again' : 'Try again'}
      </Button>
    </section>
  );
}

export function StaleCollectionNotice({ label, problem, retrying, onRetry, onSignIn, buttonRef }: {
  label: string;
  problem: CollectionLoadProblem | null;
  retrying: boolean;
  onRetry: () => void;
  onSignIn: () => void;
  buttonRef?: RefObject<HTMLButtonElement | null>;
}) {
  return (
    <div className="collection-stale">
      <div>
        <strong>{retrying ? `Updating ${label.toLowerCase()}…` : `Showing last-known ${label.toLowerCase()}`}</strong>
        <span>{clampServerText(problem?.message || 'The items below stay available while Lumina checks the vault again.')}</span>
      </div>
      <TextButton busy={retrying} data-focus-item onClick={problem?.requiresSignIn ? onSignIn : onRetry} ref={buttonRef}>
        {retrying ? 'Trying again' : problem?.requiresSignIn ? 'Sign in again' : 'Try again'}
      </TextButton>
    </div>
  );
}
