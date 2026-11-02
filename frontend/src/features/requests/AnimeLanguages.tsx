import { Button } from '../../ui';
import { getLibraryLanguages, type EnglishCoverage } from './requestsApi';
import { useLoad } from './requestsContext';

const labels: Record<EnglishCoverage, string> = { available: 'Available', partial: 'Some episodes', missing: 'Missing', unknown: 'Unknown' };

export function AnimeLanguages({ titleId }: { titleId: string }) {
  const result = useLoad(titleId, (signal) => getLibraryLanguages(titleId, signal));
  return <div aria-label="Anime English availability" className="rq-language-evidence" role="status">
    {result.data ? <p className="rq-muted">English dub: {labels[result.data.audio]} · English subtitles: {labels[result.data.subtitles]}</p>
      : result.error ? <p className="rq-muted">English availability unknown. <Button onClick={result.reload} variant="quiet">Retry</Button></p>
        : <p className="rq-muted">Checking English availability…</p>}
  </div>;
}
