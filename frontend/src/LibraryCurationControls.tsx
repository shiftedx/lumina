import { useState, type FormEvent } from 'react';
import './libraryCuration.css';

export type CurationVisibility = 'private' | 'shared';

export interface CurationItemView {
  id: string;
  title: string;
  visibility: CurationVisibility;
  ownerDisplayName: string;
  ownedByCurrentMember: boolean;
}

export interface CurationTagView {
  id: string;
  label: string;
}

export interface LibraryCurationControlsProps {
  item: CurationItemView;
  canManageItem: boolean;
  tags: CurationTagView[];
  busy?: boolean;
  error?: string | null;
  onVisibilityChange: (visibility: CurationVisibility) => Promise<void>;
  onAddTag: (tag: string) => Promise<void>;
  onDeleteTag: (tagId: string) => Promise<void>;
}

export function LibraryCurationControls({
  item,
  canManageItem,
  tags,
  busy = false,
  error = null,
  onVisibilityChange,
  onAddTag,
  onDeleteTag,
}: LibraryCurationControlsProps) {
  const [tag, setTag] = useState('');
  const ownerName = item.ownedByCurrentMember ? 'You' : item.ownerDisplayName;
  const visibilityCopy = item.visibility === 'shared' ? 'shared with the household' : `private to ${ownerName}`;

  async function submitTag(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalized = tag.trim();
    if (!normalized) return;
    try {
      await onAddTag(normalized);
      setTag('');
    } catch { /* The parent keeps the error visible and the draft recoverable. */ }
  }

  return (
    <section aria-labelledby="library-curation-title" className="library-curation-controls">
      <header>
        <div>
          <h2 id="library-curation-title">About this Library item</h2>
          <p>{ownerName} {item.ownedByCurrentMember ? 'own' : 'owns'} this Library item. It is {visibilityCopy}.</p>
        </div>
        <label className="g-field settings-select">
          <span className="g-field-label">Who can see {item.title}</span>
          <select
            className="g-input"
            disabled={!canManageItem || busy}
            onChange={(event) => { void onVisibilityChange(event.target.value as CurationVisibility).catch(() => undefined); }}
            value={item.visibility}
          >
            <option value="private">Private</option>
            <option value="shared">Shared with household</option>
          </select>
        </label>
      </header>
      {!canManageItem ? <p className="settings-note">Only {item.ownerDisplayName} can change who sees it.</p> : null}
      {error ? <p className="auth-error" role="alert">{error}</p> : null}
      {busy ? <p aria-live="polite" role="status">Saving curation changes…</p> : null}

      <section aria-labelledby="private-tags-title" className="settings-card">
        <div><h3 id="private-tags-title">Your private tags</h3><p>Tags help your searches and stay visible only to you.</p></div>
        {tags.length ? <ul aria-label="Your tags">{tags.map((entry) => <li key={entry.id}><span>{entry.label}</span><button aria-label={`Delete ${entry.label} tag`} className="g-text-button" disabled={busy} onClick={() => { void onDeleteTag(entry.id).catch(() => undefined); }} type="button">Delete</button></li>)}</ul> : <p>No private tags yet.</p>}
        <form onSubmit={(event) => { void submitTag(event); }}>
          <label className="g-field settings-select"><span className="g-field-label">Add a private tag</span><input className="g-input" disabled={busy} onChange={(event) => setTag(event.target.value)} value={tag} /></label>
          <button className="g-button" disabled={busy || !tag.trim()} type="submit">Add tag</button>
        </form>
      </section>
    </section>
  );
}
