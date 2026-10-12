import { getArtworkProgress } from '../../api';
import { Button, ProgressBar } from '../../ui';
import { useAdminResource } from './useAdminResource';

const counts = new Intl.NumberFormat('en-US');

/** Settings → Library & storage: how much artwork already has small, fast copies. */
export function ArtworkProgressRow() {
  const { data, error, reload } = useAdminResource(getArtworkProgress, 'Lumina could not load artwork progress.');
  if (error) {
    return <p className="auth-error" role="alert">{error} <Button onClick={reload} variant="quiet">Try again</Button></p>;
  }
  if (!data) return <p aria-busy="true" className="admin-note">Loading…</p>;
  return (
    <div className="g-artwork-progress" role="status">
      <p>Artwork prepared <span className="g-tabular">{counts.format(data.prepared)} / {counts.format(data.total)}</span></p>
      <ProgressBar label="Artwork prepared" value={data.total ? Math.floor((data.prepared / data.total) * 100) : null} />
      {data.paused_for_playback ? <p className="admin-note">Paused while a video is being converted</p> : null}
    </div>
  );
}
