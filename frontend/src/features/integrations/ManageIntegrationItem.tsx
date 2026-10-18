import { useCallback, useEffect, useState } from 'react';

import { errorMessage, formatDateTime } from '../../utils';
import { Button, EmptyState, ErrorState, Field, Select, StatusText, fieldProps } from '../../ui';
import {
  type ArrControlledItem,
  type ArrObservedItem,
  type ArrOperation,
  type ArrOperationAction,
  type ArrOperationStatus,
  type ArrRelease,
  getArrControlledItem,
  getArrOperations,
  getArrReleases,
  postArrOperation,
  reconcileArrOperation,
} from './integrationsApi';

type SavedIntent = { operationId: string; action: ArrOperationAction; createdAt: string };
type TrackedOperation = Pick<ArrOperation, 'id' | 'action' | 'status' | 'error' | 'updated_at'>;
const reconcilable = new Set<ArrOperationStatus>(['pending', 'dispatching', 'uncertain']);
const operationLabel: Record<ArrOperationAction, string> = { monitor: 'Monitoring', profile: 'Quality profile', search: 'Search', rescan: 'Rescan', grab: 'Grab' };
const operationTone = (status: ArrOperationStatus) => status === 'succeeded' ? 'ok' : status === 'failed' || status === 'conflict' ? 'danger' : status === 'uncertain' ? 'attention' : 'muted';

function storageKey(serverId: string, upstreamId: number) { return `lumina.arr.operation.${serverId}.${upstreamId}`; }
function readIntent(serverId: string, upstreamId: number): SavedIntent | null {
  try {
    const raw = sessionStorage.getItem(storageKey(serverId, upstreamId));
    const value = raw ? JSON.parse(raw) as SavedIntent : null;
    return value?.operationId && value.action ? value : null;
  } catch { return null; }
}
function saveIntent(serverId: string, upstreamId: number, intent: SavedIntent | null) {
  try {
    const key = storageKey(serverId, upstreamId);
    if (intent) sessionStorage.setItem(key, JSON.stringify(intent)); else sessionStorage.removeItem(key);
  } catch { /* private browsing storage is optional; the server still records the operation. */ }
}
function operationId() {
  if (typeof globalThis.crypto?.randomUUID === 'function') return globalThis.crypto.randomUUID();
  return `00000000-0000-4000-8000-${Date.now().toString(16).padStart(12, '0').slice(-12)}`;
}
function toTracked(operation: ArrOperation): TrackedOperation { return operation; }
function isConflict(error: unknown) { return typeof error === 'object' && error !== null && 'status' in error && (error as { status?: number }).status === 409; }
function conflictSnapshot(error: unknown): TrackedOperation | null {
  const candidate = (error as { body?: { detail?: { operation?: unknown } } } | null)?.body?.detail?.operation;
  if (!candidate || typeof candidate !== 'object') return null;
  const operation = candidate as Partial<ArrOperation>;
  return typeof operation.id === 'string' && typeof operation.action === 'string' && typeof operation.status === 'string' && typeof operation.updated_at === 'string'
    ? operation as TrackedOperation : null;
}
function releaseSize(size: number | null) { return size === null ? null : `${(size / 1_000_000_000).toFixed(1)} GB`; }

function OperationStatus({ operation }: { operation: TrackedOperation }) {
  const needsReview = reconcilable.has(operation.status) || operation.status === 'conflict';
  return <p className="arr-operation-status" role={needsReview ? 'alert' : 'status'}>
    <StatusText tone={operationTone(operation.status)}>{operationLabel[operation.action]}: {operation.status}{operation.error ? ` — ${operation.error}` : ''}</StatusText>
    {reconcilable.has(operation.status) ? <span> Review the current upstream state before sending another change.</span> : operation.status === 'conflict' ? <span> Lumina refreshed this item. Review it, then choose a new action if needed.</span> : null}
  </p>;
}

function Releases({ serverId, upstreamId, onGrab, blocked }: { serverId: string; upstreamId: number; onGrab: (releaseId: string) => void; blocked: boolean }) {
  const [data, setData] = useState<ArrRelease[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    setError(null);
    try { setData((await getArrReleases(serverId, upstreamId)).items); } catch (cause) { setError(errorMessage(cause, 'Lumina could not load releases.')); }
  }, [serverId, upstreamId]);
  return <section className="arr-releases" aria-labelledby={`arr-releases-${upstreamId}`}>
    <div className="arr-manage-actions"><h5 id={`arr-releases-${upstreamId}`}>Available releases</h5>{!data ? <Button onClick={() => { void load(); }} variant="quiet">Review releases</Button> : null}</div>
    {error ? <ErrorState body={error} onRetry={() => { void load(); }} title="Releases are unavailable" /> : null}
    {data?.length ? <ul>{data.map((release) => <ReleaseRow blocked={blocked} key={release.release_id} release={release} onGrab={onGrab} />)}</ul> : data ? <EmptyState body="The integration did not return a release that Lumina can offer for a deliberate grab." title="No releases available" /> : null}
  </section>;
}

function ReleaseRow({ release, onGrab, blocked }: { release: ArrRelease; onGrab: (releaseId: string) => void; blocked: boolean }) {
  const rejected = !release.approved || release.rejections.length > 0;
  return <li>
    <div><strong>{release.title}</strong><small>{[release.quality, release.languages.join(', '), releaseSize(release.size)].filter(Boolean).join(' · ') || 'No quality details reported'}</small>{release.rejections.length ? <small>Not offered: {release.rejections.join('; ')}</small> : null}</div>
    {rejected ? <span className="arr-release-denied">Not available to grab</span> : <Button disabled={blocked} onClick={() => onGrab(release.release_id)} variant="secondary">Grab release</Button>}
  </li>;
}

/** This is deliberately scoped to one upstream record. It never proxies connection settings or filesystem operations. */
export function ManageIntegrationItem({ item, onClose, onOperation }: { item: ArrObservedItem; onClose: () => void; onOperation: () => void }) {
  const [controlled, setControlled] = useState<ArrControlledItem | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [tracked, setTracked] = useState<TrackedOperation | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [busy, setBusy] = useState<ArrOperationAction | null>(null);
  const [profileId, setProfileId] = useState('');
  const [season, setSeason] = useState('');
  const key = storageKey(item.server_id, item.upstream_id);

  const load = useCallback(async (restoreIntent = true) => {
    setLoadError(null);
    try {
      const next = await getArrControlledItem(item.server_id, item.upstream_id);
      setControlled(next);
      setProfileId(String(next.quality_profile_id));
      const saved = readIntent(item.server_id, item.upstream_id);
      if (!saved || !restoreIntent) return;
      try {
        const operations = await getArrOperations(item.server_id, { pageSize: 50 });
        const restored = operations.items.find((entry) => entry.id === saved.operationId && entry.upstream_id === item.upstream_id);
        if (restored) {
          setTracked(toTracked(restored));
          if (!reconcilable.has(restored.status)) saveIntent(item.server_id, item.upstream_id, null);
        } else {
          setTracked({ id: saved.operationId, action: saved.action, status: 'uncertain', error: 'Lumina has not confirmed this operation yet.', updated_at: saved.createdAt });
        }
      } catch { setTracked({ id: saved.operationId, action: saved.action, status: 'uncertain', error: 'Lumina has not confirmed this operation yet.', updated_at: saved.createdAt }); }
    } catch (cause) { setLoadError(errorMessage(cause, 'Lumina could not load controls for this item.')); }
  }, [item.server_id, item.upstream_id]);
  useEffect(() => { setControlled(null); setTracked(null); setSubmitError(null); void load(); }, [key, load]);

  const blocked = Boolean(tracked && reconcilable.has(tracked.status)) || busy !== null;
  async function act(action: ArrOperationAction, extra: Partial<{ monitored: boolean; season_number: number; profile_id: number; release_id: string }> = {}) {
    if (!controlled || blocked) return;
    const intent = { operationId: operationId(), action, createdAt: new Date().toISOString() };
    saveIntent(item.server_id, item.upstream_id, intent);
    setTracked({ id: intent.operationId, action, status: 'pending', error: null, updated_at: intent.createdAt });
    setSubmitError(null); setBusy(action);
    try {
      const result = await postArrOperation(item.server_id, item.upstream_id, { operation_id: intent.operationId, base_revision: controlled.revision, action, ...extra });
      setTracked(toTracked(result));
      if (!reconcilable.has(result.status)) saveIntent(item.server_id, item.upstream_id, null);
      onOperation();
      if (result.status === 'conflict') void load(false);
    } catch (cause) {
      if (isConflict(cause)) {
        setTracked(conflictSnapshot(cause) ?? { id: intent.operationId, action, status: 'conflict', error: 'This item changed upstream. Lumina loaded its current state; review it before acting again.', updated_at: new Date().toISOString() });
        saveIntent(item.server_id, item.upstream_id, null);
        void load(false);
      } else {
        setTracked({ id: intent.operationId, action, status: 'uncertain', error: 'Lumina could not confirm the result. Reconcile this operation before trying another change.', updated_at: new Date().toISOString() });
        setSubmitError(errorMessage(cause, 'Lumina could not confirm the result.'));
      }
    } finally { setBusy(null); }
  }
  async function reconcile() {
    if (!tracked) return;
    setBusy('search'); setSubmitError(null);
    try {
      const result = await reconcileArrOperation(tracked.id);
      setTracked(toTracked(result));
      if (!reconcilable.has(result.status)) saveIntent(item.server_id, item.upstream_id, null);
      onOperation(); void load();
    } catch (cause) { setSubmitError(errorMessage(cause, 'Lumina could not reconcile this operation.')); } finally { setBusy(null); }
  }

  return <section aria-labelledby={`arr-manage-${item.upstream_id}`} className="arr-manage-item">
    <header><div><h4 id={`arr-manage-${item.upstream_id}`}>Manage {item.title}</h4><p>These controls change this upstream title only. Lumina never moves, deletes, renames, or transfers media here.</p></div><Button onClick={onClose} variant="quiet">Close</Button></header>
    {loadError ? <ErrorState body={loadError} onRetry={() => { void load(); }} title="Controls unavailable" /> : !controlled ? <p className="admin-note" role="status">Loading current upstream state…</p> : <>
      <dl className="arr-quality"><div><dt>Current</dt><dd>{controlled.quality.current || 'Not reported'}</dd></div><div><dt>Target</dt><dd>{controlled.quality.target || 'Not reported'}</dd></div><div><dt>Cutoff</dt><dd>{controlled.quality.cutoff || 'Not reported'}</dd></div></dl>
      <div className="arr-manage-actions"><Button busy={busy === 'monitor'} disabled={blocked} onClick={() => { void act('monitor', { monitored: !controlled.monitored }); }} variant="secondary">{controlled.monitored ? 'Unmonitor title' : 'Monitor title'}</Button><Button busy={busy === 'search'} disabled={blocked} onClick={() => { void act('search'); }} variant="quiet">Search</Button><Button busy={busy === 'rescan'} disabled={blocked} onClick={() => { void act('rescan'); }} variant="quiet">Rescan</Button></div>
      <div className="arr-manage-fields">
        {controlled.profiles.length ? <Field hint="Choose one of the profiles already configured in this integration." label="Quality profile">{(ids) => <Select {...fieldProps(ids)} onChange={(event) => setProfileId(event.target.value)} value={profileId}><option value="">Select a profile</option>{controlled.profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.name}</option>)}</Select>}</Field> : null}
        {controlled.profiles.length ? <Button disabled={blocked || !profileId || Number(profileId) === controlled.quality_profile_id} onClick={() => { void act('profile', { profile_id: Number(profileId) }); }} variant="secondary">Apply quality profile</Button> : null}
        {controlled.seasons.length ? <><Field label="Season monitoring">{(ids) => <Select {...fieldProps(ids)} onChange={(event) => setSeason(event.target.value)} value={season}><option value="">Choose a season</option>{controlled.seasons.map((entry) => <option key={entry.season_number} value={entry.season_number}>Season {entry.season_number} · {entry.monitored ? 'monitored' : 'not monitored'}</option>)}</Select>}</Field><Button disabled={blocked || !season} onClick={() => { const current = controlled.seasons.find((entry) => entry.season_number === Number(season)); void act('monitor', { season_number: Number(season), monitored: !current?.monitored }); }} variant="quiet">Toggle season monitoring</Button></> : null}
      </div>
      {tracked ? <><OperationStatus operation={tracked} />{reconcilable.has(tracked.status) ? <Button busy={busy === 'search'} onClick={() => { void reconcile(); }} variant="quiet">Reconcile operation</Button> : null}</> : null}
      {submitError ? <p role="alert"><StatusText tone="attention">{submitError}</StatusText></p> : null}
      <Releases blocked={blocked} onGrab={(releaseId) => { void act('grab', { release_id: releaseId }); }} serverId={item.server_id} upstreamId={item.upstream_id} />
    </>}
  </section>;
}

export function OperationAudit({ serverId, revision }: { serverId: string; revision: number }) {
  const [data, setData] = useState<ArrOperation[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => { setError(null); try { setData((await getArrOperations(serverId, { pageSize: 25 })).items); } catch (cause) { setError(errorMessage(cause, 'Lumina could not load operation history.')); } }, [serverId]);
  useEffect(() => { setData(null); void load(); }, [load, revision]);
  return <section aria-labelledby="arr-operation-audit" className="arr-operation-audit"><h3 id="arr-operation-audit">Recent integration operations</h3>{error ? <ErrorState body={error} onRetry={() => { void load(); }} title="Operation history unavailable" /> : !data ? <p className="admin-note" role="status">Loading operation history…</p> : !data.length ? <EmptyState body="Actions you take for this integration are recorded here." title="No integration operations" /> : <ul>{data.map((operation) => <li key={operation.id}><div><strong>{operationLabel[operation.action]}</strong><small>{formatDateTime(operation.updated_at)}</small></div><StatusText tone={operationTone(operation.status)}>{operation.status}{operation.error ? ` · ${operation.error}` : ''}</StatusText></li>)}</ul>}</section>;
}
