import { useState, type ReactElement } from 'react';
import { forgetTitle } from '../../../gallery/titleCache';
import { undoBatch } from '../editorActions';
import { ApiRequestError, getTitleMetadata, removeTitleImage, reorderBackdrops, revertMetadata, saveMetadataEdits, uploadTitleImage } from '../../../../api';
import type { TitleImageEntry } from '../../../../types';
import { ConfirmDialog, useToast, VisuallyHidden } from '../../../../ui';
import { ArtworkCard } from './ArtworkCard';
import { CandidateDialog } from './CandidateDialog';
import { UPLOAD_MAX_BYTES, isDuplicate, uploadErrorMessage } from './uploadErrors';
import { imageAt, moveOrder, slotsFor, type ArtworkTabDoc, type Slot } from './artworkModel';
import './artwork.css';

export interface ArtworkTabProps {
  doc: ArtworkTabDoc;
  onImages: (images: TitleImageEntry[]) => void;
  onChanged: () => void;
}

export default function ArtworkTab({ doc, onImages, onChanged }: ArtworkTabProps): ReactElement {
  const toast = useToast();
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<{ kind: 'remove' | 'revert'; slot: Slot } | null>(null);
  const [dialog, setDialog] = useState<Slot | null>(null);
  const [uploadingKey, setUploadingKey] = useState<string | null>(null);
  const [status, setStatus] = useState('');
  const slots = slotsFor(doc.type, doc.images);
  const backdrops = slots.filter((slot) => slot.type === 'Backdrop' && imageAt(doc.images, 'Backdrop', slot.index));
  const occupied = backdrops.map((slot) => slot.index);

  const reload = async () => {
    try { onImages((await getTitleMetadata(doc.title_id)).images); } catch { /* the toast still says the artwork moved */ }
    toast({ tone: 'info', message: uploadErrorMessage(409) });
  };

  // One write at a time, so base_tag is always the tag the user saw; a stale tag reloads instead of retrying.
  const run = async (slot: Slot, work: () => Promise<TitleImageEntry[]>, announce = '', failure: (error: unknown) => string = () => 'Lumina could not change the artwork. Try again.') => {
    if (busyKey) return;
    setBusyKey(slot.key);
    try {
      onImages(await work());
      onChanged();
      setStatus(announce);
    } catch (error) {
      if (error instanceof ApiRequestError && error.status === 409 && !isDuplicate(error)) {
        await reload();
      } else toast({ tone: 'error', message: failure(error) });
    } finally {
      setBusyKey(null);
    }
  };

  const upload = async (slot: Slot, file: File) => {
    if (busyKey) return;
    if (file.size > UPLOAD_MAX_BYTES) { toast({ tone: 'error', message: uploadErrorMessage(413) }); return; }
    setUploadingKey(slot.key);
    try {
      await run(slot, () => uploadTitleImage(doc.title_id, slot.type, slot.index, file, imageAt(doc.images, slot.type, slot.index)?.tag ?? null), `${slot.label} replaced.`, (error) => (error instanceof ApiRequestError ? uploadErrorMessage(error.status, error.message) : uploadErrorMessage(0)));
    } finally { setUploadingKey(null); }
  };
  const pin = (slot: Slot) => run(slot, async () => (await saveMetadataEdits([{ title_id: doc.title_id, changes: {}, pin: [slot.key] }])).titles[0].images, `${slot.label} locked.`);
  const revert = (slot: Slot) => run(slot, async () => {
    const result = await revertMetadata(doc.title_id, [slot.key]);
    const batch = result.batch_id;
    if (batch) {
      toast({ tone: 'success', message: `${slot.label} reverted.`, action: { label: 'Undo', onAction: () => void undoBatch(batch, toast, () => { forgetTitle(doc.title_id); onChanged(); getTitleMetadata(doc.title_id).then((fresh) => onImages(fresh.images), () => undefined); }) } });
    }
    return result.title.images;
  }, `${slot.label} reverted.`);
  const remove = (slot: Slot) => run(slot, () => removeTitleImage(doc.title_id, slot.type, slot.index, imageAt(doc.images, slot.type, slot.index)?.tag ?? null), `${slot.label} removed.`);
  const move = (slot: Slot, direction: -1 | 1) => {
    const order = moveOrder(occupied, slot.index, direction);
    if (!order) return undefined;
    const position = order.indexOf(slot.index) + 1;
    const tags = order.map((index) => imageAt(doc.images, 'Backdrop', index)?.tag);
    if (tags.some((tag) => !tag)) return reload();
    return run(slot, () => reorderBackdrops(doc.title_id, tags as string[]), `Backdrop ${slot.index + 1} moved to position ${position} of ${order.length}.`);
  };

  const confirming = confirm?.kind === 'revert';
  const done = () => setConfirm(null);
  return (
    <div className="ed-art">
      <p>Artwork changes save as you make them. Each one can be undone from History.</p>
      {doc.tmdb_configured ? null : <p>Add a TMDB key in Settings → Server → Metadata to choose artwork from TMDB.</p>}
      <div className="ed-art-grid" role="list">
        {slots.map((slot) => (
          <ArtworkCard
            busy={busyKey !== null}
            canChooseFromTmdb={doc.tmdb_configured}
            image={imageAt(doc.images, slot.type, slot.index)}
            isFirstBackdrop={occupied[0] === slot.index}
            isLastBackdrop={occupied[occupied.length - 1] === slot.index}
            itemLocked={doc.locked}
            key={slot.key}
            onChoose={() => setDialog(slot)}
            onUpload={(file) => { void upload(slot, file); }}
            uploading={uploadingKey === slot.key}
            onMove={(direction) => { void move(slot, direction); }}
            onPin={() => { void pin(slot); }}
            onRemove={() => setConfirm({ kind: 'remove', slot })}
            onUnlock={() => setConfirm({ kind: 'revert', slot })}
            slot={slot}
          />
        ))}
      </div>
      {dialog ? (
        <CandidateDialog
          image={imageAt(doc.images, dialog.type, dialog.index)}
          onChosen={(images) => { setDialog(null); onImages(images); onChanged(); setStatus(`${dialog.label} replaced.`); }}
          onClose={() => setDialog(null)}
          onStale={() => { setDialog(null); void reload(); }}
          slot={dialog}
          titleId={doc.title_id}
        />
      ) : null}
      <VisuallyHidden><span aria-live="polite" role="status">{status}</span></VisuallyHidden>
      <ConfirmDialog
        body={confirming ? 'The image you chose is replaced by the one Lumina found. You can undo this from History.' : 'You can undo this from History.'}
        cancelLabel={confirming ? 'Keep my image' : undefined}
        confirmLabel={confirming ? 'Revert' : 'Remove'}
        danger={!confirming}
        onCancel={done}
        onConfirm={() => { const pending = confirm; done(); if (pending) void (pending.kind === 'remove' ? remove(pending.slot) : revert(pending.slot)); }}
        open={confirm !== null}
        title={confirming ? 'Go back to the source image?' : `Remove this ${confirm?.slot.label.toLowerCase() ?? 'image'}?`}
      />
    </div>
  );
}
