import { useEffect, useRef, useState } from 'react';
import { del, getSessionUserId, post } from '../../api';
import { pauseMediaForUser, playMediaForUser } from '../../playIntent';
import { Button, Field, fieldProps, Input } from '../../ui';
import { LuminaSpriteIcon } from '../../luminaIcons';
import { formatDuration } from '../../utils';
import { watchChromecastAvailability, startChromecast, type ChromecastController, type ChromecastStatus, type ChromecastTicket } from './chromecast';

export function ChromecastPanel({ itemId, audioIndex, subtitle, getMedia, onProgress }: {
  itemId: string; audioIndex: number | null; subtitle: string | null; getMedia: () => HTMLMediaElement | null;
  onProgress?: (position: number, duration: number | null) => void;
}) {
  const [available, setAvailable] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<ChromecastStatus | null>(null);
  const controller = useRef<ChromecastController | null>(null);
  const ticket = useRef<ChromecastTicket | null>(null);
  const generation = useRef(0);
  const castOwner = useRef<string | null>(null);
  const returning = useRef(false);
  const current = useRef({ getMedia, onProgress });
  current.current = { getMedia, onProgress };
  useEffect(() => watchChromecastAvailability(setAvailable), []);
  useEffect(() => {
    const media = current.current.getMedia();
    const preventDuplicate = () => { if (controller.current && !returning.current && media) pauseMediaForUser(media); };
    media?.addEventListener('play', preventDuplicate);
    return () => {
      generation.current += 1;
      media?.removeEventListener('play', preventDuplicate);
      const leaving = controller.current, leavingTicket = ticket.current;
      controller.current = null; ticket.current = null;
      if (leaving) void leaving.stop().catch(() => undefined);
      if (leavingTicket) void del(`/api/chromecast/tickets/${leavingTicket.ticket_id}`).catch(() => undefined);
    };
  }, [itemId]);
  useEffect(() => {
    if (!status) return;
    let active = true, reading = false, renewing = false;
    let lastProgress = 0;
    const timer = window.setInterval(() => {
      if (castOwner.current !== getSessionUserId()) {
        const previous = controller.current;
        controller.current = null; ticket.current = null; setStatus(null);
        if (previous) void previous.stop().catch(() => undefined);
        return;
      }
      if (reading || !controller.current) return;
      reading = true;
      void controller.current.status().then((next) => {
        if (!active) return;
        setStatus(next);
        if (Date.now() - lastProgress > 10_000) { lastProgress = Date.now(); current.current.onProgress?.(next.position, next.duration || null); }
      }).catch(() => { if (active) setError('Chromecast could not be reached. You can retry or return playback here.'); }).finally(() => { reading = false; });
    }, 2000);
    const renew = window.setInterval(() => {
      const output = ticket.current;
      if (!output || renewing) return;
      renewing = true;
      void post(`/api/chromecast/tickets/${output.ticket_id}/renew`).catch(() => { if (active) setError('Cast access could not be renewed. Return playback here if the receiver stops.'); }).finally(() => { renewing = false; });
    }, 60_000);
    return () => { active = false; window.clearInterval(timer); window.clearInterval(renew); };
  }, [Boolean(status)]);
  async function cast() {
    const currentGeneration = ++generation.current;
    const owner = getSessionUserId();
    const valid = () => currentGeneration === generation.current && owner === getSessionUserId();
    if (!owner) { setError('Sign in again before casting.'); return; }
    setBusy(true); setError(null);
    let created: ChromecastTicket | null = null;
    let target: ChromecastController | null = null;
    async function revokeCreated() {
      const output = created;
      created = null;
      if (output) await del(`/api/chromecast/tickets/${output.ticket_id}`).catch(() => undefined);
    }
    try {
      target = await startChromecast(async () => {
        if (!valid()) throw new Error('Playback owner changed.');
        const media = current.current.getMedia();
        const result = await post<ChromecastTicket>('/api/chromecast/tickets', { item_id: itemId, position_seconds: media && Number.isFinite(media.currentTime) ? media.currentTime : 0,
          audio_index: audioIndex, subtitle });
        created = result;
        if (!valid()) { await revokeCreated(); throw new Error('Playback owner changed.'); }
        return result;
      });
      if (!valid()) { await target.stop().catch(() => undefined); await revokeCreated(); return; }
      const next = await target.status();
      if (!valid()) { await target.stop().catch(() => undefined); await revokeCreated(); return; }
      controller.current = target; ticket.current = created; castOwner.current = owner;
      const media = current.current.getMedia();
      if (media) pauseMediaForUser(media);
      setStatus(next);
    } catch (failure) {
      if (target) await target.stop().catch(() => undefined);
      controller.current = null; ticket.current = null;
      await revokeCreated();
      if (valid()) setError(failure instanceof Error ? failure.message : 'Chromecast could not start.');
    } finally { if (valid()) setBusy(false); }
  }
  async function control(work: (target: ChromecastController) => Promise<void>) {
    const target = controller.current;
    if (!target) return;
    const currentGeneration = generation.current, owner = getSessionUserId();
    const valid = () => currentGeneration === generation.current && owner === getSessionUserId();
    setBusy(true); setError(null);
    try { await work(target); const next = await target.status(); if (valid()) setStatus(next); }
    catch (failure) { if (valid()) setError(failure instanceof Error ? failure.message : 'Chromecast did not acknowledge the command.'); }
    finally { if (valid()) setBusy(false); }
  }
  async function returnHere() {
    const target = controller.current, output = ticket.current, media = current.current.getMedia();
    if (!target || !output || !media) return;
    const owner = getSessionUserId(), currentGeneration = generation.current;
    const valid = () => owner === getSessionUserId() && currentGeneration === generation.current;
    setBusy(true); setError(null); returning.current = true;
    try {
      let position = status?.position ?? 0;
      try { position = (await target.status()).position; } catch { /* Last acknowledged position is still recoverable. */ }
      if (!valid()) return;
      media.currentTime = position;
      await playMediaForUser(media);
      if (!valid()) { pauseMediaForUser(media); return; }
      await target.stop();
      try { await del(`/api/chromecast/tickets/${output.ticket_id}`); }
      catch { if (valid()) setError('Playback returned here. Receiver access could not be revoked; it expires within 15 minutes.'); }
      controller.current = null; ticket.current = null; setStatus(null);
      current.current.onProgress?.(position, status?.duration || null);
    } catch { pauseMediaForUser(media); if (valid()) setError('Playback could not return here. The Cast controller is still available; retry when the connection recovers.'); }
    finally { returning.current = false; if (valid()) setBusy(false); }
  }
  if (!available && !status && !busy) return null;
  return <div className="player-cast-panel playback-menu-actions">
    {error ? <p className="g-warning" role="alert">{error}</p> : null}
    {!status ? <Button icon={<LuminaSpriteIcon name="cast" />} busy={busy} disabled={!available} onClick={() => { void cast(); }} variant="secondary">{busy ? 'Connecting…' : 'Cast to Chromecast'}</Button> : <>
      <p className="playback-menu-status" role="status">{status.playing ? 'Playing' : 'Paused'} on Chromecast · {formatDuration(status.position)}{status.duration ? ` / ${formatDuration(status.duration)}` : ''}</p>
      <div className="g-actions"><Button disabled={busy} onClick={() => { void control((target) => status.playing ? target.pause() : target.play()); }} variant="secondary">{status.playing ? 'Pause Chromecast' : 'Resume Chromecast'}</Button><Button disabled={busy} onClick={() => { void returnHere(); }} variant="secondary">Return playback here</Button></div>
      <Field label={<span style={{ color: 'var(--g-overlay-ink)' }}>Chromecast position in seconds</span>}>{(ids) => <Input {...fieldProps(ids)} defaultValue={Math.floor(status.position)} disabled={busy} max={status.duration || undefined} min={0} onBlur={(event) => { const position = Number(event.currentTarget.value); if (Number.isFinite(position) && position >= 0 && position <= (status.duration || 604800)) void control((target) => target.seek(position)); }} style={{ color: 'var(--g-overlay-ink)' }} type="number" />}</Field>
    </>}
  </div>;
}
