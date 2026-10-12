import { createContext, type ReactNode, useCallback, useContext, useEffect, useState } from 'react';

import { type ArrKind, type ArrServer, type ArrTest, getRequestPolicies, getRequestsSettings, listArrServers, type PoliciesResponse, type RequestsSettings, testArrServer } from '../requestsSettingsApi';
import { useAdminResource } from '../../admin/useAdminResource';
import '../requestsSettings.css';

type Loaded<T> = { data: T | null; error: string | null; setData: (next: T | null | ((current: T | null) => T | null)) => void; reload: () => void };
export type RequestsForm = {
  servers: Loaded<ArrServer[]>;
  settings: Loaded<RequestsSettings>;
  policies: Loaded<PoliciesResponse>;
  /** Latest Test connection result per kind: its folders and profiles feed the selects. */
  tests: Partial<Record<ArrKind, ArrTest>>;
  setTest: (kind: ArrKind, test: ArrTest | undefined) => void;
  serverOf: (kind: ArrKind) => ArrServer | undefined;
  putServer: (server: ArrServer) => void;
  removeServer: (id: string) => void;
};

const RequestsContext = createContext<RequestsForm | null>(null);
export function useRequestsForm(): RequestsForm {
  const form = useContext(RequestsContext);
  if (!form) throw new Error('Requests rows render inside the Requests provider.');
  return form;
}

/** Loads the three admin resources once for every Requests row (also inside search results). */
export function RequestsProvider({ children }: { children: ReactNode }) {
  const servers = useAdminResource(listArrServers, 'Unable to load Sonarr and Radarr.');
  const settings = useAdminResource(getRequestsSettings, 'Unable to load Requests settings.');
  const policies = useAdminResource(getRequestPolicies, 'Unable to load request policies.');
  const [tests, setTests] = useState<RequestsForm['tests']>({});
  const setTest = useCallback((kind: ArrKind, test: ArrTest | undefined) => setTests((current) => ({ ...current, [kind]: test })), []);
  const setServers = servers.setData;

  // Saved servers are tested once on load (with their stored key) so the select lists are real, not just the saved ids.
  const [tested, setTested] = useState<string[]>([]);
  useEffect(() => {
    for (const server of servers.data ?? []) {
      if (tested.includes(server.id)) continue;
      setTested((current) => [...current, server.id]);
      testArrServer({ kind: server.kind, base_url: server.base_url, id: server.id }).then((result) => setTest(server.kind, result)).catch(() => undefined);
    }
  }, [servers.data, tested, setTest]);

  const form: RequestsForm = {
    servers, settings, policies, tests, setTest,
    serverOf: (kind) => servers.data?.find((server) => server.kind === kind),
    putServer: (server) => setServers((current) => [...(current ?? []).filter((entry) => entry.id !== server.id), server]),
    removeServer: (id) => setServers((current) => (current ?? []).filter((entry) => entry.id !== id)),
  };
  return <RequestsContext.Provider value={form}>{children}</RequestsContext.Provider>;
}
