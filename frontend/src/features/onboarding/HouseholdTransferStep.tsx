import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowLeft, Check, CircleAlert, Server, Users } from '../../luminaIcons';

import { discoverHouseholdTransfer, getMediaServerSettings, listImports, listStorageRoots, updateMediaServerSettings } from '../../api';
import type { ImportRun, StorageRootRecord } from '../../api';
import type { HouseholdTransferMember, HouseholdTransferProvider, HouseholdTransferRequest, MediaServerSettings } from '../../types';
import { BrandMark, Button, Checkbox, Field, fieldProps, Input, PasswordInput, ProgressBar, Radio, Skeleton } from '../../ui';
import { errorMessage } from '../../utils';
import { CopyableLink } from '../admin/AdminMembers';
import { streamHouseholdTransfer } from './onboardingTransferApi';
import './vault-setup.css';

type Stage = 'choose' | 'connect' | 'select' | 'importing' | 'results' | 'retry';
type Busy = 'discover' | 'finish' | null;
const ACTIVE_SCAN_STATES = new Set(['queued', 'running', 'cancel_requested']);
const READY_SCAN_STATES = new Set(['succeeded', 'partial', 'needs_confirmation']);

const providerName = (provider: HouseholdTransferProvider | null) => provider === 'plex' ? 'Plex' : provider === 'jellyfin' ? 'Jellyfin' : '';
const addressKey = (provider: HouseholdTransferProvider) => provider === 'plex' ? 'plex_import_url' : 'jellyfin_import_url';

function mergeResult(current: HouseholdTransferMember[], incoming: HouseholdTransferMember) {
  const previous = current.find((member) => member.source_id === incoming.source_id);
  const merged = previous?.reset_url && !incoming.reset_url ? { ...incoming, reset_url: previous.reset_url, reset_expires_at: previous.reset_expires_at } : incoming;
  return [...current.filter((member) => member.source_id !== incoming.source_id), merged];
}

export function HouseholdTransferStep({ onBack, onFinish, error = null }: { onBack: () => void; onFinish: () => Promise<void>; error?: string | null }) {
  const alive = useRef(true);
  const request = useRef<AbortController | null>(null);
  const secretRef = useRef<HTMLInputElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const [stage, setStage] = useState<Stage>('choose');
  const [provider, setProvider] = useState<HouseholdTransferProvider | null>(null);
  const [settings, setSettings] = useState<MediaServerSettings | null>(null);
  const [roots, setRoots] = useState<StorageRootRecord[]>([]);
  const [runs, setRuns] = useState<ImportRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [readinessError, setReadinessError] = useState<string | null>(null);
  const [address, setAddress] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [token, setToken] = useState('');
  const [members, setMembers] = useState<HouseholdTransferMember[]>([]);
  const [chosen, setChosen] = useState<ReadonlySet<string>>(new Set());
  const [results, setResults] = useState<HouseholdTransferMember[]>([]);
  const [processed, setProcessed] = useState<ReadonlySet<string>>(new Set());
  const [progress, setProgress] = useState({ completed: 0, total: 0, name: '' });
  const [busy, setBusy] = useState<Busy>(null);
  const [message, setMessage] = useState<string | null>(null);

  const loadContext = useCallback(async () => {
    setLoading(true);
    setReadinessError(null);
    try {
      const [nextSettings, nextRoots, nextRuns] = await Promise.all([getMediaServerSettings(), listStorageRoots(), listImports()]);
      if (!alive.current) return;
      setSettings(nextSettings);
      setRoots(nextRoots.filter((root) => root.mode === 'external'));
      setRuns(nextRuns);
    } catch (failure) {
      if (alive.current) setReadinessError(errorMessage(failure, 'Lumina could not check your Library.'));
    } finally {
      if (alive.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    alive.current = true;
    void loadContext();
    return () => { alive.current = false; request.current?.abort(); };
  }, [loadContext]);

  useEffect(() => {
    headingRef.current?.focus({ preventScroll: true });
  }, [stage]);

  const enabledRoots = useMemo(() => roots.filter((root) => root.enabled), [roots]);
  const rootIds = useMemo(() => new Set(enabledRoots.map((root) => root.id)), [enabledRoots]);
  const relevantRuns = runs.filter((run, index) => rootIds.has(run.root_id) && runs.findIndex((candidate) => candidate.root_id === run.root_id) === index);
  const scansActive = relevantRuns.some((run) => ACTIVE_SCAN_STATES.has(run.state));
  const libraryReady = !scansActive && (enabledRoots.some((root) => root.artifact_count > 0) || relevantRuns.some((run) => READY_SCAN_STATES.has(run.state)));

  useEffect(() => {
    if (!scansActive) return;
    let live = true;
    let timer: number;
    const poll = () => {
      timer = window.setTimeout(() => {
        void listImports().then(async (nextRuns) => {
          if (!live || !alive.current) return;
          setRuns(nextRuns);
          const stillActive = nextRuns.some((run, index) => rootIds.has(run.root_id) && nextRuns.findIndex((candidate) => candidate.root_id === run.root_id) === index && ACTIVE_SCAN_STATES.has(run.state));
          if (!stillActive) {
            try {
              const nextRoots = await listStorageRoots();
              if (live && alive.current) setRoots(nextRoots.filter((root) => root.mode === 'external'));
            } catch { /* Terminal scan state is enough; the restored count may lag this response. */ }
          } else poll();
        }).catch(() => { if (live && alive.current) poll(); });
      }, 5000);
    };
    poll();
    return () => { live = false; window.clearTimeout(timer); };
  }, [scansActive, rootIds]);

  const clearSecrets = () => { setPassword(''); setToken(''); };
  const credentials = (): HouseholdTransferRequest => provider === 'plex' ? { token: token.trim() } : { username: username.trim(), password };
  const credentialsReady = provider === 'plex' ? Boolean(token.trim()) : Boolean(username.trim());
  const sourcePinned = results.length > 0;
  const retryIds = useMemo(() => [...chosen].filter((id) => !processed.has(id) || Boolean(results.find((member) => member.source_id === id)?.error)), [chosen, processed, results]);

  function choose(next: HouseholdTransferProvider) {
    setProvider(next);
    setAddress((settings?.[addressKey(next)] as string | null | undefined) ?? '');
    setMessage(null);
  }

  function abortAnd(action: () => void) {
    request.current?.abort();
    request.current = null;
    setBusy(null);
    clearSecrets();
    action();
  }

  async function finish() {
    abortAnd(() => undefined);
    setBusy('finish'); setMessage(null);
    try { await onFinish(); }
    catch (failure) { if (alive.current) setMessage(errorMessage(failure, 'Lumina could not finish setup.')); }
    finally { if (alive.current) setBusy(null); }
  }

  async function discover(event: FormEvent) {
    event.preventDefault();
    if (!provider || !settings) return;
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setBusy('discover'); setMessage(null);
    try {
      const key = addressKey(provider);
      let saved = settings;
      if (address.trim() !== (settings[key] ?? '')) saved = await updateMediaServerSettings({ [key]: address.trim() }, { signal: controller.signal });
      const found = await discoverHouseholdTransfer(provider, credentials(), controller.signal);
      if (!alive.current || controller.signal.aborted) return;
      setSettings(saved);
      setMembers(found.members);
      setChosen(new Set(found.members.filter((member) => member.action !== 'skip' && !member.disabled && !member.error).map((member) => member.source_id)));
      setProcessed(new Set()); setStage('select');
    } catch (failure) {
      if (!(failure instanceof DOMException && failure.name === 'AbortError') && alive.current && request.current === controller && !controller.signal.aborted) {
        clearSecrets();
        setMessage(errorMessage(failure, `Lumina could not connect to ${providerName(provider)}.`));
      }
    } finally {
      if (alive.current && request.current === controller) { request.current = null; setBusy(null); }
    }
  }

  function toggle(id: string) {
    setChosen((current) => { const next = new Set(current); if (!next.delete(id)) next.add(id); return next; });
  }

  async function importMembers(ids: string[]) {
    if (!provider || !ids.length || !libraryReady) return;
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setStage('importing'); setMessage(null); setProgress({ completed: 0, total: ids.length, name: '' });
    try {
      await streamHouseholdTransfer(provider, { ...credentials(), member_ids: ids }, (event) => {
        if (!alive.current || request.current !== controller || controller.signal.aborted) return;
        if (event.type === 'member') {
          setResults((current) => mergeResult(current, event.member));
          setProcessed((current) => new Set(current).add(event.member.source_id));
          setProgress({ completed: event.completed, total: event.total, name: event.member.source_name });
        } else if (event.type === 'progress') setProgress({ completed: event.completed, total: event.total, name: event.source_name });
        else if (event.type === 'complete') setProgress((current) => ({ ...current, completed: event.completed, total: event.total }));
      }, controller.signal);
      if (alive.current && request.current === controller && !controller.signal.aborted) setStage('results');
    } catch (failure) {
      if (!(failure instanceof DOMException && failure.name === 'AbortError') && alive.current && request.current === controller && !controller.signal.aborted) { setMessage(errorMessage(failure, 'The transfer stopped before it finished.')); setStage('results'); }
    } finally {
      if (alive.current && request.current === controller && !controller.signal.aborted) { clearSecrets(); request.current = null; }
    }
  }

  function stopImport() {
    abortAnd(() => {
      setMessage('Transfer stopped. The current person may still finish on the server. Re-enter credentials to retry unfinished people.');
      setStage('results');
    });
  }

  function rediscover() {
    clearSecrets();
    setMessage(null);
    setStage('connect');
  }

  const imported = results.filter((member) => !member.error && member.action !== 'skip').length;
  const created = members.filter((member) => member.action === 'create').length;
  const existing = members.filter((member) => member.action === 'import').length;

  const credentialFields = (retry = false) => provider ? (
    <div className="g-vault-fields">
      <Field hint={retry || sourcePinned ? 'Using the server where these people were found.' : undefined} label={`${providerName(provider)} server address`} required>{(ids) => <Input {...fieldProps(ids)} autoComplete="url" disabled={busy === 'discover'} inputMode="url" maxLength={2048} onChange={(event) => setAddress(event.target.value)} placeholder={provider === 'plex' ? 'http://192.168.1.20:32400' : 'http://192.168.1.20:8096'} readOnly={retry || sourcePinned} required spellCheck={false} type="url" value={address} />}</Field>
      {provider === 'plex' ? <Field hint={<a href="https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/" rel="noreferrer" target="_blank">How to find your Plex token</a>} label="Plex server owner token" required>{(ids) => <PasswordInput {...fieldProps(ids)} autoComplete="off" disabled={busy === 'discover'} maxLength={4096} onChange={(event) => setToken(event.target.value)} ref={secretRef} required value={token} />}</Field> : <><Field label="Jellyfin administrator username" required>{(ids) => <Input {...fieldProps(ids)} autoComplete="username" disabled={busy === 'discover'} maxLength={256} onChange={(event) => setUsername(event.target.value)} required spellCheck={false} value={username} />}</Field><Field hint="May be empty if this administrator has no password." label="Jellyfin administrator password">{(ids) => <PasswordInput {...fieldProps(ids)} autoComplete="current-password" disabled={busy === 'discover'} maxLength={1024} onChange={(event) => setPassword(event.target.value)} ref={secretRef} value={password} />}</Field></>}
    </div>
  ) : null;

  return (
    <main className="g-vault-setup g-transfer-setup">
      <div aria-hidden="true" className="g-vault-art"><img alt="" src="/onboarding/vault-arrival-v2.jpg" /></div>
      <div className="g-vault-workspace">
        <header className="g-vault-header"><BrandMark size="bar" /><p className="g-vault-kicker">Optional transfer</p><h1 ref={headingRef} tabIndex={-1}>Bring your household along.</h1><p>Move household accounts and viewing activity from your current media server. You can also do this later in Server Settings.</p></header>

        <section aria-labelledby="transfer-title" className="g-vault-folders g-transfer-content">
          {stage === 'choose' ? <><h2 id="transfer-title">Where are you moving from?</h2><fieldset className="g-vault-visibility g-transfer-providers" disabled={loading}><legend>Media server</legend><Radio checked={provider === 'plex'} hint="Household users and video progress." label="Plex" name="transfer-provider" onChange={() => choose('plex')} value="plex" /><Radio checked={provider === 'jellyfin'} hint="Household users, video progress and favorites." label="Jellyfin" name="transfer-provider" onChange={() => choose('jellyfin')} value="jellyfin" /></fieldset></> : null}

          {stage === 'connect' && provider ? <form className="g-transfer-connect" onSubmit={(event) => { void discover(event); }}><h2 id="transfer-title">Connect to {providerName(provider)}</h2><p>Credentials are used for this transfer and never saved. No source request is made until you choose Find people.</p>{credentialFields()}<Button busy={busy === 'discover'} disabled={!address.trim() || !credentialsReady} icon={<Server />} type="submit" variant="primary">Find people</Button></form> : null}

          {stage === 'select' ? <><div className="g-transfer-heading"><div><h2 id="transfer-title">Choose people to bring over</h2><p>{members.length} found · {created} new · {existing} already in Lumina · {chosen.size} selected</p></div></div><MemberList busy={false} chosen={chosen} members={members} onToggle={toggle} /></> : null}

          {stage === 'importing' ? <div aria-live="polite" className="g-transfer-progress"><h2 id="transfer-title">Bringing your household over</h2><p>{progress.name ? `Working on ${progress.name}. ` : ''}{progress.completed} of {progress.total} people complete.</p><ProgressBar label={`${progress.completed} of ${progress.total} people complete`} value={progress.total ? progress.completed / progress.total * 100 : null} />{results.length ? <ResultList members={results} provider={provider} /> : null}</div> : null}

          {(stage === 'results' || stage === 'retry') ? <div className="g-transfer-results"><h2 id="transfer-title">{message || results.some((member) => member.error) ? 'Transfer needs attention' : 'Household transfer complete'}</h2><p>{imported} {imported === 1 ? 'person' : 'people'} brought over. New members use the password links below; save them now or create new links later.</p>{results.length ? <ResultList members={results} provider={provider} /> : null}{stage === 'retry' ? <form className="g-transfer-retry" onSubmit={(event) => { event.preventDefault(); void importMembers(retryIds); }}>{credentialFields(true)}<Button disabled={!credentialsReady || !libraryReady} type="submit" variant="primary">Retry {retryIds.length} {retryIds.length === 1 ? 'person' : 'people'}</Button></form> : null}</div> : null}

          {loading ? <Skeleton count={2} label="Checking your Library…" shape="row" /> : null}
          {!loading && scansActive ? <div className="g-transfer-readiness" role="status"><ProgressBar label="Media scan underway" value={null} /><p>Lumina is still scanning your media. Choose people now; Bring over unlocks when the scan settles.</p></div> : null}
          {!loading && !readinessError && !scansActive && !libraryReady ? <p className="g-vault-prerequisite" role="status"><strong>Add media first.</strong> Go back and finish a media scan so viewing activity has titles to match, or skip this transfer for now.</p> : null}
          {readinessError ? <div className="g-transfer-context-error" role="alert"><p className="g-vault-error">{readinessError}</p><Button onClick={() => { void loadContext(); }} variant="secondary">Try again</Button></div> : null}
          {(message || error) ? <p className="g-vault-error" role="alert">{message || error}</p> : null}
        </section>

        <footer className="g-vault-actions">
          {stage === 'choose' ? <Button disabled={busy === 'finish'} icon={<ArrowLeft />} onClick={() => abortAnd(onBack)} variant="quiet">Back to media</Button> : null}
          {stage === 'connect' ? <Button onClick={() => abortAnd(() => { setStage(sourcePinned ? 'results' : 'choose'); setMessage(null); })} variant="quiet">Back</Button> : null}
          {stage === 'select' ? <Button onClick={() => { clearSecrets(); setStage('connect'); setMessage(null); window.setTimeout(() => secretRef.current?.focus(), 0); }} variant="quiet">Back</Button> : null}
          {stage === 'importing' ? <Button onClick={stopImport} variant="secondary">Stop transfer</Button> : null}
          {stage === 'results' ? <Button onClick={rediscover} variant="secondary">Find people again</Button> : null}
          {stage === 'results' && retryIds.length ? <Button onClick={() => { setStage('retry'); setMessage(null); window.setTimeout(() => secretRef.current?.focus(), 0); }} variant="secondary">Retry failed or unfinished</Button> : null}
          {stage === 'retry' ? <Button onClick={() => { clearSecrets(); setStage('results'); }} variant="quiet">Cancel retry</Button> : null}
          {stage !== 'importing' && stage !== 'results' && stage !== 'retry' ? <Button busy={busy === 'finish'} onClick={() => { void finish(); }} variant="quiet">Skip for now</Button> : null}
          {stage === 'choose' ? <Button disabled={!provider || loading || !settings} icon={<Users />} onClick={() => { setStage('connect'); setMessage(null); }} variant="primary">Continue</Button> : null}
          {stage === 'select' ? <Button disabled={!chosen.size || !libraryReady || loading} icon={<Users />} onClick={() => { void importMembers([...chosen]); }} variant="primary">Bring over {chosen.size} {chosen.size === 1 ? 'person' : 'people'}</Button> : null}
          {stage === 'results' ? <Button busy={busy === 'finish'} icon={<Check />} onClick={() => { void finish(); }} variant="primary">Finish setup</Button> : null}
        </footer>
      </div>
    </main>
  );
}

function MemberList({ members, chosen, busy, onToggle }: { members: HouseholdTransferMember[]; chosen: ReadonlySet<string>; busy: boolean; onToggle: (id: string) => void }) {
  return <ul aria-label="People found" className="g-vault-root-list g-transfer-people">{members.map((member) => <li className="g-vault-root" key={member.source_id}><Checkbox checked={chosen.has(member.source_id)} disabled={busy || member.disabled || member.action === 'skip' || Boolean(member.error)} label={`${member.source_name}${member.disabled ? ' (disabled)' : ''}`} onChange={() => onToggle(member.source_id)} /><span>{member.action === 'create' ? `New member ${member.lumina_username}` : member.action === 'import' ? `Import into ${member.lumina_username}` : `Skipped: ${member.reason ?? ''}`}</span>{member.error ? <small className="g-vault-scan-status is-error"><CircleAlert aria-hidden="true" />{member.error}</small> : null}</li>)}</ul>;
}

function ResultList({ members, provider }: { members: HouseholdTransferMember[]; provider: HouseholdTransferProvider | null }) {
  return <ul aria-label="Transfer results" className="g-vault-root-list g-transfer-people g-transfer-result-list">{members.map((member) => {
    const counts = member.summary ? [`${member.summary.watched} watched`, `${member.summary.in_progress} in progress`, ...(provider === 'jellyfin' ? [`${member.summary.favorites} favorites`] : []), `${member.summary.unmatched} unmatched`] : [];
    return <li className="g-vault-root" key={member.source_id}><strong>{member.source_name}</strong><span>{member.error ? 'Needs attention' : member.action === 'create' ? `Created ${member.lumina_username}` : member.action === 'import' ? `Imported into ${member.lumina_username}` : `Skipped: ${member.reason ?? ''}`}</span>{counts.length ? <small className="g-vault-scan-status">{counts.join(' · ')}</small> : null}{member.error ? <small className="g-vault-scan-status is-error"><CircleAlert aria-hidden="true" />{member.error}</small> : null}{member.reset_url ? <div className="g-vault-scan-status"><CopyableLink label={`Password link for ${member.lumina_username}`} url={member.reset_url} /></div> : null}</li>;
  })}</ul>;
}
