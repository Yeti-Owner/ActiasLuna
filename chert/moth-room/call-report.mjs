// Asks Chert what happened on each call (server-side event list) + how fast the API answers.
// Uses the saved CLI login in ~/.chert/credentials.json; sends it ONLY to app.trychert.com; never prints it.
import { readFileSync, readdirSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';

const home = join(homedir(), '.chert');
const creds = JSON.parse(readFileSync(join(home, 'credentials.json'), 'utf8'));
const find = (o, re) => { for (const v of Object.values(o)) { if (typeof v === 'string' && re.test(v)) return v;
  if (v && typeof v === 'object') { const r = find(v, re); if (r) return r; } } return null; };
const token = find(creds, /^ft[uk]_/) || find(creds, /^[A-Za-z0-9._-]{40,}$/);
const project = find(creds, /^[0-9a-f-]{36}$/) || 'e2086115-423d-466a-91b3-17f99ce3abd9';
console.log(`credential fields: ${Object.keys(creds).join(', ')}; token found: ${!!token}`);

async function get(path) {
  const t0 = Date.now();
  try {
    const r = await fetch(`https://app.trychert.com${path}`, { redirect: 'error', signal: AbortSignal.timeout(20000),
      headers: { authorization: `Bearer ${token}`, 'x-chert-project-id': project } });
    const body = await r.text();
    return { status: r.status, ms: Date.now() - t0, body };
  } catch (e) { return { status: 'NO RESPONSE', ms: Date.now() - t0, body: e.message }; }
}

// Every call id we have a journal for, oldest first.
const calls = [];
for (const f of readdirSync(join(home, 'dev'), { withFileTypes: true }).filter(d => d.isDirectory())) {
  try {
    const j = JSON.parse(readFileSync(join(home, 'dev', f.name, 'journal.json'), 'utf8'));
    for (const e of Object.values(j.entries || {})) calls.push({ id: e.call.call_id, at: e.call.deadline_at, video: e.call.media?.video });
  } catch { /* no journal */ }
}
calls.sort((a, b) => a.at - b.at);

const lines = await get('/api/v1/lines');
console.log(`GET /api/v1/lines -> ${lines.status} in ${lines.ms} ms`);
for (const c of calls) {
  const r = await get(`/api/v1/calls/${c.id}`);
  const when = new Date(c.at).toLocaleTimeString();
  if (r.status !== 200) { console.log(`${when} ${c.video ? 'video' : 'audio'} ${c.id.slice(0, 8)} -> ${r.status} (${r.ms} ms)`); continue; }
  const d = JSON.parse(r.body).data || {};
  const ev = (d.facetime_call_events || []).map(e => `${e.event_type}(${e.source})`).join(' > ');
  const extra = ['status', 'end_reason', 'failure_reason', 'reason', 'outcome', 'detail']
    .filter(k => d[k]).map(k => `${k}=${d[k]}`).join(' ');
  console.log(`${when} ${c.video ? 'video' : 'audio'} ${c.id.slice(0, 8)} [${r.ms} ms] ${extra}\n    events: ${ev || '(none)'}`);
}
