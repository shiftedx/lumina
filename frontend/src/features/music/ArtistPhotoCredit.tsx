import type { MusicTitleInfo } from './musicTypes';

export function ArtistPhotoCredit({ music, imageType = 'Primary' }: { music?: MusicTitleInfo | null; imageType?: 'Primary' | 'Backdrop' }) {
  const credit = music?.artwork_credits?.find((item) => item.image_type === imageType);
  if (!credit || !/^https:\/\/(commons\.wikimedia\.org|en\.wikipedia\.org)\/wiki\//.test(credit.source_url)) return null;
  return <p className="music-note"><a href={credit.source_url} rel="noreferrer noopener" target="_blank">{['Photo', credit.author, credit.license].filter(Boolean).join(' · ')}</a></p>;
}
