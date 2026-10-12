import { expect, inviteMember, OWNER, test } from './realstack';

const WREN = { username: 'wren', password: 'Hollow-cedar-2026' };

test('remember on this browser → sign out → Who\'s watching → instant switch lands on Home; the vault owner needs a password', async ({ page, browser }) => {
  const { context, member } = await inviteMember(page, browser, WREN.username, WREN.password);
  const ringCookie = async () => (await context.cookies()).find((cookie) => cookie.name.endsWith('_ring'));
  const signOut = async () => { await member.getByRole('button', { name: /, account menu$/ }).click(); await member.getByRole('menuitem', { name: 'Sign out' }).click(); };
  /** Drops only this tab's session cookie: the browser keeps its ring, as after a closed laptop lid past the cookie. */
  const leaveSession = async () => { await context.clearCookies({ name: (await ringCookie())!.name.slice(0, -'_ring'.length) }); await member.goto('/'); };
  const signInRemembered = async (username: string, password: string) => {
    await member.getByLabel('Username').fill(username);
    await member.getByLabel('Password', { exact: true }).fill(password);
    await member.getByRole('checkbox', { name: "Show me in Who's watching on this device" }).check();
    await member.getByRole('button', { name: 'Sign in', exact: true }).click();
    await expect(member.getByRole('main', { name: 'Main content' })).toBeVisible();
  };

  await test.step('(1) remember sets an HttpOnly ring cookie on /api/session', async () => {
    await signOut();
    await expect(member.getByRole('heading', { name: 'Welcome home' })).toBeVisible();
    await signInRemembered(WREN.username, WREN.password);
    const ring = await ringCookie();
    expect(ring?.httpOnly).toBe(true);
    expect(ring?.path).toBe('/api/session');
  });

  await test.step('(2) signing out removes the active member', async () => {
    await signOut();
    await expect(member.getByRole('heading', { name: 'Welcome home' })).toBeVisible();
    expect(await ringCookie()).toBeUndefined();
  });

  await test.step('(3) with two remembered members the signed-out screen is the picker', async () => {
    await signInRemembered(WREN.username, WREN.password);
    await leaveSession();
    await expect(member.getByRole('heading', { name: "Who's watching?" })).toBeVisible();
    await member.getByRole('button', { name: 'Someone else' }).click();
    await signInRemembered(OWNER.username, OWNER.password);
    await leaveSession();
    await expect(member.getByRole('heading', { name: "Who's watching?" })).toBeVisible();
  });

  await test.step('(4) the instant tile lands on Home as that member', async () => {
    await member.getByRole('button', { name: 'Continue as Wren' }).click();
    await expect(member.getByRole('main', { name: 'Main content' })).toBeVisible();
    expect((await (await member.request.get('/api/session/me')).json()).user.username).toBe(WREN.username);
  });

  await test.step('(5) the vault owner\'s tile asks for the password', async () => {
    await signOut(); // removes wren; the owner stays remembered
    const owner = member.getByRole('button', { name: `${OWNER.displayName}, vault owner, needs password` });
    await expect(owner).toBeVisible();
    await owner.click();
    await expect(member.getByLabel(`Password for ${OWNER.displayName}`)).toBeFocused();
  });

  await context.close();
});
