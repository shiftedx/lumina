import { type FormEvent, useCallback, useEffect, useRef, useState } from 'react';

import { formatDateTime } from '../../utils';
import { Button, ButtonLink, ConfirmDialog, Input, Select, StatusText } from '../../ui';
import {
  cancelPhotoSource, createPhotoSource, deletePhotoSource, listPhotoSources, scanPhotoSource,
  type PhotoSource, type PhotoSourcesPage, type PhotoVisibility,
} from './photoApi';

const ACTIVE = new Set<PhotoSource['state']>(['queued', 'scanning', 'canceling']);
const LABELS: Record<PhotoSource['state'], string> = {
  queued: 'Waiting', scanning: 'Scanning', idle: 'Up to date', canceling: 'Stopping', offline: 'Folder offline', failed: 'Needs attention',
};
const ERRORS: Record<string, string> = {
  root_offline: 'The storage root is offline. Reconnect it, then scan again.',
  permission_denied: 'Lumina cannot read this folder. Check its permissions.',
  invalid_image: 'Some images could not be read. Other photos remain available.',
  partial_scan: 'The scan finished, but some files could not be indexed. Valid photos remain available.',
};
const when = (value: string | null): string => value ? formatDateTime(value) : 'Never';

export function PhotoSources({ onChanged }: { onChanged: () => void }) {
  const [page, setPage] = useState<PhotoSourcesPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [rootId, setRootId] = useState('');
  const [folder, setFolder] = useState('');
  const [visibility, setVisibility] = useState<PhotoVisibility>('private');
  const [removing, setRemoving] = useState<PhotoSource | null>(null);
  const mounted = useRef(true);
  const polling = useRef(false);
  const completion = useRef<string | null>(null);
  const changed = useRef(onChanged);
  changed.current = onChanged;

  const load = useCallback(async (silent = false) => {
    if (polling.current) return;
    polling.current = true;
    if (!silent) setError(null);
    try {
      const next = await listPhotoSources();
      if (!mounted.current) return;
      const signature = next.items.map((source) => `${source.id}:${source.last_completed_at ?? ''}`).join('|');
      if (completion.current !== null && completion.current !== signature) changed.current();
      completion.current = signature;
      setPage(next);
      setRootId((current) => current || next.roots.find((root) => root.online)?.id || next.roots[0]?.id || '');
    } catch (failure) {
      if (mounted.current && !silent) setError(failure instanceof Error ? failure.message : 'Lumina could not load photo sources.');
    } finally { polling.current = false; }
  }, []);

  useEffect(() => {
    mounted.current = true;
    void load();
    return () => { mounted.current = false; };
  }, [load]);

  const active = page?.items.some((source) => ACTIVE.has(source.state)) ?? false;
  useEffect(() => {
    if (!active) return undefined;
    const timer = window.setInterval(() => { if (document.visibilityState === 'visible') void load(true); }, 3500);
    const visible = () => { if (document.visibilityState === 'visible') void load(true); };
    document.addEventListener('visibilitychange', visible);
    return () => { window.clearInterval(timer); document.removeEventListener('visibilitychange', visible); };
  }, [active, load]);

  const replace = (source: PhotoSource) => setPage((current) => current && ({ ...current, items: current.items.map((item) => item.id === source.id ? source : item) }));
  const run = async (name: string, action: () => Promise<PhotoSource>) => {
    setBusy(name); setError(null);
    try { replace(await action()); }
    catch (failure) { setError(failure instanceof Error ? failure.message : 'That photo source could not be updated.'); }
    finally { setBusy(null); }
  };
  const add = async (event: FormEvent) => {
    event.preventDefault();
    const root = page?.roots.find((item) => item.id === rootId);
    if (!root?.online) return;
    setBusy('add'); setError(null);
    try {
      const source = await createPhotoSource({ root_id: root.id, relative_folder: folder.trim(), visibility });
      setPage((current) => current && ({ ...current, items: [...current.items, source] }));
      setAdding(false); setFolder(''); setVisibility('private');
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'Lumina could not add that photo source.'); }
    finally { setBusy(null); }
  };
  const remove = async () => {
    if (!removing) return;
    const source = removing;
    setBusy(`delete:${source.id}`); setError(null);
    try {
      await deletePhotoSource(source.id);
      setPage((current) => current && ({ ...current, items: current.items.filter((item) => item.id !== source.id) }));
      setRemoving(null); changed.current();
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'Lumina could not remove that source.'); }
    finally { setBusy(null); }
  };

  if (!page && !error) return <p aria-busy="true" className="ph-source-note">Loading photo sources…</p>;
  if (!page) return <div><p className="ph-source-error" role="alert">{error}</p><Button onClick={() => void load()}>Try again</Button></div>;
  const selectedRoot = page.roots.find((root) => root.id === rootId);
  return (
    <section aria-labelledby="ph-sources-title" className="ph-sources">
      <div className="ph-source-head"><div><h3 id="ph-sources-title">Folders that stay in sync</h3><p>Lumina indexes photos in place. It never moves, edits, or deletes the original files.</p></div><Button onClick={() => setAdding((value) => !value)} variant="secondary">{adding ? 'Cancel' : 'Add source'}</Button></div>
      {adding ? page.roots.length ? <form className="ph-source-form" onSubmit={(event) => void add(event)}>
        <label>Registered storage root<Select aria-label="Photo storage root" onChange={(event) => setRootId(event.target.value)} value={rootId}>{page.roots.map((root) => <option disabled={!root.online} key={root.id} value={root.id}>{root.label}{root.online ? '' : ' · offline'}</option>)}</Select></label>
        <label>Folder inside that root <span>(optional)</span><Input onChange={(event) => setFolder(event.target.value)} placeholder="Family/Photos" value={folder} /></label>
        <label>Who can see imported photos<Select aria-label="Source visibility" onChange={(event) => setVisibility(event.target.value as PhotoVisibility)} value={visibility}><option value="private">Only me</option><option value="shared">Household</option></Select></label>
        <Button busy={busy === 'add'} disabled={!selectedRoot?.online || busy !== null} type="submit" variant="primary">Add and scan</Button>
      </form> : <div className="ph-source-empty"><p>No external storage roots are registered yet.</p><ButtonLink href="/manage/library" variant="secondary">Manage server storage</ButtonLink></div> : null}
      {page.items.length ? <ul className="ph-source-list">{page.items.map((source) => {
        const online = page.roots.find((root) => root.id === source.root_id)?.online ?? false;
        const activeSource = ACTIVE.has(source.state);
        return <li key={source.id}>
          <div className="ph-source-title"><strong>{source.root_label}{source.relative_folder ? ` / ${source.relative_folder}` : ''}</strong><StatusText tone={source.state === 'idle' ? 'ok' : source.state === 'failed' || source.state === 'offline' ? 'danger' : 'attention'}>{LABELS[source.state]}</StatusText></div>
          <p>{source.indexed_count.toLocaleString()} photos indexed · {source.visibility === 'shared' ? 'Household' : 'Only me'}</p>
          <p>Last scan started {when(source.last_scan_at)} · completed {when(source.last_completed_at)}</p>
          {source.error_code ? <p className="ph-source-error" role="alert">{ERRORS[source.error_code] ?? source.error_code.replaceAll('_', ' ')}{source.error_count ? ` ${source.error_count.toLocaleString()} file${source.error_count === 1 ? '' : 's'} need attention.` : ''}</p> : null}
          <div className="ph-source-actions">
            {activeSource ? <Button disabled={busy !== null || source.state === 'canceling'} onClick={() => void run(`cancel:${source.id}`, () => cancelPhotoSource(source.id))}>Cancel scan</Button> : <Button disabled={busy !== null || !online} onClick={() => void run(`scan:${source.id}`, () => scanPhotoSource(source.id))}>{source.state === 'failed' ? 'Retry scan' : 'Scan now'}</Button>}
            <Button disabled={busy !== null || activeSource} onClick={() => setRemoving(source)} variant="quiet">Remove source</Button>
          </div>
        </li>;
      })}</ul> : <div className="ph-source-empty"><p>No synced folders yet. Add a registered storage root to index an existing photo collection.</p></div>}
      <Button disabled={busy !== null} onClick={() => void load()} variant="quiet">Refresh status</Button>
      {error ? <p className="ph-source-error" role="alert">{error}</p> : null}
      <ConfirmDialog body={`Lumina will remove “${removing?.root_label ?? ''}” from photo sources and move its indexed photo records to Lumina Trash. Original files in the storage root are never deleted.`} busy={busy?.startsWith('delete:') ?? false} confirmLabel="Remove source" danger onCancel={() => setRemoving(null)} onConfirm={() => void remove()} open={removing !== null} title="Remove this photo source?" />
    </section>
  );
}
