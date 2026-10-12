/** The builders produce what the server would: keys its event schema accepts, exploration where the server puts it. */
import { describe, expect, it } from 'vitest';

import { pickedSnapshot, RECO_KEY, recoEntry, recoTitle, remoteKey, suppression, titleKey } from './recoFixtures';

describe('recommendation fixtures', () => {
  it('give every item a key the event endpoint accepts', () => {
    for (const key of [remoteKey('v1'), remoteKey('a much longer id than thirty-two characters'), remoteKey(''), titleKey(7), recoTitle(3).reco!.key, recoEntry('x').reco!.key]) {
      expect(key).toMatch(RECO_KEY);
    }
    expect(remoteKey('v1')).not.toBe(remoteKey('v2'));
  });

  it('put exploration in the last two of every twelve positions', () => {
    const slots = pickedSnapshot(24).items.map((item) => item.reco!.slot);
    expect(slots.flatMap((slot, index) => (slot === 'explore' ? [index + 1] : []))).toEqual([11, 12, 23, 24]);
    expect(pickedSnapshot(20).items.map((item) => item.reco!.position)).toEqual(Array.from({ length: 20 }, (_, index) => index));
  });

  it('date a show-fewer suppression 140 days out', () => {
    const { created_at, recovers_at } = suppression('fewer');
    expect((Date.parse(recovers_at!) - Date.parse(created_at)) / 86_400_000).toBe(140);
    expect(suppression('channel').recovers_at).toBeNull();
  });
});
