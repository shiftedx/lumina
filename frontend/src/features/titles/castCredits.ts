type CastCredit = { id?: string | number | null; name: string; role?: string | null; image_url?: string | null };

const normalized = (value: string) => value.normalize('NFKC').trim().replace(/\s+/g, ' ').toLowerCase();

/** Keep billing order and distinct roles, without repeating imported credits for the same performer. */
export function distinctCastCredits<T extends CastCredit>(people: T[]): T[] {
  const idsByName = new Map<string, Set<string>>();
  for (const person of people) {
    if (person.id == null || person.id === '') continue;
    const name = normalized(person.name);
    const ids = idsByName.get(name) ?? new Set<string>();
    ids.add(String(person.id));
    idsByName.set(name, ids);
  }
  const identity = (person: T) => {
    const ids = idsByName.get(normalized(person.name));
    const id = person.id != null && person.id !== '' ? person.id : (ids?.size === 1 ? [...ids][0] : null);
    return id == null ? `name:${normalized(person.name)}` : `id:${id}`;
  };
  const withRoles = new Set(people.filter((person) => normalized(person.role ?? '')).map(identity));
  const credits = new Map<string, T>();
  for (const person of people) {
    const role = normalized(person.role ?? '');
    // An unspecified role is not evidence that someone played an additional character.
    if (!role && withRoles.has(identity(person))) continue;
    const key = JSON.stringify([identity(person), role]);
    const existing = credits.get(key);
    if (!existing) credits.set(key, person);
    else if (!existing.image_url && person.image_url) credits.set(key, { ...existing, image_url: person.image_url });
  }
  return [...credits.values()];
}
