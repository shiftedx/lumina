import { ArchiveRestore, FileCheck2, HardDriveDownload } from 'lucide-react';
import { useEffect, useState } from 'react';

import { applyMusicWriteback, listMusicFileBackups, previewMusicWriteback, restoreMusicFile } from '../../api';
import { formatBytes, formatDateTime } from '../../utils';
import { Button, Checkbox } from '../../ui';
import type { MusicTrack } from './musicTypes';

type WritebackPreview = Awaited<ReturnType<typeof previewMusicWriteback>>;
type WritebackResult = Awaited<ReturnType<typeof applyMusicWriteback>>;
type FileBackup = Awaited<ReturnType<typeof listMusicFileBackups>>[number];

export interface MusicWritebackProps {
  tracks: readonly MusicTrack[];
  isAdmin: boolean;
  onRefreshed?: () => void;
  onBusyChange?: (busy: boolean) => void;
}

const fieldValue = (value: unknown): string => {
  if (Array.isArray(value)) return value.join(', ') || 'None';
  if (value === null || value === undefined || value === '') return 'None';
  return String(value);
};

function failureMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

/** Admin-only, previewed persistence of server metadata into the selected source audio files. */
export function MusicWriteback({ tracks, isAdmin, onRefreshed, onBusyChange }: MusicWritebackProps) {
  const [includeArtwork, setIncludeArtwork] = useState(false);
  const [preview, setPreview] = useState<WritebackPreview | null>(null);
  const [result, setResult] = useState<WritebackResult | null>(null);
  const [backups, setBackups] = useState<FileBackup[] | null>(null);
  const [busy, setBusy] = useState<'preview' | 'apply' | 'history' | string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const selectionKey = tracks.map((track) => track.id).join('\u0000');

  useEffect(() => {
    setPreview(null);
    setResult(null);
    setError(null);
  }, [selectionKey]);
  useEffect(() => { onBusyChange?.(busy !== null); }, [busy, onBusyChange]);

  if (!isAdmin || !tracks.length) return null;

  const ids = tracks.map((track) => track.id);
  const overLimit = tracks.length > 100;
  const previewWriteback = async () => {
    setBusy('preview');
    setError(null);
    setResult(null);
    try {
      setPreview(await previewMusicWriteback(ids, includeArtwork));
    } catch (loadError) {
      setPreview(null);
      setError(failureMessage(loadError, 'Lumina could not preview the file changes.'));
    } finally {
      setBusy(null);
    }
  };
  const loadBackups = async () => {
    setBusy('history');
    setError(null);
    try { setBackups(await listMusicFileBackups()); }
    catch (loadError) { setError(failureMessage(loadError, 'Lumina could not load file backups.')); }
    finally { setBusy(null); }
  };
  const apply = async () => {
    if (!preview) return;
    setBusy('apply');
    setError(null);
    try {
      const answer = await applyMusicWriteback(preview.plan_id);
      setResult(answer);
      setPreview(null);
      onRefreshed?.();
      try { setBackups(await listMusicFileBackups()); }
      catch { setError('Files were updated, but the backup history could not refresh.'); }
    } catch (applyError) {
      setError(failureMessage(applyError, 'Lumina could not write these audio files. Preview again and retry.'));
    } finally {
      setBusy(null);
    }
  };
  const restore = async (backup: FileBackup) => {
    setBusy(backup.id);
    setError(null);
    try {
      await restoreMusicFile(backup.id);
      onRefreshed?.();
      try { setBackups(await listMusicFileBackups()); }
      catch { setError('The file was restored, but the backup history could not refresh.'); }
    } catch (restoreError) {
      setError(failureMessage(restoreError, 'Lumina could not restore this file.'));
    } finally {
      setBusy(null);
    }
  };

  return (
    <section aria-labelledby="music-writeback-heading" className="music-writeback">
      <header>
        <div>
          <h3 id="music-writeback-heading">Write changes to audio files</h3>
          <p>Optional owner action. Metadata edits stay in Lumina unless you preview and apply this file writeback.</p>
        </div>
        <HardDriveDownload aria-hidden="true" />
      </header>
      <Checkbox
        checked={includeArtwork}
        label="Include current album artwork"
        hint="Supported audio files receive the current square cover with their metadata."
        onChange={(event) => { setIncludeArtwork(event.currentTarget.checked); setPreview(null); setResult(null); }}
      />
      {overLimit ? <p className="music-editor-error" role="status">Choose 100 or fewer tracks for one file writeback.</p> : null}
      <div className="music-editor-actions">
        <Button busy={busy === 'preview'} disabled={busy !== null || overLimit} onClick={() => void previewWriteback()}>Preview file writeback</Button>
        <Button busy={busy === 'history'} disabled={busy !== null} onClick={() => void loadBackups()} variant="quiet">{backups === null ? 'Show file backups' : 'Refresh file backups'}</Button>
      </div>
      {error ? <p className="music-editor-error" role="alert">{error}</p> : null}
      {preview ? (
        <div className="music-writeback-preview">
          <div className="music-writeback-summary">
            <strong>{preview.entries.filter((entry) => entry.supported).length} supported of {preview.entries.length} selected</strong>
            <span>Backup space needed: {formatBytes(preview.backup_bytes)} · Artwork {preview.include_artwork ? 'included' : 'not included'}</span>
            <span>Supported formats: MP3, FLAC, OGG, Opus, M4A, M4B and MP4. Unsupported files will be skipped.</span>
          </div>
          <ul className="music-writeback-entries">
            {preview.entries.map((entry) => (
              <li className={entry.supported ? '' : 'is-unsupported'} key={entry.item_id}>
                <div><strong>{entry.title}</strong><span>{entry.format.toUpperCase()} · {entry.supported ? 'Ready' : 'Unsupported — will be skipped'}</span></div>
                <dl>{Object.entries(entry.fields).map(([field, value]) => <div key={field}><dt>{field.replaceAll('_', ' ')}</dt><dd>{fieldValue(value)}</dd></div>)}</dl>
              </li>
            ))}
          </ul>
          <div className="music-editor-actions">
            <Button busy={busy === 'apply'} disabled={busy !== null || !preview.entries.some((entry) => entry.supported)} icon={<FileCheck2 />} onClick={() => void apply()} variant="primary">Apply to audio files</Button>
            <Button disabled={busy !== null} onClick={() => setPreview(null)} variant="quiet">Cancel preview</Button>
          </div>
        </div>
      ) : null}
      {result ? (
        <div className="music-writeback-result" role="status">
          <strong>Wrote {result.applied.length} file{result.applied.length === 1 ? '' : 's'}.</strong>
          {result.applied.length ? <ul>{result.applied.map((entry) => <li key={entry.id}>{entry.title} · journal <code>{entry.id}</code></li>)}</ul> : null}
          {result.skipped.length ? <p>{result.skipped.length} skipped: {result.skipped.map((entry) => entry.reason).join('; ')}</p> : null}
        </div>
      ) : null}
      {backups !== null ? (
        <div className="music-writeback-backups">
          <h4>File backups</h4>
          {backups.length ? <ul>{backups.map((backup) => {
            const track = tracks.find((candidate) => candidate.id === backup.item_id);
            return <li key={backup.id}><span><strong>{track?.title ?? backup.item_id}</strong><small>{formatDateTime(backup.created_at)} · journal <code>{backup.id}</code></small></span><Button busy={busy === backup.id} disabled={busy !== null || backup.restored} icon={<ArchiveRestore />} onClick={() => void restore(backup)} variant="quiet">{backup.restored ? 'Restored' : 'Restore file'}</Button></li>;
          })}</ul> : <p>No file backups yet.</p>}
        </div>
      ) : null}
    </section>
  );
}
