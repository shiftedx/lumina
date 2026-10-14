import { Checkbox } from '../../ui';
import { BOOK_SECTION_IDS, expandLegacyBookSections, sectionDisplayLabel, type Section } from './memberAccess';

type LibraryGroup = 'Watch' | 'Listen' | 'Read' | 'Other files';
const GROUPS: readonly LibraryGroup[] = ['Watch', 'Listen', 'Read', 'Other files'];

function groupFor(id: string): LibraryGroup {
  if (id === 'movies' || id === 'shows' || id === 'anime') return 'Watch';
  if (id === 'music' || id.startsWith('music:')) return 'Listen';
  if (id === 'books' || id.startsWith('books:')) return 'Read';
  return 'Other files';
}

function orderedSelection(sections: readonly Section[], selected: ReadonlySet<string>): string[] {
  const ordered = sections.map((section) => section.id).filter((id, index, all) => selected.has(id) && all.indexOf(id) === index);
  for (const id of selected) if (!ordered.includes(id)) ordered.push(id);
  return ordered;
}

function distinctLabels(sections: readonly Section[]): Map<string, string> {
  const bases = sections.map((section) => sectionDisplayLabel(section));
  const counts = new Map<string, number>();
  for (const base of bases) counts.set(base, (counts.get(base) ?? 0) + 1);
  const used = new Set<string>();
  return new Map(sections.map((section, index) => {
    const base = bases[index];
    if ((counts.get(base) ?? 0) < 2) {
      used.add(base);
      return [section.id, base];
    }
    const detail = section.description?.trim() || `${section.count.toLocaleString()} files`;
    let label = `${base} — ${detail}`;
    if (used.has(label)) label = `${label} (folder ${index + 1})`;
    used.add(label);
    return [section.id, label];
  }));
}

export function LibraryChoices({ sections, selected, onChange }: { sections: readonly Section[]; selected: readonly string[]; onChange: (next: string[]) => void }) {
  const entries = sections;
  const hasBookLeaves = entries.some((section) => section.id === 'books:ebook' || section.id === 'books:manga');
  const chosen = new Set(hasBookLeaves ? expandLegacyBookSections(selected) : selected);
  const labels = distinctLabels(entries);

  function toggle(id: string, on: boolean) {
    const next = new Set(chosen);
    if (id === 'books' && hasBookLeaves) {
      for (const leaf of BOOK_SECTION_IDS) {
        if (on) next.add(leaf);
        else next.delete(leaf);
      }
    } else if (on) next.add(id);
    else next.delete(id);
    onChange(orderedSelection(entries, next));
  }

  return (
    <div className="g-control-stack">
      <p className="g-setting-note">Only shared items are included. Private items stay private.</p>
      {GROUPS.map((group) => {
        const groupSections = entries.filter((section) => groupFor(section.id) === group);
        if (!groupSections.length) return null;
        return (
          <fieldset className="g-fieldset" key={group}>
            <legend>{group}</legend>
            <div className="g-setting-options">
              {groupSections.map((section) => {
                const noun = section.id.startsWith('books:') ? 'shared books' : section.id === 'music' ? 'albums' : section.id.startsWith('root:') || section.id === 'vault' ? 'files' : 'titles';
                const hint = [`${section.count.toLocaleString()} ${noun}`, section.description?.trim()].filter(Boolean).join(' · ');
                return <Checkbox checked={section.id === 'books' ? selected.includes('books') : chosen.has(section.id)} hint={hint} key={section.id} label={labels.get(section.id) ?? sectionDisplayLabel(section)} onChange={(event) => toggle(section.id, event.currentTarget.checked)} />;
              })}
            </div>
          </fieldset>
        );
      })}
      {!selected.length ? <p className="g-setting-note" role="status">No library is shared: this member sees nothing in the Library.</p> : null}
    </div>
  );
}
