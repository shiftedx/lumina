import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const ownerScript = fileURLToPath(import.meta.url);

export function spawnOwnedCommand(args, { stdio, ...options }) {
  const useGroup = process.platform !== 'win32';
  const child = spawn(process.execPath, useGroup ? [ownerScript, ...args] : args, {
    ...options, detached: useGroup, stdio: useGroup ? [...stdio, 'ipc'] : stdio,
  });
  let commandExit;
  if (useGroup) child.on('message', (message) => { commandExit = message.code; });
  const exited = new Promise((resolve, reject) => {
    child.once('error', reject);
    child.once('close', (code) => resolve(useGroup ? commandExit ?? null : code));
  });
  return { child, exited };
}

export async function stopOwnedChild(child, exited, graceMs = 10_000) {
  if (process.platform !== 'win32') {
    if (child.connected) child.send({ graceMs }, () => {});
    await exited.catch(() => undefined);
    return;
  }
  if (child.exitCode === null && child.signalCode === null) child.kill('SIGINT');
  const killer = setTimeout(() => {
    if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL');
  }, graceMs);
  try {
    await exited.catch(() => undefined);
  } finally {
    clearTimeout(killer);
  }
}

function ownCommand(args) {
  if (!process.connected) throw new Error('An owned test command requires its supervisor IPC channel.');
  // This process leads the detached group and holds its identity until cleanup.
  // Playwright can close its separately managed browsers during the SIGINT grace.
  const signalGroup = (signal) => process.kill(-process.pid, signal);
  process.on('SIGINT', () => {});
  let killer;
  const cancel = (graceMs) => {
    if (killer) return;
    killer = setTimeout(() => signalGroup('SIGKILL'), graceMs);
    signalGroup('SIGINT');
  };
  process.on('SIGTERM', () => cancel(10_000));
  process.on('disconnect', () => cancel(10_000));
  process.on('message', ({ graceMs }) => cancel(graceMs));
  const finish = (code) => {
    const reap = () => signalGroup('SIGKILL');
    if (process.connected) process.send({ code }, reap);
    else reap();
  };
  const command = spawn(process.execPath, args, { stdio: 'inherit' });
  command.once('exit', finish);
  command.once('error', (error) => { console.error(error.message); finish(1); });
}

if (process.argv[1] && path.resolve(process.argv[1]) === ownerScript) {
  ownCommand(process.argv.slice(2));
}
