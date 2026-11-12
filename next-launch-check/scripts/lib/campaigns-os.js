'use strict';
// Runs Campaigns OS spec-aware QA (`campaigns-os qa run`, the engine behind
// the public next-campaigns-qa skill) when the tester has a Campaigns OS
// project, and imports its verdict rows into the launch-check checklist.
// It needs one of: a Build Packet, a built Page Kit folder plus its template
// family, or a saved Map id plus its exported CampaignSpec. Without those,
// campaigns-os has no bare-URL mode, so this lane is skipped.
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');
const { assertion, pageLabel } = require('./util');

const PIN = '@nextcommerce/campaigns-os@1.68.0';

// Our families that a non-skipped campaigns-os family already proves.
const DEFERS = {
  'funnel-flow': ['http', 'funnel-chain'],
  'browser-test-order': ['order'],
};

function detect(o, cwd = process.cwd()) {
  if (o.campaignsOs === false) return { run: false, reason: 'off (--no-campaigns-os)' };
  const packet = o.packet || (fs.existsSync(path.join(cwd, 'campaign-runtime.build.json')) ? 'campaign-runtime.build.json' : null);
  if (packet) return { run: true, kind: 'packet', args: ['--packet', packet] };
  if (o.site) {
    if (!o.family) return { run: false, reason: '--site needs --family (the starter-template family, e.g. olympus)' };
    return { run: true, kind: 'site', args: ['--site', o.site, '--family', o.family, ...(o.slug ? ['--slug', o.slug] : [])], needsBase: true };
  }
  if (o.mapId) {
    if (!o.spec) return { run: false, reason: '--map-id needs --spec (the CampaignSpec exported from Map Builder)' };
    return { run: true, kind: 'map', args: [o.mapId, '--spec', o.spec], needsBase: true };
  }
  return { run: false, reason: 'no Campaigns OS project (Build Packet, --site + --family, or --map-id + --spec)' };
}

function command(o, cwd = process.cwd()) {
  if (o.campaignsOsBin) return [o.campaignsOsBin, []];
  const local = path.join(cwd, 'node_modules', '.bin', process.platform === 'win32' ? 'campaigns-os.cmd' : 'campaigns-os');
  if (fs.existsSync(local)) return [local, []];
  return [process.platform === 'win32' ? 'npx.cmd' : 'npx', ['--yes', PIN]];
}

function newest(dir) {
  const found = [];
  const walk = (d) => { for (const e of fs.existsSync(d) ? fs.readdirSync(d, { withFileTypes: true }) : []) { const p = path.join(d, e.name); if (e.isDirectory()) walk(p); else if (e.name.endsWith('.json')) found.push({ p, t: fs.statSync(p).mtimeMs }); } };
  walk(dir);
  return found.sort((a, b) => b.t - a.t)[0]?.p || null;
}

function run(det, { baseUrl, outDir, o }) {
  return new Promise((resolve) => {
    const verdictDir = path.join(outDir, 'campaigns-os');
    fs.mkdirSync(verdictDir, { recursive: true });
    const [bin, pre] = command(o);
    const args = [...pre, 'qa', 'run', ...det.args];
    if (baseUrl && (det.needsBase || o.campaignsOsBaseUrl)) args.push('--base-url', baseUrl);
    if (o.browser !== false) args.push('--browser');
    if (o.campaignsOsOrders) args.push('--test-order', o.campaignsOsOrders);
    args.push('--no-post-verdict', '--output-dir', verdictDir);
    const started = Date.now();
    const child = spawn(bin, args, { cwd: process.cwd(), env: process.env, stdio: ['ignore', 'pipe', 'pipe'] });
    let log = '';
    child.stdout.on('data', (d) => { log += d; });
    child.stderr.on('data', (d) => { log += d; });
    const timer = setTimeout(() => child.kill('SIGKILL'), (o.campaignsOsMinutes || 20) * 60000);
    const done = (code) => {
      clearTimeout(timer);
      fs.writeFileSync(path.join(verdictDir, 'campaigns-os.log'), log);
      resolve({ code, verdict: newest(verdictDir), command: [path.basename(bin), ...args].join(' '), seconds: Math.round((Date.now() - started) / 1000), tail: log.trim().split('\n').slice(-4).join(' ') });
    };
    child.on('error', (e) => { log += String(e.message); done(-1); });
    child.on('close', done);
  });
}

// Campaign Map page ids → launch-check page types.
function roleOf(pageId) {
  const p = String(pageId || '').toLowerCase();
  if (!p || ['campaign', 'run'].includes(p)) return null;
  if (/receipt|thank|confirm/.test(p)) return 'receipt';
  if (/upsell|downsell|oto|offer/.test(p)) return 'upsell';
  if (/checkout|select|cart/.test(p)) return 'checkout';
  if (/presell|advertorial|listicle|story|article/.test(p)) return 'presell';
  return 'landing';
}

const STATUS = { pass: 'pass', fail: 'fail', warn: 'warn', manual_review: 'manual_review', incomplete: 'manual_review', skipped: 'skipped', carried_forward: 'pass' };

function describe(a) {
  if (a.message) return String(a.message);
  const noise = /^(unknown|no_prior_run|carried_forward)$/;
  const parts = [a.cause_reason, a.cause].filter((x) => x && !noise.test(String(x)));
  const actual = a.actual === undefined || a.actual === null ? '' : typeof a.actual === 'string' ? a.actual : JSON.stringify(a.actual);
  return [parts.join(': '), actual].filter(Boolean).join(' — ').slice(0, 300) || a.status;
}

function importVerdict(file) {
  const v = JSON.parse(fs.readFileSync(file, 'utf8'));
  const rows = [];
  for (const a of v.assertions || []) {
    const status = STATUS[a.status] || 'skipped';
    const severity = ['blocker', 'warn', 'info'].includes(a.severity) ? a.severity : status === 'fail' ? 'blocker' : 'info';
    const label = a.page && !['campaign', 'run'].includes(a.page) ? `cos:${a.page}` : null;
    const row = assertion({ id: `cos:${a.family}:${a.id}`, family: `cos-${a.family}`, page: label, url: a.url || null, status, severity, message: `${describe(a)}${a.page && label ? '' : ''}`, evidence: a.expected !== undefined ? { expected: a.expected, actual: a.actual } : undefined });
    row.role = roleOf(a.page);
    if (a.waiver) row.waived = `campaigns-os waiver: ${a.waiver.reason || ''} (${a.waiver.waived_by || 'unknown'})`;
    rows.push(row);
  }
  const disposition = v.disposition || 'unknown';
  rows.push(assertion({ id: 'campaigns-os:verdict', family: 'campaigns-os', status: disposition === 'ready' ? 'pass' : disposition === 'ready_with_exceptions' ? 'warn' : 'fail', severity: 'blocker', message: `Campaigns OS QA verdict: ${disposition} (${(v.assertions || []).length} rows, run ${v.run_id || '?'}).`, evidence: { path: file, run_id: v.run_id || null, base_url: v.base_url || null } }));
  const emitted = new Set((v.assertions || []).filter((a) => a.status !== 'skipped').map((a) => a.family));
  const defer = new Set([...emitted].flatMap((f) => DEFERS[f] || []));
  return { rows, info: { path: file, disposition, run_id: v.run_id || null, families: [...emitted] }, defer };
}

module.exports = { detect, run, importVerdict, roleOf, command, PIN };
void pageLabel;
