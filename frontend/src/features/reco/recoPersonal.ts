/**
 * Whether a served list is personal. With the kill switch off the server
 * sends no annotation at all, and the legacy policy ignores "Show fewer", so the menu leaves it out. A surface wraps its
 * list in the scope; the default is personal.
 */
import { createContext, useContext } from 'react';

const Context = createContext(true);
export const RecoPersonalScope = Context.Provider;
export const useRecoPersonal = (): boolean => useContext(Context);
/** A list that came back with items and none annotated is the legacy policy's. An empty list says nothing. */
export const isPersonal = (items: readonly { reco?: unknown }[]): boolean => !items.length || items.some((item) => item.reco);
