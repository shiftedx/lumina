/* The offline worker owns one small, explicit shell cache. Audio and lyrics live in IndexedDB instead. */
const SHELL_VERSION = '1';
const SHELL_PREFIX = `lumina-offline-shell-v${SHELL_VERSION}`;
const META_CACHE = `${SHELL_PREFIX}-meta`;
const READY_KEY = '/__lumina_offline_shell_ready__';
const ENTRY_PATH = '/music-offline.html';
const MAX_SHELL_RESOURCES = 256;

const STATIC_PREFIXES = ['/assets/', '/fonts/', '/src/', '/node_modules/.vite/deps/', '/node_modules/vite/dist/'];
const STATIC_PATHS = new Set([ENTRY_PATH, '/theme-boot.js', '/favicon.svg', '/favicon.ico', '/apple-touch-icon.png', '/media-mark.svg', '/@vite/client', '/@react-refresh']);
const VITE_ENV_MODULE = /^\/@fs\/(?:[^/]+\/)*node_modules\/vite\/dist\/client\/env\.mjs$/;
const PRIVATE_QUERY_KEYS = /(?:token|auth|credential|cookie|session|ticket|sig|signature|secret|password|api[_-]?key|jwt)/i;

let shellBuild = null;

function safeStaticUrl(value, base) {
  if (!value || value.startsWith('#') || /^(?:data|blob|javascript|mailto):/i.test(value)) return null;
  let url;
  try {
    url = new URL(value, base);
  } catch {
    return null;
  }
  url.hash = '';
  if (url.origin !== self.location.origin || url.username || url.password || url.pathname.startsWith('/api/')) return null;
  if ([...url.searchParams.keys()].some((key) => PRIVATE_QUERY_KEYS.test(key))) return null;
  if (STATIC_PATHS.has(url.pathname) || STATIC_PREFIXES.some((prefix) => url.pathname.startsWith(prefix)) || VITE_ENV_MODULE.test(url.pathname)) return url;
  return null;
}

function referencesIn(text, baseUrl, contentType) {
  const references = new Set();
  const add = (value, pathOnly = false) => {
    if (pathOnly && !/^(?:\/|\.\.?\/)/.test(value)) return;
    const url = safeStaticUrl(value, baseUrl);
    if (pathOnly && url && !STATIC_PATHS.has(url.pathname) && !/\.(?:html|m?js|c?jsx?|tsx?|css|woff2?|svg|png|jpe?g|webp|gif|ico)$/i.test(url.pathname)) return;
    if (url) references.add(url.href);
  };

  if (contentType.includes('html') || /\.html$/i.test(baseUrl.pathname)) {
    const attributes = /\b(?:src|href)\s*=\s*["']([^"']+)["']/gi;
    for (const match of text.matchAll(attributes)) add(match[1]);
  }

  if (contentType.includes('javascript') || /\.m?js$/i.test(baseUrl.pathname)) {
    const imports = /\bimport\s*(?:\(\s*)?["'`]([^"'`]+)["'`]/g;
    for (const match of text.matchAll(imports)) add(match[1], true);
    // The production minifier may remove the space in `from "./chunk.js"`; requiring a
    // non-quote character before `from` avoids treating object keys such as "from" as imports.
    const froms = /(?:^|[^\w"'$])from\s*["'`]([^"'`]+)["'`]/g;
    for (const match of text.matchAll(froms)) add(match[1], true);
  }

  if (contentType.includes('css') || /\.css$/i.test(baseUrl.pathname)) {
    const css = /(?:url|@import)\(\s*["']?([^\)"'\s]+)["']?\s*\)/gi;
    for (const match of text.matchAll(css)) add(match[1]);
  }

  return [...references];
}

async function fetchShellResource(url) {
  if (!safeStaticUrl(url.href, self.location.href)) throw new Error(`Offline shell rejected a non-static resource: ${url.pathname}`);
  const response = await fetch(url.href, { cache: 'no-store', credentials: 'omit' });
  const finalUrl = new URL(response.url || url.href, self.location.origin);
  if (!response.ok || finalUrl.origin !== self.location.origin || !safeStaticUrl(finalUrl.href, self.location.href)) {
    throw new Error(`Offline shell could not fetch ${url.pathname} (HTTP ${response.status}).`);
  }
  return response;
}

async function buildShell() {
  const cacheName = `${SHELL_PREFIX}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
  const cache = await caches.open(cacheName);
  const queue = [new URL(ENTRY_PATH, self.location.origin)];
  const seen = new Set();
  try {
    while (queue.length) {
      const url = queue.shift();
      if (!url || seen.has(url.href)) continue;
      seen.add(url.href);
      if (seen.size > MAX_SHELL_RESOURCES) throw new Error('Offline shell contains too many static resources.');
      const response = await fetchShellResource(url);
      await cache.put(url.href, response.clone());
      const contentType = (response.headers.get('content-type') || '').toLowerCase();
      if (contentType.includes('text/html') || contentType.includes('javascript') || contentType.includes('css') || /\.(?:html|m?js|css)$/i.test(url.pathname)) {
        const text = await response.clone().text();
        for (const reference of referencesIn(text, url, contentType)) {
          if (!seen.has(reference)) queue.push(new URL(reference));
        }
      }
    }

    const meta = await caches.open(META_CACHE);
    await meta.put(READY_KEY, new Response(JSON.stringify({ cacheName, entry: ENTRY_PATH, version: SHELL_VERSION }), {
      headers: { 'Content-Type': 'application/json' },
    }));

    const names = await caches.keys();
    await Promise.all(names
      .filter((name) => name.startsWith(`${SHELL_PREFIX}-`) && name !== META_CACHE && name !== cacheName)
      .map((name) => caches.delete(name))).catch(() => undefined);
    return { cacheName, resources: seen.size };
  } catch (error) {
    await caches.delete(cacheName);
    throw error;
  }
}

function readyCacheName() {
  return caches.open(META_CACHE).then(async (meta) => {
    const response = await meta.match(READY_KEY);
    if (!response) return null;
    let value;
    try { value = await response.json(); } catch { return null; }
    if (!value || value.version !== SHELL_VERSION || typeof value.cacheName !== 'string' || !value.cacheName.startsWith(`${SHELL_PREFIX}-`)) return null;
    return (await caches.keys()).includes(value.cacheName) ? value.cacheName : null;
  });
}

function reply(event, message) {
  const port = event.ports && event.ports[0];
  if (port) {
    port.postMessage(message);
    return;
  }
  if (event.source && typeof event.source.postMessage === 'function') event.source.postMessage(message);
}

self.addEventListener('install', (event) => { event.waitUntil(self.skipWaiting()); });
self.addEventListener('activate', (event) => { event.waitUntil(self.clients.claim()); });

self.addEventListener('message', (event) => {
  if (!event.data || event.data.type !== 'CACHE_OFFLINE_SHELL') return;
  if (!shellBuild) shellBuild = buildShell().finally(() => { shellBuild = null; });
  event.waitUntil(shellBuild.then(
    (result) => reply(event, { type: 'OFFLINE_SHELL_READY', ...result }),
    async () => {
      const retained = await readyCacheName();
      if (retained) reply(event, { type: 'OFFLINE_SHELL_READY', cacheName: retained, retained: true });
      else reply(event, { type: 'OFFLINE_SHELL_ERROR', message: 'The offline listening page could not be prepared. Check the connection and try again.' });
    },
  ));
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== 'GET' || url.origin !== self.location.origin || !safeStaticUrl(url.href, self.location.href)) return;
  event.respondWith((async () => {
    // Online documents see the current build; prepared bytes are the fallback when the server is unreachable.
    try { return await fetch(request); } catch { /* offline: use the prepared shell below */ }
    const cacheName = await readyCacheName();
    if (cacheName) {
      const response = await caches.open(cacheName).then((cache) => cache.match(request));
      if (response) return response;
    }
    return fetch(request);
  })());
});
