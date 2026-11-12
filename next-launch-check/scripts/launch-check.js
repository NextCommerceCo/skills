#!/usr/bin/env node
'use strict';
// next-launch-check: test a campaign funnel, a storefront, or a page against
// its design, from a URL. Runs on your machine. Reads pages with plain GETs,
// optionally renders them in Chromium with order calls blocked and vendor
// beacons held back, and writes report.md + results.json. Nothing is uploaded.
const fs = require('fs');
const path = require('path');
const { crawl } = require('./lib/crawl');
const P = require('./lib/page');
const R = require('./lib/rules');
const report = require('./lib/report');
const U = require('./lib/util');
const H = require('./lib/html');

const ROOT = path.join(__dirname, '..');
const VERSION = (/^version:\s*([^\s]+)/m.exec(fs.readFileSync(path.join(ROOT, 'SKILL.md'), 'utf8')) || [])[1] || '0.0.0';
const MANUAL = JSON.parse(fs.readFileSync(path.join(ROOT, 'references', 'manual-checklist.json'), 'utf8'));

const SOURCE_FAMILIES = {
  funnel: ['http', 'funnel-chain', 'sdk', 'tracking', 'legal-links', 'links', 'content', 'seo-meta', 'media', 'storefront-domain', 'pagespeed', 'pr-review'],
  storefront: ['http', 'storefront-nav', 'tracking', 'legal-links', 'links', 'content', 'seo-meta', 'media', 'storefront-domain', 'pagespeed'],
  design: ['http'],
};
// Families a campaigns-os verdict already proves for the same build.
const CAMPAIGNS_OS_COVERS = new Set(['http', 'funnel-chain', 'legal-links', 'param-propagation', 'param-toggles', 'browser-runtime', 'order']);

const USAGE = `next-launch-check ${VERSION}

Usage:
  node launch-check.js env
  node launch-check.js funnel <url> [--page <url>]... [options]
  node launch-check.js storefront <url> [options]
  node launch-check.js design <url> [--figma-css <file>] [--frame <png>]... [--width <px>]
  node launch-check.js record <checkout-url> [--confirm-test-params] [--rules <file>] [--out <dir>]
  node launch-check.js finalize <run-dir> --answers <answers.json>

Options:
  --browser                 also run the browser lane (needs Playwright)
  --pagespeed               ask Google PageSpeed Insights (optional PAGESPEED_API_KEY)
  --rules <file>            your QA rules: JSON, or a Markdown checklist
  --diff <file>             review a pull request diff (gh pr diff > pr.diff)
  --campaigns-os-verdict <file>  fold in a campaigns-os QA verdict for the same build
  --mask-ids                mask tracking ids in the report
  --max-pages <n>           pages to crawl (default 12)
  --no-follow               only the pages you name
  --param key=value         extra parameter to test for carry-through (repeatable)
  --live-beacons            let analytics and ad beacons through (default: held back)
  --out <dir>               output folder (default ./next-launch-check-output/<host>-<time>)
  --json                    print results.json to stdout as well
`;

function parseArgs(argv) {
  const o = { _: [], pages: [], frames: [], params: {} };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    const val = () => { const v = argv[++i]; if (v === undefined) throw new Error(`${a} needs a value`); return v; };
    switch (a) {
      case '--page': o.pages.push(val()); break;
      case '--frame': o.frames.push(val()); break;
      case '--param': { const [k, ...v] = val().split('='); o.params[k] = v.join('='); break; }
      case '--browser': o.browser = true; break;
      case '--pagespeed': o.pagespeed = true; break;
      case '--mask-ids': o.maskIds = true; break;
      case '--no-follow': o.follow = false; break;
      case '--live-beacons': o.liveBeacons = true; break;
      case '--confirm-test-params': o.confirmTestParams = true; break;
      case '--headless': o.headless = true; break;
      case '--json': o.json = true; break;
      case '--rules': o.rules = val(); break;
      case '--diff': o.diff = val(); break;
      case '--campaigns-os-verdict': o.campaignsOsVerdict = val(); break;
      case '--figma-css': o.figmaCss = val(); break;
      case '--width': o.width = parseInt(val(), 10); break;
      case '--max-pages': o.maxPages = parseInt(val(), 10); break;
      case '--out': o.out = val(); break;
      case '--answers': o.answers = val(); break;
      case '--record-minutes': o.recordMinutes = parseFloat(val()); break;
      case '-h': case '--help': o.help = true; break;
      default:
        if (a.startsWith('--')) throw new Error(`unknown option ${a}`);
        o._.push(a);
    }
  }
  return o;
}

function checkUrl(u) {
  let url;
  try { url = new URL(u); } catch (_) { throw new Error(`not a URL: ${u}`); }
  if (!/^https?:$/.test(url.protocol)) throw new Error(`only http and https URLs: ${u}`);
  return url.href;
}

function envInfo() {
  const PW = require('./browser/playwright');
  const cwd = process.cwd();
  const exists = (p) => fs.existsSync(path.join(cwd, p));
  return {
    tool: 'next-launch-check', version: VERSION, node: process.version,
    playwright: PW.locate(),
    campaigns_os: {
      package: exists('node_modules/@nextcommerce/campaigns-os'),
      build_packet: exists('campaign-runtime.build.json') ? 'campaign-runtime.build.json' : null,
    },
    pagespeed_key: !!process.env.PAGESPEED_API_KEY,
  };
}

async function sourceLane(ctx, families) {
  const out = [];
  for (const f of families) {
    const fn = require(`./lib/checks/${f}`);
    try { out.push(...(await fn(ctx))); } catch (e) {
      out.push(U.assertion({ id: `${f}:error`, family: f, status: 'skipped', severity: 'info', message: `This family crashed and was skipped: ${String(e.message || e).slice(0, 200)}` }));
    }
  }
  return out;
}

function storefrontPages(home, max) {
  const urls = [];
  const seen = new Set([U.pathKey(home.finalUrl)]);
  for (const a of home.anchors) {
    const region = H.regionAt(home.html, a.index);
    if (!['header', 'nav', 'footer'].includes(region)) continue;
    const u = U.resolve(a.href, home.finalUrl);
    if (!u || !U.sameOrigin(u, home.finalUrl)) continue;
    const k = U.pathKey(u);
    if (seen.has(k)) continue;
    seen.add(k);
    urls.push(u);
    if (urls.length >= max - 1) break;
  }
  return urls;
}

function manualFor(mode, rules) {
  const base = mode === 'storefront' ? MANUAL.storefront : mode === 'design' ? MANUAL.design : MANUAL.funnel;
  return [...base, ...rules.manual].map((m) => ({ ...m, answer: null, note: null }));
}

function foldCampaignsOs(assertions, file) {
  const v = JSON.parse(fs.readFileSync(file, 'utf8'));
  const disposition = v.disposition || (v.verdict && v.verdict.disposition) || 'unknown';
  const marked = assertions.map((a) => (CAMPAIGNS_OS_COVERS.has(a.family) ? { ...a, deferred: true } : a));
  const status = disposition === 'ready' ? 'pass' : disposition === 'ready_with_exceptions' ? 'warn' : 'fail';
  marked.push(U.assertion({ id: 'campaigns-os:verdict', family: 'campaigns-os', status, severity: 'blocker', message: `campaigns-os QA verdict: ${disposition}. It is the authority for routes, policy links, parameters, runtime and orders on this build.`, evidence: { path: file, run_id: v.run_id || null, assertions: Array.isArray(v.assertions) ? v.assertions.length : null } }));
  return { assertions: marked, info: { path: file, disposition, run_id: v.run_id || null } };
}

async function runCheck(mode, url, o) {
  const started = new Date();
  const rules = R.load(o.rules);
  const outDir = path.resolve(o.out || path.join('next-launch-check-output', `${U.hostSlug(url)}-${U.timestamp(started)}`));
  fs.mkdirSync(path.join(outDir, 'screenshots'), { recursive: true });
  const cache = new Map();
  const loadPage = async (u) => { const k = U.pathKey(u); if (!cache.has(k)) cache.set(k, await P.load(u)); return cache.get(k); };
  let pages;
  if (mode === 'storefront') {
    const home = await loadPage(url);
    const extra = home.ok ? storefrontPages(home, o.maxPages || 15) : [];
    pages = [home];
    for (const u of [...o.pages, ...extra]) { const p = await loadPage(u); p.via = 'navigation link'; pages.push(p); }
    home.via = 'entry';
  } else if (mode === 'design') {
    const p = await loadPage(url); p.via = 'entry'; pages = [p];
  } else {
    const c = await crawl(url, { pages: o.pages, maxPages: o.maxPages || 12, follow: o.follow !== false, load: loadPage });
    pages = c.pages;
  }
  const ctx = {
    mode, entryUrl: url, pages, outDir, rules, loadPage,
    options: { ...o, pages: o.pages },
    showId: o.maskIds ? U.maskId : (x) => x,
  };
  const lanes = { source: 'ran' };
  let assertions = await sourceLane(ctx, SOURCE_FAMILIES[mode]);
  if (mode === 'design') {
    assertions.push(...(await require('./browser/design').run(ctx)));
    lanes.browser = 'ran';
  } else if (o.browser) {
    const mod = mode === 'storefront' ? require('./browser/storefront') : require('./browser/funnel');
    const rows = await mod.run(ctx);
    lanes.browser = rows.some((r) => /unavailable/.test(r.id)) ? 'skipped (no Playwright)' : 'ran';
    assertions.push(...rows);
  } else {
    lanes.browser = 'not requested';
  }
  if (mode === 'funnel') lanes.order = 'not run (use `record`, or campaigns-os for a scripted order)';
  let campaignsOs = null;
  if (o.campaignsOsVerdict) ({ assertions, info: campaignsOs } = foldCampaignsOs(assertions, o.campaignsOsVerdict));
  assertions = R.applyByDesign(assertions, rules);
  const manual = manualFor(mode, rules);
  const automated = report.automatedDisposition(assertions);
  const result = {
    schema: 'next-launch-check/results/v1',
    tool_version: VERSION,
    run_id: `${U.timestamp(started)}-${Math.random().toString(36).slice(2, 6)}`,
    mode, entry_url: url,
    started_at: started.toISOString(), finished_at: new Date().toISOString(),
    options: { browser: !!o.browser, pagespeed: !!o.pagespeed, rules: rules.source, diff: o.diff || null, mask_ids: !!o.maskIds, live_beacons: !!o.liveBeacons, max_pages: o.maxPages || null },
    lanes,
    pages: pages.map((p) => ({ label: p.label, url: p.url, final_url: p.finalUrl, status: p.status, type: p.kind.type, type_source: p.kind.source, title: p.title, via: p.via, sdk_version: p.sdk.version || null })),
    assertions,
    counts: report.counts(assertions),
    disposition_automated: automated,
    disposition: report.finalDisposition(automated, manual),
    manual,
    campaigns_os: campaignsOs,
  };
  report.write(outDir, result);
  return { result, outDir };
}

async function runRecord(url, o) {
  const rec = require('./browser/record');
  const rules = R.load(o.rules);
  const gate = rec.paramGate(url, { confirmed: !!o.confirmTestParams, allowed: rules.allowedParams });
  if (!gate.ok) { const e = new Error(gate.reason); e.code = 3; throw e; }
  const started = new Date();
  const outDir = path.resolve(o.out || path.join('next-launch-check-output', `${U.hostSlug(url)}-${U.timestamp(started)}-order`));
  fs.mkdirSync(path.join(outDir, 'screenshots'), { recursive: true });
  const ctx = { entryUrl: url, outDir, options: { ...o, autodrive: o.autodrive }, rules };
  const { assertions } = await rec.record(ctx);
  const manual = MANUAL.funnel.filter((m) => ['F-10', 'F-16', 'F-17', 'F-18'].includes(m.id)).map((m) => ({ ...m, answer: null, note: null }));
  const automated = report.automatedDisposition(assertions);
  const result = {
    schema: 'next-launch-check/results/v1', tool_version: VERSION, run_id: `${U.timestamp(started)}-order`, mode: 'record', entry_url: url,
    started_at: started.toISOString(), finished_at: new Date().toISOString(), options: { live_beacons: !!o.liveBeacons, affiliate_params: gate.found.map(([k]) => k) },
    lanes: { order: 'recorded (tester drove)' },
    pages: [], assertions, counts: report.counts(assertions), disposition_automated: automated, disposition: report.finalDisposition(automated, manual), manual, campaigns_os: null,
  };
  report.write(outDir, result);
  return { result, outDir };
}

function finalize(dir, answersFile) {
  const file = path.join(dir, 'results.json');
  const result = JSON.parse(fs.readFileSync(file, 'utf8'));
  const answers = JSON.parse(fs.readFileSync(answersFile, 'utf8'));
  const list = Array.isArray(answers) ? answers : Object.entries(answers).map(([id, v]) => (typeof v === 'string' ? { id, answer: v } : { id, ...v }));
  for (const a of list) {
    const m = result.manual.find((x) => x.id === a.id);
    if (!m) throw new Error(`no manual item ${a.id}`);
    if (!['pass', 'fail', 'na'].includes(a.answer)) throw new Error(`${a.id}: answer must be pass, fail or na`);
    m.answer = a.answer;
    m.note = a.note || null;
  }
  result.disposition = report.finalDisposition(result.disposition_automated, result.manual);
  result.finished_at = new Date().toISOString();
  report.write(dir, result);
  return { result, outDir: dir };
}

async function main(argv) {
  let o;
  try { o = parseArgs(argv); } catch (e) { process.stderr.write(`${e.message}\n\n${USAGE}`); return 2; }
  const [cmd, target] = o._;
  if (o.help || !cmd) { process.stdout.write(USAGE); return cmd ? 0 : 2; }
  try {
    let done;
    if (cmd === 'env') { process.stdout.write(JSON.stringify(envInfo(), null, 2) + '\n'); return 0; }
    if (cmd === 'finalize') { if (!target || !o.answers) throw new Error('finalize needs <run-dir> and --answers <file>'); done = finalize(path.resolve(target), o.answers); }
    else if (cmd === 'record') { done = await runRecord(checkUrl(target || ''), o); }
    else if (['funnel', 'storefront', 'design'].includes(cmd)) { done = await runCheck(cmd, checkUrl(target || ''), o); }
    else throw new Error(`unknown command ${cmd}`);
    const r = done.result;
    process.stdout.write(`${r.disposition.toUpperCase()}  (automated: ${r.disposition_automated})\n`);
    process.stdout.write(`${r.counts.pass} pass · ${r.counts.fail} fail · ${r.counts.warn} warn · ${r.counts.manual_review} to check · ${r.counts.skipped} skipped · manual ${r.manual.filter((m) => m.answer).length}/${r.manual.length}\n`);
    process.stdout.write(`Report: ${path.join(done.outDir, 'report.md')}\n`);
    if (o.json) process.stdout.write(JSON.stringify(r, null, 2) + '\n');
    return r.disposition === 'blocked' ? 4 : 0;
  } catch (e) {
    process.stderr.write(`next-launch-check: ${e.message}\n`);
    return e.code === 3 ? 3 : 2;
  }
}

if (require.main === module) main(process.argv.slice(2)).then((code) => { process.exitCode = code; });

module.exports = { main, parseArgs, runCheck, finalize, foldCampaignsOs, VERSION };
