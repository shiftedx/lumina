import type { LanguageAvailability } from './requestsApi';
import { languageName } from './RequestSheet';

const tracks = (codes: string[], known: boolean) => [...codes.map(languageName), ...(!known ? ['Unknown'] : [])].join(', ') || 'None recorded';

export function LibraryLanguages({ availability }: { availability: LanguageAvailability }) {
  const { files, total_files, truncated } = availability;
  const sorted = files.map((file, index) => ({ ...file, index })).sort((a, b) => (a.season ?? Infinity) - (b.season ?? Infinity) || (a.episode ?? Infinity) - (b.episode ?? Infinity) || a.index - b.index);
  return <section aria-label="Languages in your library" className="rq-language-evidence">
    <header><h2>Languages in your library</h2><span className="rq-muted">{total_files} {total_files === 1 ? 'file' : 'files'}</span></header>
    {files.length ? <>
      <div className="rq-language-summary">{(['audio', 'subtitle'] as const).map((kind) => {
        const counts = new Map<string, number>();
        for (const file of files) for (const code of new Set(file[`${kind}_languages`])) counts.set(code, (counts.get(code) ?? 0) + 1);
        const known = kind === 'audio' ? 'audio_known' : 'subtitles_known';
        const unknown = files.filter((file) => !file[known]).length;
        const none = files.filter((file) => file[known] && !file[`${kind}_languages`].length).length;
        const rows = [...counts].sort(([a], [b]) => languageName(a).localeCompare(languageName(b))).map(([code, count]) => [languageName(code), count] as const);
        if (unknown) rows.push(['Unknown', unknown]);
        if (none) rows.push(['None recorded', none]);
        return <div key={kind}><h3 className="g-label">{kind === 'audio' ? 'Audio' : 'Subtitles'}</h3><table className="g-table"><tbody>{rows.map(([name, count]) => <tr key={name}><th scope="row">{name}</th><td>{count} / {files.length} files</td></tr>)}</tbody></table></div>;
      })}</div>
      <p className="rq-muted">{truncated ? `Showing language coverage for ${files.length} of ${total_files} files. ` : ''}Files may contain multiple languages.{availability.state !== 'known' ? ' Some language information is unknown.' : ''}</p>
      <details><summary>File details · {files.length} {files.length === 1 ? 'file' : 'files'}</summary><div className="rq-language-files" role="region" aria-label="File language details" tabIndex={0}><table className="g-table"><thead><tr><th scope="col">Episode / file</th><th scope="col">Audio</th><th scope="col">Subtitles</th></tr></thead><tbody>{sorted.map((file) => <tr key={file.index}><th scope="row">{file.season !== null ? `S${file.season}${file.episode !== null ? ` · E${file.episode}` : ''}` : `File ${file.index + 1}`}</th><td>{tracks(file.audio_languages, file.audio_known)}</td><td>{tracks(file.subtitle_languages, file.subtitles_known)}</td></tr>)}</tbody></table></div></details>
    </> : <p className="rq-muted">Language information is unknown.</p>}
  </section>;
}
