import { type CSSProperties, type ReactNode, type RefObject, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';

import { getHeroFraming, type HeroFraming, type HeroFramingRegion } from '../../api';
import './heroFrame.css';

type Size = { width: number; height: number };
export type HeroGeometry = { mode: 'cover'; x: number; y: number; zoom: number; originX: number; originY: number; parallaxScale: number; parallax: number };

const EMPTY: HeroFraming = { state: 'pending' };
const CACHE_LIMIT = 128;
const NEGATIVE_TTL_MS = 60_000;
const FIRST_RESPONSE_HOLD_MS = 150;
const cache = new Map<string, { framing: HeroFraming; expiresAt: number | null }>();
const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
const clamp = (value: number, low: number, high: number) => Math.min(high, Math.max(low, value));

function region(value: unknown): HeroFramingRegion | null {
  if (!value || typeof value !== 'object') return null;
  const candidate = value as Partial<HeroFramingRegion>;
  if ((candidate.kind !== 'face' && candidate.kind !== 'subject') || !finite(candidate.x) || !finite(candidate.y) || !finite(candidate.width) || !finite(candidate.height) || !finite(candidate.weight)) return null;
  if (candidate.x < 0 || candidate.y < 0 || candidate.width <= 0 || candidate.height <= 0 || candidate.x + candidate.width > 1 || candidate.y + candidate.height > 1 || candidate.weight < 0 || candidate.weight > 1) return null;
  return candidate as HeroFramingRegion;
}

/** Treat model output as untrusted: malformed ready data falls back to the whole image. */
export function validFraming(value: unknown): HeroFraming {
  if (!value || typeof value !== 'object') return { state: 'unavailable' };
  const candidate = value as Partial<HeroFraming>;
  if (candidate.state === 'pending' || candidate.state === 'unavailable') return { state: candidate.state };
  if (candidate.state !== 'ready' || !finite(candidate.width) || !finite(candidate.height) || !Number.isInteger(candidate.width) || !Number.isInteger(candidate.height) || candidate.width <= 0 || candidate.height <= 0 || candidate.width > 16_384 || candidate.height > 16_384 || candidate.width * candidate.height > 40_000_000 || !Array.isArray(candidate.regions) || candidate.regions.length > 16) return { state: 'unavailable' };
  const regions = candidate.regions.map(region);
  if (regions.some((item) => item === null)) return { state: 'unavailable' };
  return { state: 'ready', width: candidate.width, height: candidate.height, regions: regions as HeroFramingRegion[] };
}

function remember(src: string, framing: HeroFraming) {
  if (cache.has(src)) cache.delete(src);
  cache.set(src, { framing, expiresAt: framing.state === 'unavailable' ? Date.now() + NEGATIVE_TTL_MS : null });
  if (cache.size > CACHE_LIMIT) cache.delete(cache.keys().next().value!);
}

function cached(src: string): HeroFraming | null {
  const hit = cache.get(src);
  if (!hit) return null;
  if (hit.expiresAt !== null && hit.expiresAt <= Date.now()) { cache.delete(src); return null; }
  if (hit.framing.state === 'ready') { cache.delete(src); cache.set(src, hit); }
  return hit.framing;
}

/** Full-bleed framing: protect heads first, then fit generic subject proposals above the text fade. */
export function heroGeometry(raw: unknown, viewport: Size, placement: 'home' | 'title' = 'title'): HeroGeometry {
  const framing = validFraming(raw);
  const fallback: HeroGeometry = { mode: 'cover', x: 50, y: 15, zoom: 1.025, originX: 50, originY: 15, parallaxScale: 1, parallax: 0 };
  if (framing.state !== 'ready' || !viewport.width || !viewport.height) return fallback;
  const faces = framing.regions!.filter((item) => item.kind === 'face');
  const subjects = framing.regions!.filter((item) => item.kind === 'subject');
  const regions = faces.length ? faces : subjects;
  if (!regions.length) return fallback;
  const strongest = Math.max(...regions.map((item) => item.weight));
  const material = regions.filter((item) => item.weight >= strongest * .55);
  const bounds = material.map((item) => ({
    left: clamp(item.x - item.width * .1, 0, 1), right: clamp(item.x + item.width * 1.1, 0, 1),
    top: clamp(item.y - item.height * (faces.length ? .45 : .07), 0, 1),
    bottom: clamp(item.y + item.height * (faces.length ? 1.2 : 1.03), 0, 1),
  }));
  const left = Math.min(...bounds.map((item) => item.left));
  const right = Math.max(...bounds.map((item) => item.right));
  const top = Math.min(...bounds.map((item) => item.top));
  const bottom = Math.max(...bounds.map((item) => item.bottom));
  const sourceAspect = framing.width! / framing.height!;
  const viewportAspect = viewport.width / viewport.height;
  const cropWidth = Math.min(1, viewportAspect / sourceAspect);
  const cropHeight = Math.min(1, sourceAspect / viewportAspect);
  const weight = material.reduce((total, item) => total + item.weight * item.width * item.height, 0);
  const focus = (axis: 'x' | 'y') => weight ? material.reduce((total, item) => total + (item[axis] + item[axis === 'x' ? 'width' : 'height'] / 2) * item.weight * item.width * item.height, 0) / weight : .5;
  const preferredX = placement === 'home' && viewport.width >= 600 ? .68 : .5;
  const xLow = Math.max(0, right - cropWidth);
  const xHigh = Math.min(1 - cropWidth, left);
  const cropLeft = xLow <= xHigh ? clamp(focus('x') - cropWidth * preferredX, xLow, xHigh) : clamp(focus('x') - cropWidth * preferredX, 0, 1 - cropWidth);
  const yLow = Math.max(0, bottom - cropHeight * .9);
  const yHigh = Math.min(1 - cropHeight, top);
  // If a group cannot fit, retain its upper edge rather than slice through its heads.
  const cropTop = yLow <= yHigh ? clamp(focus('y') - cropHeight * .42, yLow, yHigh) : clamp(top, 0, 1 - cropHeight);
  // Zoom around the protected group, stopping before any padded edge leaves the frame.
  const visibleBounds = [(left - cropLeft) / cropWidth, (right - cropLeft) / cropWidth, (top - cropTop) / cropHeight, (bottom - cropTop) / cropHeight];
  const originX = clamp((visibleBounds[0] + visibleBounds[1]) / 2, 0, 1);
  const originY = clamp((visibleBounds[2] + visibleBounds[3]) / 2, 0, 1);
  const limit = (low: number, high: number, origin: number) => Math.min(low < origin ? origin / (origin - low) : Infinity, high > origin ? (1 - origin) / (high - origin) : Infinity);
  const zoom = clamp(Math.min(limit(visibleBounds[0], visibleBounds[1], originX), limit(visibleBounds[2], visibleBounds[3], originY)), 1, 1.06);
  // Share the same safe crop budget with camera zoom. Reserve coverage even after that animation ends.
  const parallaxScale = 1 + (zoom - 1) / 2;
  const combinedZoom = 1 + (zoom - 1) * .75;
  const parallax = Math.max(0, Math.min(24, viewport.height * (parallaxScale - 1) * originY,
    viewport.height * (1 - originY - (visibleBounds[3] - originY) * combinedZoom)));
  return { mode: 'cover', x: cropWidth === 1 ? 50 : cropLeft / (1 - cropWidth) * 100, y: cropHeight === 1 ? 50 : cropTop / (1 - cropHeight) * 100,
    zoom: parallax > .5 ? combinedZoom / parallaxScale : zoom, originX: originX * 100, originY: originY * 100,
    parallaxScale: parallax > .5 ? parallaxScale : 1, parallax: parallax > .5 ? parallax : 0 };
}

/** Event-driven only: one coalesced write per scroll frame, with no React updates or idle loop. */
function useHeroParallax(root: RefObject<HTMLElement | null>, active: boolean, distance: number, src?: string | null) {
  useLayoutEffect(() => {
    if (!active || !distance || !root.current) return;
    const node = root.current;
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
    let frame = 0;
    const update = () => {
      frame = 0;
      const rect = node.getBoundingClientRect();
      const progress = rect.height ? clamp(-rect.top / rect.height, 0, 1) : 0;
      node.style.setProperty('--hero-parallax-y', `${progress * distance}px`);
    };
    const scroll = () => { if (!frame) frame = requestAnimationFrame(update); };
    const stop = () => { window.removeEventListener('scroll', scroll); cancelAnimationFrame(frame); frame = 0; };
    const sync = () => {
      stop();
      if (!reduced.matches) { update(); window.addEventListener('scroll', scroll, { passive: true }); }
    };
    sync();
    reduced.addEventListener('change', sync);
    return () => { stop(); reduced.removeEventListener('change', sync); };
  }, [active, distance, root, src]);
}

function useHeroFraming(src: string | null | undefined, enabled: boolean, root: RefObject<HTMLElement | null>, placement: 'home' | 'title') {
  const [visible, setVisible] = useState(false);
  const [pageVisible, setPageVisible] = useState(() => document.visibilityState !== 'hidden');
  const [size, setSize] = useState<Size>({ width: 0, height: 0 });
  const [entry, setEntry] = useState<{ src: string | null; framing: HeroFraming; revealed: boolean }>({ src: null, framing: EMPTY, revealed: false });
  const entryRef = useRef(entry);
  entryRef.current = entry;
  const hit = src ? cached(src) : null;
  const initial = { src: src ?? null, framing: hit ?? EMPTY, revealed: !src || !enabled || hit !== null };
  // During a source swap, derive a fresh presentation synchronously so the previous hero's crop cannot flash.
  const presentation = !src || !enabled ? initial : entry.src === src ? entry : initial;
  useLayoutEffect(() => {
    const update = () => setPageVisible(document.visibilityState !== 'hidden');
    document.addEventListener('visibilitychange', update);
    return () => document.removeEventListener('visibilitychange', update);
  }, []);
  useLayoutEffect(() => {
    if (!enabled || !root.current) return;
    const node = root.current;
    const update = () => { const rect = node.getBoundingClientRect(); setSize({ width: rect.width, height: rect.height }); };
    update();
    const resize = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(update);
    resize?.observe(node);
    const observer = typeof IntersectionObserver === 'undefined' ? null : new IntersectionObserver(([entry]) => setVisible(entry?.isIntersecting ?? false));
    if (observer) observer.observe(node); else setVisible(true);
    return () => { resize?.disconnect(); observer?.disconnect(); };
  }, [enabled, root]);
  useEffect(() => {
    if (!src || !enabled || !visible || !pageVisible) return;
    const showing = entryRef.current;
    const preserveVisibleCrop = showing.src === src && showing.revealed;
    const cachedFraming = cached(src);
    if (cachedFraming?.state === 'ready') {
      if (!preserveVisibleCrop) setEntry({ src, framing: cachedFraming, revealed: true });
      return;
    }
    if (cachedFraming?.state === 'unavailable') {
      if (!preserveVisibleCrop) setEntry({ src, framing: EMPTY, revealed: true });
      return;
    }
    let cancelled = false;
    const controller = new AbortController();
    const reveal = preserveVisibleCrop ? undefined : window.setTimeout(() => {
      if (!cancelled) setEntry((current) => current.src === src && !current.revealed ? { ...current, revealed: true } : current);
    }, FIRST_RESPONSE_HOLD_MS);
    if (!preserveVisibleCrop) setEntry({ src, framing: EMPTY, revealed: false });
    getHeroFraming(src, { signal: controller.signal }).then(validFraming).catch(() => ({ state: 'unavailable' as const })).then((next) => {
      if (cancelled) return;
      if (next.state === 'ready' || next.state === 'unavailable') remember(src, next);
      // The first response wins the visible crop. A later ready answer only warms the cache for the next visit.
      setEntry((current) => current.src === src && !current.revealed ? { src, framing: next.state === 'ready' ? next : EMPTY, revealed: true } : current);
      if (reveal !== undefined) window.clearTimeout(reveal);
    });
    return () => { cancelled = true; controller.abort(); if (reveal !== undefined) window.clearTimeout(reveal); };
  }, [enabled, pageVisible, src, visible]);
  return { framing: presentation.framing, geometry: useMemo(() => heroGeometry(presentation.framing, size, placement), [placement, presentation.framing, size]), revealed: presentation.revealed, active: enabled && visible && pageVisible };
}

export function HeroFrame({ children, className = '', enabled = true, placement = 'title', src }: { children: ReactNode; className?: string; enabled?: boolean; placement?: 'home' | 'title'; src?: string | null }) {
  const root = useRef<HTMLSpanElement>(null);
  const { framing, geometry, revealed, active } = useHeroFraming(src, enabled, root, placement);
  useHeroParallax(root, active && revealed, geometry.parallax, src);
  const style = { '--hero-focus-x': `${geometry.x}%`, '--hero-focus-y': `${geometry.y}%`, '--hero-zoom': geometry.zoom, '--hero-origin-x': `${geometry.originX}%`, '--hero-origin-y': `${geometry.originY}%`, '--hero-parallax-scale': geometry.parallaxScale } as CSSProperties;
  return <span className={`hero-frame ${className} is-cover`} data-hero-active={active} data-hero-motion={geometry.zoom > 1} data-hero-parallax={geometry.parallax > 0} data-hero-framing={framing.state} data-hero-revealed={revealed} ref={root} style={style}>
    <span className="hero-frame-foreground" key={src}>{children}</span>
  </span>;
}
