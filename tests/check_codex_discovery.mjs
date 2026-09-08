/** A real app-server discovery probe. No thread, turn, or model is started. */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { resolve } from 'node:path';
import { createInterface } from 'node:readline';
import { fileURLToPath } from 'node:url';

const NAME = 'ros2-engineering-skills';

export function validateListing(result, cwd, expectedPath) {
  assert.ok(result && Array.isArray(result.data), 'Missing skills/list data');
  const rows = result.data.filter(row => row.cwd === cwd);
  assert.equal(rows.length, 1, 'Exactly one requested workspace must be returned');
  assert.deepEqual(rows[0].errors, [], 'Discovery reported errors');
  assert.ok(Array.isArray(rows[0].skills), 'Missing skills array');
  const matches = rows[0].skills.filter(skill => skill.name === NAME);
  if (expectedPath === 'absent') {
    assert.equal(matches.length, 0, 'Negative control unexpectedly discovered the skill');
    return { status: 'pass', scope: 'discovery only', present: false };
  }
  assert.equal(matches.length, 1, 'Expected exactly one installed skill');
  const skill = matches[0];
  assert.equal(skill.enabled, true, 'Installed skill must be enabled');
  assert.equal(typeof skill.path, 'string', 'Missing resolved skill path');
  assert.equal(resolve(skill.path), resolve(expectedPath), 'Wrong skill copy was discovered');
  assert.ok(typeof skill.description === 'string' && skill.description.length > 0);
  return { status: 'pass', scope: 'discovery only', present: true, skill };
}

export function probe(cwd, expectedPath) {
  return new Promise((accept, reject) => {
    const proc = spawn('codex', ['app-server'], {
      cwd, stdio: ['pipe', 'pipe', 'inherit'], env: process.env,
    });
    const lines = createInterface({ input: proc.stdout });
    let finished = false;
    let result;
    let failure;
    const timeout = setTimeout(() => finish(new Error('Discovery timed out')), 45000);
    const send = message => proc.stdin.write(JSON.stringify(message) + '\n');
    function finish(error, value) {
      if (finished) return;
      finished = true;
      failure = error;
      result = value;
      clearTimeout(timeout);
      lines.close();
      proc.stdin.end();
      proc.kill('SIGTERM');
      setTimeout(() => proc.kill('SIGKILL'), 2000).unref();
    }
    proc.on('error', error => finish(error));
    proc.stdin.on('error', error => { if (!finished) finish(error); });
    proc.on('close', () => {
      clearTimeout(timeout);
      if (failure) reject(failure);
      else if (!finished) reject(new Error('App server exited before discovery completed'));
      else accept(result);
    });
    lines.on('line', line => {
      if (finished) return;
      try {
        const message = JSON.parse(line);
        if (message.id !== 0 && message.id !== 1) return;
        if (message.error) throw new Error(JSON.stringify(message.error));
        if (message.id === 0) {
          send({ method: 'initialized', params: {} });
          send({ method: 'skills/list', id: 1, params: { cwds: [cwd], forceReload: true } });
        } else {
          finish(null, validateListing(message.result, cwd, expectedPath));
        }
      } catch (error) { finish(error); }
    });
    send({ method: 'initialize', id: 0, params: {
      clientInfo: { name: 'ros2_skill_discovery_check', version: '1.0.0' },
    } });
  });
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const [cwd, expectedPath] = process.argv.slice(2);
  if (!cwd || !expectedPath) {
    console.error('Usage: node tests/check_codex_discovery.mjs WORKSPACE SKILL_PATH|absent');
    process.exitCode = 2;
  } else {
    try { console.log(JSON.stringify(await probe(resolve(cwd), expectedPath), null, 2)); }
    catch (error) { console.error(error.message); process.exitCode = 1; }
  }
}
