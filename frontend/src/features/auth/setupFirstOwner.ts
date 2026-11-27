import { createInitialAdmin, getBootstrapStatus, loginSession } from '../../api';

export async function setupFirstOwner(
  payload: Parameters<typeof createInitialAdmin>[0],
  loadAuthenticated: () => Promise<unknown>,
): Promise<'ready' | 'sign-in'> {
  let accountCreated = false;
  try {
    await createInitialAdmin(payload);
    accountCreated = true;
    await loginSession({ username: payload.username, password: payload.password });
    await loadAuthenticated();
    return 'ready';
  } catch (error) {
    // The POST may have committed even when the browser never received its response.
    if (accountCreated || (await getBootstrapStatus({ timeoutMs: 3500 }).catch(() => null))?.needs_setup === false) {
      return 'sign-in';
    }
    throw error;
  }
}
