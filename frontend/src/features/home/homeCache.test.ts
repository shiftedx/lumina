import { expect, it } from 'vitest';

import { cachedShelf, forgetHomeCache, rememberShelf } from './homeCache';

it('keeps each member apart and forgets everything on sign-out', () => {
  rememberShelf('member-a', 'next_up', ['a']);
  rememberShelf('member-b', 'next_up', ['b']);
  expect(cachedShelf('member-a', 'next_up')).toEqual(['a']);
  expect(cachedShelf('member-b', 'next_up')).toEqual(['b']);
  expect(cachedShelf('member-a', 'live')).toBeUndefined();
  forgetHomeCache();
  expect(cachedShelf('member-a', 'next_up')).toBeUndefined();
  expect(cachedShelf('member-b', 'next_up')).toBeUndefined();
});
