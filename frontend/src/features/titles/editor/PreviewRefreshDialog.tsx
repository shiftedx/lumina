import { useEffect, useState } from 'react';

import { ApiRequestError, previewMetadataRefresh, refreshTitleMetadata } from '../../../api';
import { Artwork } from '../../../Artwork';
import type { RefreshOutcome, RefreshPreview } from '../../../types';
import { Button, Dialog, Skeleton, useToast } from '../../../ui';
import { FIELD_META, formatValue } from './editorModel';

const OUTCOME: Record<RefreshOutcome, string> = {
  update: 'Will update', same: 'Same', kept_edit: 'Kept: your edit', kept_lock: 'Kept: locked', kept_higher_source: 'Kept: NFO file wins', new: 'Will add',
};

function failureCopy(failure: unknown): string {
  if (failure instanceof ApiRequestError) {
    if (failure.message === 'tmdb_not_configured') return 'Add a TMDB key in Settings → Server → Metadata to preview a refresh.';
    if (failure.message === 'title_locked') return 'This title is locked, so a refresh would change nothing. Turn off Lock this item first.';
    if (failure.message === 'no_match') return 'This title has no TMDB match yet. Use Fix match first.';
    if (failure.status === 403 || failure.status === 404) return 'You can no longer preview this title.';
    if (failure.status === 429) return 'You can preview 6 times a minute. Wait a moment.';
  }
  return 'TMDB did not answer. Try again.';
}

export function PreviewRefreshDialog({ titleId, onClose }: { titleId: string; onClose: () => void }) {
  const toast = useToast();
  const [preview, setPreview] = useState<RefreshPreview | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  useEffect(() => {
    let current = true;
    previewMetadataRefresh(titleId).then((value) => { if (current) setPreview(value); }, (error: unknown) => { if (current) setFailure(failureCopy(error)); });
    return () => { current = false; };
  }, [titleId]);

  async function refresh() {
    try {
      await refreshTitleMetadata(titleId);
      toast({ tone: 'success', message: 'Refreshing details in the background.' });
      onClose();
    } catch (error) {
      setFailure(failureCopy(error));
    }
  }
  return (
    <Dialog
      footer={<>
        <Button disabled={!preview} onClick={() => void refresh()} variant="primary">Refresh now</Button>
        <Button onClick={onClose} variant="quiet">Close</Button>
      </>}
      onClose={onClose}
      open
      size="lg"
      title="Preview refresh"
    >
      {failure ? <p className="auth-error" role="alert">{failure}</p> : preview ? (
        <>
          <dl className="ed-preview">
            {preview.fields.map((row) => (
              <div key={row.field}>
                <dt>{FIELD_META[row.field]?.label ?? row.field}</dt>
                <dd>{formatValue(row.current)} → {formatValue(row.incoming)} <span className="ed-src">{OUTCOME[row.outcome]}</span></dd>
              </div>
            ))}
          </dl>
          {preview.images.map((image) => (
            <div className="ed-preview-image" key={image.type}>
              <span>{image.type}</span>
              <Artwork alt="" src={image.incoming_preview_url} />
              <span className="ed-src">{OUTCOME[image.outcome]}</span>
            </div>
          ))}
        </>
      ) : <Skeleton label="Loading the preview" shape="row" />}
    </Dialog>
  );
}
