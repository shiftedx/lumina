/**
 * A channel's avatar: the proxied image (only Lumina's own /api/ URLs), else the serif
 * monogram. Sizes 32 (bylines), 40 (watch page), 88/96 (tiles, phone header), 120 (channel header). Decoration only:
 * the channel's name is always written beside it.
 */
import { type CSSProperties, useState } from 'react';

import { displayInitials } from '../../luminaModel';
import { remoteArt } from './remoteModel';
import './channelAvatar.css';

export type ChannelAvatarProps = { name: string; url?: string | null; size: 32 | 40 | 88 | 96 | 120; live?: boolean; className?: string };

export function ChannelAvatar({ name, url, size, live = false, className }: ChannelAvatarProps) {
  const art = remoteArt(url);
  const [failed, setFailed] = useState<string | null>(null);
  const style = { width: size, height: size, '--g-avatar-size': `${size}px` } as CSSProperties;
  return (
    <span aria-hidden="true" className={`g-avatar${live ? ' is-live' : ''}${className ? ` ${className}` : ''}`} style={style}>
      {art && failed !== art.url ? <img alt="" decoding="async" loading="lazy" onError={() => setFailed(art.url)} src={art.url} /> : <span className="g-avatar-monogram">{displayInitials(name)}</span>}
    </span>
  );
}
