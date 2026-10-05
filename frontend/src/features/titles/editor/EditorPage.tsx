import { useEffect, useState } from 'react';

import { ApiRequestError, getTitleMetadata, saveMetadataEdits } from '../../../api';
import type { EditConflict, EditResult, MetadataErrorBody, TitleImageEntry, UserProfile } from '../../../types';
import { Button, EmptyState, ErrorState, Masthead, SegmentedControl, Skeleton, useToast } from '../../../ui';
import { forgetTitle, forgetTitles } from '../../gallery/titleCache';
import { useUnsavedChanges } from '../../settings/unsavedChanges';
import { useFetched } from '../titleModel';
import ArtworkTab from './artwork/ArtworkTab';
import { ConflictBanner } from './ConflictBanner';
import DetailsTab from './DetailsTab';
import { undoBatch } from './editorActions';
import { EditorFooter } from './EditorFooter';
import {
  applyConflicts, dirtyCount, discardKey, FIELD_META, setChange, setLock, tabsFor, togglePinned, toRequests, TYPE_LABEL,
  type Draft, type TabId, type TabProps,
} from './editorModel';
import EpisodesTab from './EpisodesTab';
import HistoryTab from './HistoryTab';
import IdsTab from './IdsTab';
import PeopleTab from './PeopleTab';
import './editor.css';

export interface EditorPageProps {
  id: string;
  tab: TabId | null;
  onTab: (tab: TabId) => void;
  onBack: () => void;
  user: UserProfile;
}

function failureMessage(failure: unknown): string {
  if (failure instanceof ApiRequestError) {
    if (failure.status === 403) return failure.message === 'identify_requires_owner' ? 'Only vault owners can change the TMDB match.' : 'Ask a vault owner to let household members edit details.';
    const body = failure.body as MetadataErrorBody | null;
    if (body?.field) return `Check ${FIELD_META[body.field]?.label ?? body.field}: ${body.reason ?? 'that value is not allowed'}`;
  }
  if (failure instanceof ApiRequestError && (failure.status === 422 || failure.status === 413)) return "Lumina couldn't save. One of the values you changed is not allowed; check the fields you edited, including episodes.";
  return "Lumina couldn't save. Try again.";
}

/** One page, one draft, one Save (one undoable batch). */
export function EditorPage({ id, tab, onTab, onBack, user }: EditorPageProps) {
  const toast = useToast();
  const [revision, setRevision] = useState(0);
  const { data: doc, error, loading, patch } = useFetched(id, () => getTitleMetadata(id), revision);
  const [draft, setDraft] = useState<Draft>({});
  const [banner, setBanner] = useState<EditConflict[] | null>(null);
  // Bumped when the draft is thrown away so tab forms holding local row state remount from the document.
  const [formKey, setFormKey] = useState(0);
  const [saving, setSaving] = useState(false);
  const [episodesRevision, setEpisodesRevision] = useState(0);
  const [historyRevision, setHistoryRevision] = useState(0);
  const count = dirtyCount(draft);
  useUnsavedChanges(count > 0, 'this title');

  const wantedTab = tab && !tabsFor(doc?.type ?? 'movie').some((option) => option.value === tab);
  useEffect(() => { if (doc && wantedTab) onTab('details'); }, [doc, wantedTab]); // eslint-disable-line react-hooks/exhaustive-deps -- fix the address once the document says which tabs exist
  const reload = () => setRevision((n) => n + 1);
  // An undo can touch any title in the batch: drop every cached detail, refetch this one, and refresh the episode table and History.
  const reloadAfterUndo = () => { forgetTitles(); setEpisodesRevision((n) => n + 1); setHistoryRevision((n) => n + 1); reload(); };

  if (loading && !doc) return <div className="ed"><Skeleton count={6} label="Loading details" shape="row" /></div>;
  if (error || !doc) {
    if (error instanceof ApiRequestError && error.status === 404) return <div className="ed"><EmptyState size="page" title="This title isn't in your library." /></div>;
    if (error instanceof ApiRequestError && error.status === 403) return <div className="ed"><EmptyState size="page" title="Ask a vault owner to let household members edit details." /></div>;
    return <div className="ed"><ErrorState onRetry={reload} title="Lumina could not load this title." /></div>;
  }

  function applyResult(result: EditResult) {
    const conflicted = new Set(result.conflicts.map((conflict) => conflict.title_id));
    const applied = result.titles.filter((title) => !conflicted.has(title.title_id));
    setDraft((current) => {
      const next = { ...current };
      applied.forEach((title) => { delete next[title.title_id]; });
      return next;
    });
    for (const title of applied) {
      forgetTitle(title.title_id);
      if (title.title_id === id) patch(() => title);
    }
    setEpisodesRevision((n) => n + 1);
    setHistoryRevision((n) => n + 1);
    const batch = result.batch_id;
    const undoAction = batch ? { action: { label: 'Undo', onAction: () => void undoBatch(batch, toast, reloadAfterUndo) } } : {};
    if (result.conflicts.length) {
      if (applied.length) toast({ tone: 'success', message: 'Some titles saved.', ...undoAction });
      setBanner(result.conflicts);
      const mine = result.conflicts.find((conflict) => conflict.title_id === id);
      if (mine) patch((current) => ({ ...current, fields: { ...current.fields, ...mine.current } }));
      return;
    }
    setBanner(null);
    toast({ tone: 'success', message: 'Saved.', ...undoAction });
  }

  async function save(send: Draft = draft) {
    setSaving(true);
    try {
      applyResult(await saveMetadataEdits(toRequests(send)));
    } catch (failure) {
      toast({ tone: 'error', message: failureMessage(failure) });
    } finally {
      setSaving(false);
    }
  }

  const tabs = tabsFor(doc.type);
  const active: TabId = tabs.some((option) => option.value === tab) ? (tab as TabId) : 'details';
  const year = doc.fields.year?.value;
  const poster = doc.images.find((image) => image.type === 'Primary' && image.index === 0)?.url;
  const props: TabProps = {
    doc, draft, user, reload, reloadAfterUndo, episodesRevision,
    setField: (titleId, key, value, base) => setDraft((current) => setChange(current, titleId, key, value, base)),
    togglePin: (titleId, key) => setDraft((current) => togglePinned(current, titleId, key)),
    setItemLock: (titleId, next, base) => setDraft((current) => setLock(current, titleId, next, base)),
    discardField: (titleId, key) => setDraft((current) => discardKey(current, titleId, key)),
  };
  const conflictFields = banner ? banner.flatMap((conflict) => conflict.fields) : [];

  return (
    <div className="ed">
      <div className="ed-head">
        {poster ? <img alt="" className="ed-poster" height={84} src={poster} width={56} /> : null}
        <Masthead
          actions={<Button onClick={onBack} variant="quiet">Back to title</Button>}
          kicker={`${TYPE_LABEL[doc.type] ?? doc.type}${year ? ` · ${String(year)}` : ''}`}
          level={1}
          title={doc.name}
        />
      </div>
      <SegmentedControl hideLegend legend="Edit sections" onChange={onTab} options={tabs} value={active} />
      {banner ? (
        <ConflictBanner
          fields={conflictFields}
          onMine={() => { const next = applyConflicts(draft, banner, 'mine'); setDraft(next); setBanner(null); void save(next); }}
          onTheirs={() => {
            setDraft(applyConflicts(draft, banner, 'theirs'));
            setFormKey((n) => n + 1);
            const mine = banner.find((conflict) => conflict.title_id === id);
            if (mine) patch((current) => ({ ...current, fields: { ...current.fields, ...mine.current } }));
            setBanner(null);
          }}
        />
      ) : null}
      <div className="ed-body">
        {active === 'details' ? <DetailsTab {...props} /> : null}
        {active === 'people' ? <PeopleTab key={formKey} {...props} /> : null}
        {active === 'artwork' ? (
          <ArtworkTab
            doc={doc}
            onChanged={() => setHistoryRevision((n) => n + 1)}
            onImages={(images: TitleImageEntry[]) => { patch((current) => ({ ...current, images })); forgetTitle(id); }}
          />
        ) : null}
        {active === 'ids' ? <IdsTab key={formKey} {...props} /> : null}
        {active === 'episodes' ? <EpisodesTab {...props} /> : null}
        {active === 'history' ? <HistoryTab key={historyRevision} {...props} /> : null}
      </div>
      <EditorFooter count={count} onDiscard={() => { setDraft({}); setBanner(null); setFormKey((n) => n + 1); }} onSave={() => void save()} saving={saving} />
    </div>
  );
}
