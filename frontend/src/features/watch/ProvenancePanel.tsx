import { type MouseEvent, type ReactNode, useEffect, useState } from 'react';
import { ExternalLink } from '../../luminaIcons';
import { getProvenance, type Provenance } from '../../api';
import { formatBytes } from '../../utils';

const STATE_NOTE: Record<string, string> = { missing: 'File missing', quarantined: 'Quarantined' };

function describeFormat(format: NonNullable<Provenance['format']>, size: number | null) {
  const container = format.container?.split(',')[0];
  return [
    format.width && format.height ? `${format.width}×${format.height}` : null,
    [format.video_codec, format.audio_codec].filter(Boolean).join(' / ') || null,
    container || null,
    size ? formatBytes(size) : null,
  ].filter(Boolean).join(' · ');
}

const day = (value: string) => new Date(value.length === 10 ? `${value}T00:00:00` : value).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });

/**
 * Where this library item came from and what else in the library relates to it.
 * Every row is a stored fact; a missing fact is simply not shown.
 */
export function ProvenancePanel({ itemId, onOpenItem, compact = false }: { itemId: string; onOpenItem?: (id: string) => void; compact?: boolean }) {
  const [data, setData] = useState<Provenance | null | undefined>(undefined);

  useEffect(() => {
    const controller = new AbortController();
    getProvenance(itemId, controller.signal).then(setData).catch(() => { if (!controller.signal.aborted) setData(null); });
    return () => controller.abort();
  }, [itemId]);

  if (data === undefined) return compact ? null : <p aria-live="polite" className="provenance-state" role="status">Loading source details…</p>;
  if (data === null) return compact ? null : <p className="provenance-state">Source details are unavailable right now.</p>;

  const open = (event: MouseEvent<HTMLAnchorElement>, id: string) => {
    if (!onOpenItem || event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
    event.preventDefault();
    onOpenItem(id);
  };
  const origin = data.origin === 'imported'
    ? `Imported from ${data.storage_label || 'an external library'}`
    : data.origin === 'recording' ? `Live recording${data.provider ? ` from ${data.provider}` : ''}` : `Saved from ${data.provider || 'the web'}`;
  const format = data.format ? describeFormat(data.format, data.file_size) : data.file_size ? formatBytes(data.file_size) : null;
  const link = (href: string, text: string) => <a href={href} rel="noopener noreferrer" target="_blank">{text}<ExternalLink aria-hidden="true" /></a>;
  const rows = ([
    ['Origin', origin],
    ['Original page', data.original_url && link(data.original_url, new URL(data.original_url).hostname)],
    [data.origin === 'imported' ? 'Credited to' : 'Channel', data.channel && (data.channel_url ? link(data.channel_url, data.channel) : data.channel)],
    ['Published', data.uploaded_on && day(data.uploaded_on)],
    [data.origin === 'imported' ? 'Indexed' : 'Saved', data.saved_at && day(data.saved_at)],
    ['Stored in', data.storage_label && `${data.storage_label} · ${data.storage_mode === 'external' ? 'external, read-only' : 'managed by Lumina'}${data.media_state && STATE_NOTE[data.media_state] ? ` · ${STATE_NOTE[data.media_state]}` : ''}`],
    ['File', format],
    ['Notes', data.notes_count ? `${data.notes_count} note${data.notes_count === 1 ? '' : 's'} you can read` : null],
  ] as Array<[string, ReactNode]>).filter(([, value]) => value);

  if (compact) {
    const facts = [
      ['Video', [data.format?.width && data.format?.height ? `${data.format.width}×${data.format.height}` : null, data.format?.video_codec?.toUpperCase()].filter(Boolean).join(' · ')],
      ['Audio', data.format?.audio_codec?.toUpperCase()],
      ['Container', data.format?.container?.split(',')[0].toUpperCase()],
      ['Size', data.file_size ? formatBytes(data.file_size) : null],
      ['Storage', data.storage_label],
      ['Status', data.media_state && STATE_NOTE[data.media_state]],
    ].filter(([, value]) => value);
    return <section aria-label="Media file" className="watch-file">
      {facts.length ? <dl className="watch-file-facts">{facts.map(([term, value]) => <div key={term}><dt>{term}</dt><dd>{value}</dd></div>)}</dl> : null}
      <details className="watch-source-history"><summary>Source history</summary><dl className="watch-file-history">{rows.filter(([term]) => term !== 'File').map(([term, value]) => <div key={term}><dt>{term}</dt><dd>{value}</dd></div>)}</dl></details>
    </section>;
  }

  return (
    <section aria-labelledby="provenance-title" className="provenance">
      <h2 id="provenance-title">Source</h2>
      <dl>{rows.map(([term, value]) => <div key={term}><dt>{term}</dt><dd>{value}</dd></div>)}</dl>
      {data.related.map((group) => (
        <section aria-label={group.reason === 'same_channel' ? `More from ${group.name}` : `More in ${group.name}`} className="provenance-related" key={group.reason}>
          <h3>{group.reason === 'same_channel' ? 'More from' : 'More in'} {group.name} <span>{group.count} in your library</span></h3>
          <ul>{group.items.map((entry) => <li key={entry.id}><a href={`/watch/library/${encodeURIComponent(entry.id)}`} onClick={(event) => open(event, entry.id)}>{entry.title}</a></li>)}</ul>
        </section>
      ))}
    </section>
  );
}
