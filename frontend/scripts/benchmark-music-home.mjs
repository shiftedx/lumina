import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { arch, cpus, platform, release } from 'node:os';
import { execFileSync } from 'node:child_process';
import { chromium } from '@playwright/test';

const [fixturePath, outputPath, sampleCount = '5'] = process.argv.slice(2);
const runs = Number(sampleCount);
assert(fixturePath && outputPath && Number.isInteger(runs) && runs > 0,
  'Usage: node frontend/scripts/benchmark-music-home.mjs Fixture.json output.json [samples per viewport]');
const fixture = JSON.parse(await readFile(fixturePath, 'utf8'));
const origin = new URL(fixture.serverURL);
assert.equal(origin.hostname, '127.0.0.1', 'Use a disposable loopback proof server');
// The owner seed contains the Wait queue. Use the second member for timing
// so benchmarking leaves that queue available for workflow tests.
const username = fixture.musicHomeMemberUsername;
const password = fixture.musicHomeMemberPassword;
assert(username && password, 'Launch the proof server with LUMINA_MUSIC_HOME_FIXTURE=1');
const album = 'Signals from a Quiet Observatory';
const first = 'First Light in the Listening Room';
const second = 'A Signal Answered at Dawn';
const views = {
  desktop: { width: 1440, height: 900 },
  phone: { width: 390, height: 844 },
};

function installMarkers() {
  window.musicBenchmark = { intents: {}, output: [] };
  const originalStart = AudioBufferSourceNode.prototype.start;
  AudioBufferSourceNode.prototype.start = function (...args) {
    const result = originalStart.apply(this, args);
    if (this.buffer) {
      const delay = Math.max(0, (args[0] ?? 0) - this.context.currentTime);
      window.musicBenchmark.output.push({ at: performance.now(), delay, kind: 'buffer-scheduled' });
    }
    return result;
  };

  const originalPlay = HTMLMediaElement.prototype.play;
  HTMLMediaElement.prototype.play = function (...args) {
    this.addEventListener('playing', () => {
      window.musicBenchmark.output.push({ at: performance.now(), delay: 0, kind: 'media-playing' });
    }, { once: true });
    return originalPlay.apply(this, args);
  };
}

async function timePlayback(page, button, intent, title) {
  await button.evaluate((node, name) => node.addEventListener('click', () => {
    window.musicBenchmark.intents[name] = performance.now();
  }, { once: true, capture: true }), intent);
  await button.click();
  await page.getByRole('button', { name: `Open listening room for ${title}`, exact: true }).waitFor();
  const handle = await page.waitForFunction((name) => {
    const { intents, output } = window.musicBenchmark;
    const sentAt = intents[name];
    const marker = output.find(({ at, delay }) => at >= sentAt && delay < 0.05);
    return marker && { ms: marker.at - sentAt, marker: marker.kind };
  }, intent);
  return handle.jsonValue();
}

async function verifyDownload(page, context) {
  const player = page.getByRole('region', { name: 'Music player' });
  await player.getByRole('button', { name: `Open listening room for ${second}`, exact: true }).click();
  const room = page.getByRole('dialog', { name: 'Listening room' });
  await room.getByRole('button', { name: 'Save album', exact: true }).click();
  await room.getByRole('button', { name: 'Track available offline', exact: true }).waitFor();
  await page.goto('/music-offline.html');
  const savedTrack = page.getByRole('button', { name: `Play ${first}`, exact: true });
  await savedTrack.waitFor();
  await page.waitForFunction(() => navigator.serviceWorker.controller);
  await context.setOffline(true);
  await page.reload({ waitUntil: 'domcontentloaded' });
  await savedTrack.click();
  try {
    await page.getByRole('button', { name: 'Pause music', exact: true }).waitFor({ timeout: 8_000 });
  } catch (error) {
    throw new Error(`Offline playback did not start: ${await page.getByRole('main').innerText()}`, { cause: error });
  }
}

// Chrome's AAC decoder matches the proof album and the installed browser
// used by the repository's real playback benchmarks.
const browser = await chromium.launch({ channel: 'chrome', args: ['--disable-audio-output'] });
const report = {
  source: execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim(),
  browser: browser.version(),
  host: { platform: platform(), release: release(), arch: arch(), cpu: cpus()[0].model },
  fixture_album: album,
  measurement: {
    browse_ready_ms: 'Document navigation start to actionable fixture album on /audio?view=albums',
    first_audio_ms: 'Track Play click to immediate Web Audio scheduling or HTML media playing event',
    next_track_ms: 'Manual Next click to immediate successor scheduling or media playing event',
  },
  samples: [],
};
try {
  for (const [view, viewport] of Object.entries(views)) {
    for (let run = 1; run <= runs; run += 1) {
      const context = await browser.newContext({ baseURL: origin.origin, viewport });
      try {
        const login = await context.request.post('/api/session/login', { data: { username, password } });
        assert(login.ok(), `Fixture sign-in failed with HTTP ${login.status()}`);
        await context.addInitScript(installMarkers);
        const page = await context.newPage();
        await page.goto('/audio?view=albums', { waitUntil: 'commit' });
        const albumCard = page.getByRole('group', { name: 'Albums', exact: true })
          .getByRole('button', { name: new RegExp(`^${album} by `) });
        await albumCard.waitFor();
        const browseReady = await page.evaluate(() => performance.now());
        await albumCard.press('Enter');
        await page.getByRole('heading', { level: 1, name: album }).waitFor();
        const firstTrack = page.getByRole('button', { name: new RegExp(`^Play 1\\. ${first}(?:,|$)`) });
        const firstAudio = await timePlayback(page, firstTrack, 'start', first);
        const next = await timePlayback(page, page.getByRole('region', { name: 'Music player' })
          .getByRole('button', { name: 'Next track', exact: true }), 'next', second);
        report.samples.push({ view, run, browse_ready_ms: browseReady, first_audio_ms: firstAudio.ms,
          first_audio_marker: firstAudio.marker, next_track_ms: next.ms, next_track_marker: next.marker });
        await writeFile(outputPath, JSON.stringify(report, null, 2) + '\n');
        if (view === 'phone' && run === runs) {
          await verifyDownload(page, context);
          report.offline_smoke = 'Saved fixture album, reloaded standalone shell with network disabled, started saved track';
        }
      } finally {
        await context.close();
      }
    }
  }
} finally {
  await browser.close();
}
await writeFile(outputPath, JSON.stringify(report, null, 2) + '\n');
