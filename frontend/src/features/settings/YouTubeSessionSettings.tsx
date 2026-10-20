import { useEffect, useRef, useState } from 'react';
import { Upload } from 'lucide-react';
import { requestJson } from '../../api';
import { Button, ButtonLink } from '../../ui';
import { useSettingsHost } from './settingsHost';

type Connection = { status: 'not_connected' | 'connected' | 'needs_reconnect'; updated_at: string | null };
const URL = '/api/me/youtube-session';
const MAX_BYTES = 256 * 1024;

export function YouTubeSessionSettings() {
  const { user } = useSettingsHost();
  return <PersonalYouTubeSession key={user.id} />;
}

function PersonalYouTubeSession() {
  const [connection, setConnection] = useState<Connection | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const mounted = useRef(false);
  const input = useRef<HTMLInputElement>(null);
  const request = useRef<AbortController | null>(null);
  useEffect(() => {
    mounted.current = true;
    const controller = new AbortController();
    setError(null);
    void requestJson<Connection>(URL, { signal: controller.signal }).then(setConnection).catch(() => {
      if (mounted.current && !controller.signal.aborted) setError('Your YouTube connection could not load. Try again.');
    });
    return () => { mounted.current = false; controller.abort(); request.current?.abort(); };
  }, [reload]);

  function clearFile() { setFile(null); setConsent(false); if (input.current) input.current.value = ''; }
  async function save(disconnect = false) {
    if (busy || (!disconnect && (!file || !consent))) return;
    if (file && file.size > MAX_BYTES && !disconnect) { setError('Choose a cookie file smaller than 256 KB.'); return; }
    setBusy(true); setError(null); setNotice(null);
    const controller = new AbortController(); request.current = controller;
    try {
      const cookies = disconnect ? null : await file!.text();
      if (!mounted.current) return;
      const result = await requestJson<Connection>(URL, {
        method: disconnect ? 'DELETE' : 'PUT', signal: controller.signal,
        ...(cookies !== null ? { body: JSON.stringify({ cookies, consent: true }), headers: { 'Content-Type': 'application/json' } } : {}),
      });
      if (!mounted.current) return;
      setConnection(result); clearFile();
      setNotice(disconnect ? 'YouTube disconnected. Lumina deleted its saved session and stopped playback using it.' : 'Session saved. YouTube checks access when you play a video.');
    } catch (problem) {
      if (mounted.current && !controller.signal.aborted) setError(problem instanceof Error ? problem.message : 'YouTube could not be connected. Try again.');
    } finally { if (mounted.current) setBusy(false); }
  }

  return <div className="g-subrows settings-youtube-session">
    <p>Your session is encrypted on this server and belongs only to you. YouTube checks access separately for each video.</p>
    {connection ? <p role="status">{connection.status === 'connected' ? 'Session saved' : connection.status === 'needs_reconnect' ? 'Needs reconnecting — choose a fresh cookie file.' : 'Not connected'}</p> : !error ? <p role="status">Loading your connection…</p> : null}
    <details><summary>How to connect safely</summary>
      <p>Sign in to YouTube in a separate private browser session, then export that session’s YouTube cookies as a Netscape-format cookies.txt file. This is a browser session export, not a Google password or API key.</p>
      <p>Treat the file like a password. Google may restrict accounts used with third-party tools. Sessions can expire, and connecting does not grant access to videos your account cannot watch.</p>
      <ButtonLink href="https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies" target="_blank" rel="noreferrer noopener" variant="quiet">YouTube session export guide</ButtonLink>
    </details>
    {connection ? <form className="g-subrows" onSubmit={(event) => { event.preventDefault(); void save(); }}>
      <label htmlFor="youtube-session-file">YouTube cookie file</label>
      <input accept=".txt,text/plain" disabled={busy} hidden id="youtube-session-file" onChange={(event) => { setFile(event.target.files?.[0] ?? null); setConsent(false); setError(null); }} ref={input} type="file" />
      <div className="g-actions settings-file-picker">
        <Button aria-label="Choose YouTube cookie file" disabled={busy} icon={<Upload />} onClick={() => input.current?.click()}>Choose file</Button>
        <span aria-live="polite" title={file?.name}>{file?.name ?? 'No file selected'}</span>
      </div>
      <label className="g-check"><input checked={consent} disabled={busy} onChange={(event) => setConsent(event.target.checked)} type="checkbox" /> Allow Lumina to use this session for my YouTube playback.</label>
      <div className="settings-inline-actions"><Button disabled={busy || !file || !consent} type="submit">{busy ? 'Saving…' : connection.status === 'not_connected' ? 'Connect YouTube' : 'Replace session'}</Button>
        {connection.status !== 'not_connected' ? <Button disabled={busy} onClick={() => void save(true)} variant="quiet">Disconnect YouTube</Button> : null}</div>
    </form> : error ? <Button onClick={() => setReload((value) => value + 1)} variant="quiet">Try again</Button> : null}
    <p className="settings-hint">Downloads and discovery stay anonymous. Some videos and signed-in live streams still need to open on YouTube. Disconnect removes Lumina’s copy; it does not sign you out of Google.</p>
    {notice ? <p role="status">{notice}</p> : null}{error ? <p role="alert">{error}</p> : null}
  </div>;
}
