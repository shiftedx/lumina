import { useRef } from 'react';
import { Ellipsis, Lock, LockOpen } from 'lucide-react';
import { Artwork } from '../../../../Artwork';
import type { TitleImageEntry } from '../../../../types';
import { IconButton, Menu, ProgressBar, type MenuEntry } from '../../../../ui';
import { originLabel, type Slot } from './artworkModel';

export interface ArtworkCardProps {
  slot: Slot;
  image: TitleImageEntry | null;
  itemLocked: boolean;
  busy: boolean;
  canChooseFromTmdb: boolean;
  isFirstBackdrop: boolean;
  isLastBackdrop: boolean;
  onPin: () => void;
  onUnlock: () => void;
  onRemove: () => void;
  onMove: (direction: -1 | 1) => void;
  onChoose?: () => void;
  onUpload?: (file: File) => void;
  uploading?: boolean;
}

export function ArtworkCard({ slot, image, itemLocked, busy, canChooseFromTmdb, isFirstBackdrop, isLastBackdrop, onPin, onUnlock, onRemove, onMove, onChoose, onUpload, uploading = false }: ArtworkCardProps) {
  const input = useRef<HTMLInputElement>(null);
  const mine = image?.source === 'user';
  const blocked = itemLocked && !mine;
  const items: MenuEntry[] = [
    { kind: 'item', label: 'Choose from TMDB…', disabled: !canChooseFromTmdb, onSelect: onChoose },
    { kind: 'item', label: 'Upload…', onSelect: () => input.current?.click() },
  ];
  if (slot.type === 'Backdrop' && image) {
    items.push({ kind: 'item', label: 'Move left', disabled: isFirstBackdrop, onSelect: () => onMove(-1) }, { kind: 'item', label: 'Move right', disabled: isLastBackdrop, onSelect: () => onMove(1) });
  }
  if (image && mine) items.push({ kind: 'item', label: 'Go back to the source image', onSelect: onUnlock });
  if (image) items.push({ kind: 'separator' }, { kind: 'item', label: 'Remove', danger: true, onSelect: onRemove });
  const lockLabel = blocked ? 'Locked by the item lock' : `${mine ? 'Unlock' : 'Lock'} ${slot.label}`;
  return (
    <div aria-busy={busy || undefined} className="ed-art-card" role="listitem">
      <div className="ed-art-frame" data-type={slot.label === 'Still' ? 'Still' : slot.type}>
        <Artwork alt={slot.label} fallback={<span className="ed-art-empty">No {slot.label.toLowerCase()}</span>} src={image?.url} />
      </div>
      {uploading ? <ProgressBar label={`Uploading ${slot.label}`} value={null} /> : null}
      <input
        accept="image/jpeg,image/png,image/webp"
        aria-hidden="true"
        hidden
        onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ''; if (file) onUpload?.(file); }}
        ref={input}
        tabIndex={-1}
        type="file"
      />
      <div className="ed-art-caption">
        <strong>{slot.label}</strong>
        {image && originLabel(image) ? <span className="g-chip">{originLabel(image)}</span> : null}
        {image ? <IconButton disabled={busy || blocked} icon={mine ? <LockOpen /> : <Lock />} label={lockLabel} onClick={mine ? onUnlock : onPin} pressed={mine} /> : null}
        <Menu align="end" items={items} trigger={(props) => <IconButton {...props} disabled={busy} icon={<Ellipsis />} label={`${slot.label} options`} />} />
      </div>
    </div>
  );
}
