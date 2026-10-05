/** My requests (with a status timeline, polling while anything is moving) and Manage, the admin queue. */
import { type ReactNode, useState } from 'react';

import { usePolledSnapshot } from '../../app/usePolledSnapshot';
import { Button, Dialog, EmptyState, ErrorState, Field, fieldProps, Skeleton, Textarea, useToast } from '../../ui';
import {
  type Amend, approveRequest, cancelRequest, declineRequest, errorCode, getCatalogTitle, type Language, listRequests, type MediaRequest,
  type RequestsPage, type RequestState, retryRequest,
} from './requestsApi';
import { useLoad, useRequests } from './requestsContext';
import { catalogId, errorCopy, isActive, KIND_LABEL, type SeasonMode, seasonsPayload, stateLabel, timeline } from './requestsModel';
import { Chips } from './Browse';
import { Art } from './parts';
import { LanguageChoice, SeasonPicker } from './RequestSheet';

const POLL_MS = 15_000;

const seasonsText = (seasons: MediaRequest['seasons']) => (seasons === 'all' ? 'All seasons' : seasons?.length ? `Season${seasons.length > 1 ? 's' : ''} ${seasons.join(', ')}` : null);
const languageText = (language: Language | null) => (language === 'dub' ? 'English dub' : language === 'sub' ? 'Japanese + subtitles' : null);

export function Timeline({ request }: { request: Pick<MediaRequest, 'status' | 'progress'> }) {
  return (
    <ol aria-label="Progress" className="rq-timeline">
      {timeline(request).map((step) => <li aria-current={step.state === 'current' ? 'step' : undefined} className={`is-${step.state}`} key={step.label}>{step.label}</li>)}
    </ol>
  );
}

function RequestRow({ request, showRequester, children }: { request: MediaRequest; showRequester?: boolean; children?: ReactNode }) {
  const { openLibrary, go } = useRequests();
  const id = catalogId(request);
  const open = () => (request.status === 'available' && request.library_title_id ? openLibrary(request.library_title_id) : id ? go({ surface: 'requests', view: 'title', kind: request.kind, id }) : undefined);
  const details = [request.year, KIND_LABEL[request.kind], seasonsText(request.seasons), languageText(request.language)].filter(Boolean).join(' · ');
  const people = [showRequester ? `Asked by ${request.requested_by.name}` : null, request.followers.length ? `also wanted by ${request.followers.map((member) => member.name).join(', ')}` : null].filter(Boolean).join(', ');
  return (
    <li className="rq-request">
      <button aria-label={`${request.title}, ${stateLabel(request.status)}`} className="rq-frame rq-request-poster" data-focus-item onClick={open} type="button"><Art fallback={request.title} src={request.poster_url} /></button>
      <div className="rq-request-copy">
        <p className="rq-request-title">{request.title}</p>
        <p className="rq-muted">{details}</p>
        {people ? <p className="rq-muted">{people}</p> : null}
        <Timeline request={request} />
        {request.decline_reason ? <p className="rq-muted">“{request.decline_reason}”{request.decided_by ? ` — ${request.decided_by.name}` : ''}</p> : null}
        {request.failure_reason ? <p className="rq-muted">{request.failure_reason}</p> : null}
        {children ? <div className="rq-request-actions" data-focus-row>{children}</div> : null}
      </div>
    </li>
  );
}

/** Loads a request list, polling while any request is still moving; `bump` reloads at once. */
function usePolledRequests(key: string | null, params: Parameters<typeof listRequests>[0]) {
  const { session } = useRequests();
  const [page, setPage] = useState<RequestsPage | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [generation, setGeneration] = useState(0);
  usePolledSnapshot(key === null ? null : `${key}:${generation}`, session, (isCurrent) => listRequests(params).then((next) => {
    if (!isCurrent()) return true;
    setPage(next); setError(null);
    return !next.items.some((request) => isActive(request.status));
  }, (failure) => { if (isCurrent()) setError(failure); return false; }), POLL_MS);
  return { page, error, reload: () => setGeneration((value) => value + 1) };
}

export function MyRequests() {
  const { user } = useRequests();
  const toast = useToast();
  const { page, error, reload } = usePolledRequests(`mine:${user.id}`, { scope: 'mine' });
  const [busy, setBusy] = useState<string | null>(null);
  if (error && !page) return <ErrorState onRetry={reload} title="Lumina couldn't load your requests." />;
  if (!page) return <Skeleton count={3} label="Loading your requests" shape="row" />;
  if (!page.items.length) return <EmptyState body="Find something in Discover and press Request. It'll show up here as it makes its way into your vault." size="page" title="No requests yet" />;
  const cancel = async (request: MediaRequest) => {
    setBusy(request.id);
    try { await cancelRequest(request.id); toast({ tone: 'success', message: `Cancelled ${request.title}.` }); reload(); } catch (failure) { toast({ tone: 'error', message: errorCopy(errorCode(failure), "Lumina couldn't cancel that request.") }); } finally { setBusy(null); }
  };
  return (
    <ul className="rq-requests">
      {page.items.map((request) => (
        <RequestRow key={request.id} request={request}>
          {request.status === 'pending' && request.requested_by.id === user.id ? <Button busy={busy === request.id} data-focus-item onClick={() => void cancel(request)} variant="quiet">Cancel request</Button> : null}
        </RequestRow>
      ))}
    </ul>
  );
}

function AmendDialog({ request, onClose, onApproved }: { request: MediaRequest; onClose: () => void; onApproved: () => void }) {
  const toast = useToast();
  const id = catalogId(request);
  const series = request.media_type === 'tv';
  const detail = useLoad(series && id ? `${request.kind}:${id}` : '', (signal) => (series && id ? getCatalogTitle(request.kind, id, signal) : Promise.resolve(null)));
  const [mode, setMode] = useState<SeasonMode>(request.seasons === 'all' ? 'all' : 'pick');
  const [picked, setPicked] = useState<number[]>(Array.isArray(request.seasons) ? request.seasons : []);
  const [language, setLanguage] = useState<Language | null>(request.language);
  const [busy, setBusy] = useState(false);
  const seasons = seasonsPayload(mode, picked, detail.data?.seasons ?? []);
  const approve = async () => {
    const amend: Amend = { ...(series && seasons ? { seasons } : {}), ...(request.kind === 'anime' && series && language ? { language } : {}) };
    setBusy(true);
    try { await approveRequest(request.id, amend); toast({ tone: 'success', message: `Approved ${request.title}.` }); onApproved(); onClose(); } catch (failure) { toast({ tone: 'error', message: errorCopy(errorCode(failure), "Lumina couldn't approve that request.") }); setBusy(false); }
  };
  return (
    <Dialog busy={busy} footer={<><Button onClick={onClose} variant="quiet">Cancel</Button><Button aria-disabled={(series && !seasons) || undefined} busy={busy} onClick={() => { if (!series || seasons) void approve(); }} variant="primary">Approve</Button></>} onClose={onClose} open title={`Approve ${request.title}`}>
      {series ? (detail.data ? <SeasonPicker mode={mode} onMode={setMode} onPicked={setPicked} picked={picked} seasons={detail.data.seasons} /> : detail.error ? null : <Skeleton count={3} label="Loading seasons" shape="line" />) : <p className="rq-muted">Films have nothing to change; this approves as asked.</p>}
      {request.kind === 'anime' && series ? <LanguageChoice onChange={setLanguage} value={language} /> : null}
    </Dialog>
  );
}

function DeclineDialog({ request, onClose, onDeclined }: { request: MediaRequest; onClose: () => void; onDeclined: () => void }) {
  const toast = useToast();
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const decline = async () => {
    setBusy(true);
    try { await declineRequest(request.id, reason.trim() || undefined); toast({ tone: 'success', message: `Declined ${request.title}.` }); onDeclined(); onClose(); } catch (failure) { toast({ tone: 'error', message: errorCopy(errorCode(failure), "Lumina couldn't decline that request.") }); setBusy(false); }
  };
  return (
    <Dialog busy={busy} footer={<><Button onClick={onClose} variant="quiet">Keep it</Button><Button busy={busy} onClick={() => void decline()} variant="danger">Decline</Button></>} onClose={onClose} open size="sm" title={`Decline ${request.title}?`}>
      <Field hint={`${request.requested_by.name} will see this.`} label="Reason (optional)">
        {(ids) => <Textarea {...fieldProps(ids)} maxLength={500} onChange={(event) => setReason(event.currentTarget.value)} rows={3} value={reason} />}
      </Field>
    </Dialog>
  );
}

const FILTERS: ReadonlyArray<readonly [RequestState, string]> = [['approved', 'Approved'], ['processing', 'Downloading'], ['partially_available', 'Partly in'], ['available', 'In your vault'], ['declined', 'Declined'], ['failed', 'Needs attention']];

export function Manage() {
  const { isAdmin, onQueueChanged } = useRequests();
  const toast = useToast();
  const [filter, setFilter] = useState<RequestState | undefined>();
  const [dialog, setDialog] = useState<{ kind: 'amend' | 'decline'; request: MediaRequest } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const pending = usePolledRequests(isAdmin ? 'manage:pending' : null, { scope: 'all', status: 'pending' });
  const all = usePolledRequests(isAdmin ? `manage:all:${filter ?? ''}` : null, { scope: 'all', status: filter });
  if (!isAdmin) return <EmptyState body="Only admins approve requests." size="page" title="This page is for admins" />;
  const changed = () => { pending.reload(); all.reload(); onQueueChanged(); };
  const act = async (request: MediaRequest, run: () => Promise<unknown>, done: string) => {
    setBusy(request.id);
    try { await run(); toast({ tone: 'success', message: done }); changed(); } catch (failure) { toast({ tone: 'error', message: errorCopy(errorCode(failure)) }); } finally { setBusy(null); }
  };
  const counts = all.page?.counts ?? {};
  return (
    <>
      <section aria-labelledby="rq-queue" className="rq-manage">
        <h2 id="rq-queue">Waiting for you{pending.page?.items.length ? ` · ${pending.page.items.length}` : ''}</h2>
        {pending.error && !pending.page ? <ErrorState onRetry={pending.reload} title="Lumina couldn't load the queue." /> : !pending.page ? <Skeleton count={2} label="Loading the queue" shape="row" />
          : !pending.page.items.length ? <EmptyState body="New requests that need approval land here." title="Nothing waiting" /> : (
            <ul className="rq-requests">
              {pending.page.items.map((request) => (
                <RequestRow key={request.id} request={request} showRequester>
                  <Button busy={busy === request.id} data-focus-item onClick={() => void act(request, () => approveRequest(request.id), `Approved ${request.title}.`)} variant="primary">Approve</Button>
                  <Button data-focus-item onClick={() => setDialog({ kind: 'amend', request })}>Approve with changes</Button>
                  <Button data-focus-item onClick={() => setDialog({ kind: 'decline', request })} variant="quiet">Decline</Button>
                </RequestRow>
              ))}
            </ul>
          )}
      </section>
      <section aria-labelledby="rq-all" className="rq-manage">
        <h2 id="rq-all">All requests</h2>
        <Chips label="Status" onChange={setFilter} options={FILTERS.map(([state, label]) => [state, counts[state] ? `${label} · ${counts[state]}` : label] as const)} value={filter} />
        {all.error && !all.page ? <ErrorState onRetry={all.reload} title="Lumina couldn't load requests." /> : !all.page ? <Skeleton count={3} label="Loading requests" shape="row" />
          : !all.page.items.length ? <EmptyState title="No requests here." /> : (
            <ul className="rq-requests">
              {all.page.items.map((request) => (
                <RequestRow key={request.id} request={request} showRequester>
                  {request.status === 'failed' ? <Button busy={busy === request.id} data-focus-item onClick={() => void act(request, () => retryRequest(request.id), `Sent ${request.title} again.`)}>Retry</Button> : null}
                </RequestRow>
              ))}
            </ul>
          )}
      </section>
      {dialog?.kind === 'amend' ? <AmendDialog onApproved={changed} onClose={() => setDialog(null)} request={dialog.request} /> : null}
      {dialog?.kind === 'decline' ? <DeclineDialog onClose={() => setDialog(null)} onDeclined={changed} request={dialog.request} /> : null}
    </>
  );
}
