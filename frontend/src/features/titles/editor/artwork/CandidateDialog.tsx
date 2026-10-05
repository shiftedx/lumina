import { useEffect, useState } from 'react';
import { Artwork } from '../../../../Artwork';
import { ApiRequestError, chooseTitleImage, listImageCandidates } from '../../../../api';
import type { ImageCandidate, TitleImageEntry } from '../../../../types';
import { Button, Dialog, EmptyState, ErrorState, Radio, Skeleton } from '../../../../ui';
import type { Slot } from './artworkModel';
import { CHOOSE_RATE_COPY, DUPLICATE_COPY, isDuplicate } from './uploadErrors';

export interface CandidateDialogProps {
  titleId: string;
  slot: Slot;
  image: TitleImageEntry | null;
  onChosen: (images: TitleImageEntry[]) => void;
  onStale: () => void;
  onClose: () => void;
}

const languageName = (code: string | null): string => {
  if (!code) return 'No language';
  try { return new Intl.DisplayNames(['en'], { type: 'language' }).of(code) ?? code; } catch { return code; }
};

const failureCopy = (error: unknown): { title: string; retry: boolean } => {
  const code = error instanceof ApiRequestError ? error.message : '';
  if (code === 'tmdb_not_configured') return { title: 'Add a TMDB key in Settings → Server → Metadata to choose artwork from TMDB.', retry: false };
  if (code === 'no_tmdb_id') return { title: 'Match this title to TMDB first, using Fix match.', retry: false };
  return { title: 'TMDB did not answer. Try again.', retry: true };
};

export function CandidateDialog({ titleId, slot, image, onChosen, onStale, onClose }: CandidateDialogProps) {
  const [candidates, setCandidates] = useState<ImageCandidate[] | null>(null);
  const [failure, setFailure] = useState<{ title: string; retry: boolean } | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [choice, setChoice] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const label = slot.label.toLowerCase();

  useEffect(() => {
    let cancelled = false;
    setCandidates(null);
    setFailure(null);
    listImageCandidates(titleId, slot.type, slot.index).then(
      (found) => { if (!cancelled) setCandidates(found); },
      (error) => { if (!cancelled) setFailure(failureCopy(error)); },
    );
    return () => { cancelled = true; };
  }, [titleId, slot.type, slot.index, attempt]);

  const save = async () => {
    if (!choice) return;
    setSaving(true);
    try {
      onChosen(await chooseTitleImage(titleId, slot.type, slot.index, { tmdb_path: choice, base_tag: image?.tag ?? null }));
    } catch (error) {
      if (error instanceof ApiRequestError && isDuplicate(error)) { setFailure({ title: DUPLICATE_COPY, retry: false }); setSaving(false); }
      else if (error instanceof ApiRequestError && error.status === 409) onStale();
      else if (error instanceof ApiRequestError && error.status === 429) { setFailure({ title: CHOOSE_RATE_COPY, retry: false }); setSaving(false); }
      else { setFailure({ title: 'Lumina could not use that image. Try again.', retry: false }); setSaving(false); }
    }
  };

  return (
    <Dialog
      busy={saving}
      description="Images from TMDB. Pick one to use it."
      footer={<><Button onClick={onClose} variant="quiet">Cancel</Button><Button busy={saving} disabled={!choice} onClick={() => { void save(); }}>Use this image</Button></>}
      onClose={onClose}
      open
      size="lg"
      title={`Choose a ${label}`}
    >
      {failure ? <ErrorState onRetry={failure.retry ? () => setAttempt((n) => n + 1) : undefined} title={failure.title} />
        : candidates === null ? <Skeleton count={6} label="Loading images from TMDB" shape="block" />
        : candidates.length === 0 ? <EmptyState title="TMDB has no images for this title." />
        : (
          <div aria-label={`${slot.label} candidates`} className="ed-art-picks" data-type={slot.type} role="radiogroup">
            {candidates.map((candidate) => (
              <Radio
                checked={choice === candidate.tmdb_path}
                key={candidate.tmdb_path}
                label={<><Artwork alt="" src={candidate.preview_url} /><span>{`${languageName(candidate.language)} · ${candidate.width} × ${candidate.height}`}</span></>}
                name="artwork-candidate"
                onChange={() => setChoice(candidate.tmdb_path)}
              />
            ))}
          </div>
        )}
    </Dialog>
  );
}
