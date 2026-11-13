#!/usr/bin/env node
'use strict';
// next-launch-check: QA a campaign page or funnel, or a storefront, from a URL.
// You say what you are testing (presell, landing, checkout, upsell, the full
// funnel, or a storefront). The script reads the pages, renders them in
// Chromium (order calls blocked, vendor beacons held back), runs a speed
// test, and fills the QA checklist for those page types: each item passes or
// fails automatically, shows evidence for you to judge, or waits for your
// answer. It writes report.md + results.json locally and uploads nothing.
const fs = require('fs');
const path = require('path');
const { crawl } = require('./lib/crawl');
const P = require('./lib/page');
const R = require('./lib/rules');
const report = require('./lib/report');
const CL = require('./lib/checklist');
const U = require('./lib/util');
const H = require('./lib/html');
const { isCommerce } = require('./lib/detect');
const COS = require('./lib/campaigns-os');

const ROOT = path.join(__dirname, '..');
const VERSION = (/^version:\s*([^\s]+)/m.exec(fs.readFileSync(path.join(ROOT, 'SKILL.md'), 'utf8')) || [])[1] || '0.0.0';
const SCOPES = ['presell', 'landing', 'checkout', 'upsell', 'funnel', 'storefront'];
const ALL_ROLES = ['presell', 'landing', 'checkout', 'upsell', 'receipt', 'storefront'];

const FAMILIES = {
  funnel: ['http', 'funnel-chain', 'sdk', 'tracking', 'legal-links', 'contact', 'links', 'content', 'seo-meta', 'media', 'storefront-domain', 'speed', 'pr-review'],
  storefront: ['http', 'storefront-nav', 'tracking', 'legal-links', 'links', 'content', 'seo-meta', 'media', 'storefront-domain', 'speed'],
  design: ['http'],
};
const DESIGN_ITEMS = [
  { id: 'design-layout', section: 'Design review', text: 'Side by side with the design: layout, spacing and imagery match at desktop width', roles: ['design'], severity: 'warn', auto: ['design-parity:screens-*'] },
  { id: 'design-typography', section: 'Design review', text: 'Text styles (font, size, weight) match the design', roles: ['design'], severity: 'warn', auto: ['design-parity:typography'] },
  { id: 'design-colours', section: 'Design review', text: 'Colours match the design', roles: ['design'], severity: 'warn', auto: ['design-parity:colours'] },
  { id: 'design-mobile', section: 'Design review', text: 'The mobile layout matches the design', roles: ['design'], severity: 'warn' },
  { id: 'design-copy', section: 'Design review', text: 'Copy matches the design word for word, including prices and claims', roles: ['design'], severity: 'warn' },
];

const USAGE = `next-launch-check ${VERSION}

Usage:
  node launch-check.js env
  node launch-check.js check <url> --scope <presell|landing|checkout|upsell|funnel|storefront> [options]
  node launch-check.js speed <url> [--page <url>]...
  node launch-check.js design <url> [--figma-css <file>] [--frame <png>]... [--width <px>]
  node launch-check.js order <checkout-url> --into <run-dir> [--orders standard:accept,3ds:decline@2] [--confirm-test-params]
  node launch-check.js record <checkout-url> [--into <run-dir>] [--confirm-test-params] [--rules <file>]
  node launch-check.js finalize <run-dir> --answers <answers.json>
  (funnel <url> and storefront <url> are short for check --scope funnel|storefront)

Options:
  --scope <type>            what you are testing (default: funnel)
  --page <url>              another page of the funnel, in order (repeatable)
  --no-browser              skip the browser lane (on by default when Playwright is installed)
  --no-speed                skip the speed test (on by default)
  --speed-pages <n>         pages to speed-test in a full funnel (default 3)
  --rules <file>            your QA rules: JSON, or a Markdown checklist
  --diff <file>             review a pull request diff (gh pr diff > pr.diff)
  Campaigns OS (runs campaigns-os qa and adds its results to the checklist):
  --packet <file>           Build Packet (found automatically as ./campaign-runtime.build.json)
  --site <dir> --family <f> [--slug <s>]   a built Page Kit folder and its template family
  --map-id <id> --spec <file>              a saved Map id and its exported CampaignSpec
  --campaigns-os-base-url <url>  the deployed campaign root, if not the URL being tested
  --campaigns-os-orders <common|checkout|accept|decline|full>  place typed test-card orders (ask first)
  --no-campaigns-os         skip it
  --campaigns-os-verdict <file>  fold in a verdict from an earlier campaigns-os run instead
  --mask-ids                mask tracking ids in the report
  --max-pages <n>           pages to crawl (default 12)
  --param key=value         extra parameter to test for carry-through (repeatable)
  --live-beacons            let analytics and ad beacons through (default: held back)
  --out <dir>               output folder (default ./next-launch-check-output/<host>-<time>)

  Test orders (order: typed by the script; record: typed by you). Real test orders, ask first:
  --orders <plans>          card[:accept|decline|accept-decline|decline-accept][@package], comma-separated
                            cards: standard (6011 1111 1111 1117), 3ds (6011 0009 9013 9424). Default standard:accept
  --max-orders <n>          refuse more than this many orders (default 4)
  --email <addr> --first-name --last-name --phone --country --address1 --city --province --postal
                            test customer (default a US address and qa+launchcheck@example.com)
  --headed                  show the browser window
  --dry-run                 rehearse: fill everything, stop before submit (no order is sent)
  --no-pdf                  skip report.pdf (report.html and report.md are always written)
  --json                    print results.json to stdout as well
`;

function parseArgs(argv) {
  const o = { _: [], pages: [], frames: [], params: {}, browser: true, speed: true };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    const val = () => { const v = argv[++i]; if (v === undefined) throw new Error(`${a} needs a value`); return v; };
    switch (a) {
      case '--scope': o.scope = val(); if (!SCOPES.includes(o.scope)) throw new Error(`--scope must be one of ${SCOPES.join(', ')}`); break;
      case '--page': o.pages.push(val()); break;
      case '--frame': o.frames.push(val()); break;
      case '--param': { const [k, ...v] = val().split('='); o.params[k] = v.join('='); break; }
      case '--browser': o.browser = true; break;
      case '--no-browser': o.browser = false; break;
      case '--speed': case '--pagespeed': o.speed = true; break;
      case '--no-speed': o.speed = false; break;
      case '--speed-pages': o.speedPages = parseInt(val(), 10); break;
      case '--mask-ids': o.maskIds = true; break;
      case '--no-follow': o.follow = false; break;
      case '--live-beacons': o.liveBeacons = true; break;
      case '--confirm-test-params': o.confirmTestParams = true; break;
      case '--headless': o.headless = true; break;
      case '--json': o.json = true; break;
      case '--no-pdf': o.pdf = false; break;
      case '--rules': o.rules = val(); break;
      case '--diff': o.diff = val(); break;
      case '--campaigns-os-verdict': o.campaignsOsVerdict = val(); break;
      case '--no-campaigns-os': o.campaignsOs = false; break;
      case '--packet': o.packet = val(); break;
      case '--site': o.site = val(); break;
      case '--family': o.family = val(); break;
      case '--slug': o.slug = val(); break;
      case '--map-id': o.mapId = val(); break;
      case '--spec': o.spec = val(); break;
      case '--campaigns-os-base-url': o.campaignsOsBaseUrl = val(); break;
      case '--campaigns-os-orders': o.campaignsOsOrders = val(); break;
      case '--campaigns-os-bin': o.campaignsOsBin = val(); break;
      case '--figma-css': o.figmaCss = val(); break;
      case '--width': o.width = parseInt(val(), 10); break;
      case '--max-pages': o.maxPages = parseInt(val(), 10); break;
      case '--out': o.out = val(); break;
      case '--into': o.into = val(); break;
      case '--answers': o.answers = val(); break;
      case '--record-minutes': o.recordMinutes = parseFloat(val()); break;
      case '--orders': o.orders = val(); break;
      case '--max-orders': o.maxOrders = parseInt(val(), 10); break;
      case '--headed': o.headed = true; break;
      case '--dry-run': o.dryRun = true; break;
      case '--email': o.email = val(); break;
      case '--first-name': o.firstName = val(); break;
      case '--last-name': o.lastName = val(); break;
      case '--phone': o.phone = val(); break;
      case '--country': o.country = val(); break;
      case '--address1': o.address1 = val(); break;
      case '--city': o.city = val(); break;
      case '--province': o.province = val(); break;
      case '--postal': o.postal = val(); break;
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
    campaigns_os: { package: exists('node_modules/@nextcommerce/campaigns-os'), build_packet: exists('campaign-runtime.build.json') ? 'campaign-runtime.build.json' : null },
    pagespeed_key: !!process.env.PAGESPEED_API_KEY,
  };
}

// Page types for the checklist. In a full funnel, content pages before the
// checkout are presell pages except the last, which is the landing page; the
// 0.4 `product` type is a landing page.
function assignRoles(pages, scope) {
  if (scope === 'storefront') { for (const p of pages) { p.role = 'storefront'; p.in_scope = true; } return []; }
  const firstCommerce = pages.findIndex((p) => isCommerce(p));
  const before = pages.filter((p, i) => !isCommerce(p) && (firstCommerce < 0 || i < firstCommerce));
  for (const p of pages) {
    if (!p.ok) { p.role = null; p.in_scope = scope === 'funnel'; continue; } // a page that did not load is checked by `http` only
    if (isCommerce(p)) p.role = p.kind.type;
    else if (p.kind.type === 'product') p.role = 'landing';
    else if (before.includes(p)) p.role = before.indexOf(p) === before.length - 1 && before.length > 1 ? 'landing' : before.length === 1 ? 'landing' : 'presell';
    else p.role = 'landing';
    p.in_scope = scope === 'funnel';
  }
  const notes = [];
  if (scope !== 'funnel') {
    const entry = pages[0];
    const detected = entry.role;
    entry.role = scope;
    entry.in_scope = true;
    const looksCommerce = isCommerce(entry);
    const mismatch = (['checkout', 'upsell'].includes(scope) && entry.kind.type !== scope) || (['presell', 'landing'].includes(scope) && looksCommerce);
    if (entry.ok && mismatch) notes.push(U.assertion({ id: 'scope:entry', family: 'scope', page: entry.label, url: entry.url, status: 'warn', severity: 'info', message: `You said this page is a ${scope}, but it looks like a ${detected} page (${entry.kind.source}). Checks follow what you said.` }));
  }
  return notes;
}

function storefrontPages(home, max) {
  const urls = [];
  const seen = new Set([U.pathKey(home.finalUrl)]);
  for (const a of home.anchors) {
    if (!['header', 'nav', 'footer'].includes(H.regionAt(home.html, a.index))) continue;
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

async function sourceLane(ctx, families) {
  const out = [];
  for (const f of families) {
    const fn = require(`./lib/checks/${f}`);
    try { out.push(...(await fn(ctx))); } catch (e) {
      out.push(U.assertion({ id: `${f}:error`, family: f, status: 'skipped', severity: 'info', message: `This check crashed and was skipped: ${String(e.message || e).slice(0, 200)}` }));
    }
  }
  return out;
}

// Import a campaigns-os verdict: its rows join the checklist, and our rows in
// the families it proved are marked deferred to it.
function foldCampaignsOs(rows, file) {
  const imp = COS.importVerdict(file);
  const marked = rows.map((a) => (imp.defer.has(a.family) ? { ...a, deferred: true } : a));
  return { assertions: [...marked, ...imp.rows], info: imp.info };
}

function extraItems(rules, scopeRoles) {
  return rules.manual.map((m) => ({ id: m.id, section: m.section || 'Your rules', text: m.text, roles: scopeRoles, severity: m.severity || 'warn' }));
}

function finish(result, rows, pagesForChecklist, { rules, prior = [], extra = [] }) {
  result.assertions = rows;
  result.counts = report.counts(rows);
  result.disposition_automated = report.automatedDisposition(rows);
  result.checklist = CL.build(pagesForChecklist, rows, { prior, extra: [...extra, ...extraItems(rules, ALL_ROLES.concat('design'))] });
  result.disposition = CL.disposition(result.checklist, result.disposition_automated);
  return result;
}

async function runCheck(scope, url, o) {
  const started = new Date();
  const rules = R.load(o.rules);
  const mode = scope === 'design' ? 'design' : scope === 'storefront' ? 'storefront' : 'funnel';
  const outDir = path.resolve(o.out || path.join('next-launch-check-output', `${U.hostSlug(url)}-${U.timestamp(started)}`));
  fs.mkdirSync(path.join(outDir, 'screenshots'), { recursive: true });
  const cache = new Map();
  const loadPage = async (u) => { const k = U.pathKey(u); if (!cache.has(k)) cache.set(k, await P.load(u)); return cache.get(k); };
  let pages;
  if (mode === 'storefront') {
    const home = await loadPage(url);
    home.via = 'entry';
    pages = [home];
    for (const u of [...o.pages, ...(home.ok ? storefrontPages(home, o.maxPages || 15) : [])]) { const p = await loadPage(u); p.via = 'navigation link'; pages.push(p); }
  } else if (mode === 'design') {
    const p = await loadPage(url); p.via = 'entry'; pages = [p];
  } else {
    const follow = o.follow !== false && !['checkout', 'upsell'].includes(scope);
    const c = await crawl(url, { pages: o.pages, maxPages: o.maxPages || (scope === 'funnel' ? 12 : 6), follow, load: loadPage });
    pages = c.pages;
  }
  let notes = [];
  if (mode === 'design') { pages[0].role = 'design'; pages[0].in_scope = true; } else notes = assignRoles(pages, scope);
  const inScope = pages.filter((p) => p.in_scope);
  const speedPages = (() => {
    const ok = inScope.filter((p) => p.ok);
    if (scope !== 'funnel') return ok.slice(0, 1);
    const pick = [ok[0], ok.find((p) => p.role === 'checkout'), ok.find((p) => p.role === 'upsell')].filter(Boolean);
    return [...new Set(pick)].slice(0, o.speedPages || 3);
  })();
  const ctx = {
    mode, scope, entryUrl: url, pages: inScope, allPages: pages, speedPages, outDir, rules, loadPage,
    options: { ...o, pages: o.pages },
    showId: o.maskIds ? U.maskId : (x) => x,
  };
  const lanes = {};
  let rows = [...notes, ...(await sourceLane(ctx, FAMILIES[mode]))];
  lanes.source = 'ran';
  lanes.speed = mode === 'design' ? 'not used' : o.speed === false ? 'off (--no-speed)' : rows.some((r) => r.family === 'speed' && r.status !== 'skipped') ? 'ran' : 'did not run';
  if (mode === 'design') {
    rows.push(...(await require('./browser/design').run(ctx)));
    lanes.browser = 'ran';
  } else if (o.browser !== false) {
    const mod = mode === 'storefront' ? require('./browser/storefront') : require('./browser/funnel');
    const got = await mod.run(ctx);
    lanes.browser = got.some((r) => /unavailable/.test(r.id)) ? 'skipped (Playwright not installed)' : 'ran';
    rows.push(...got);
  } else lanes.browser = 'off (--no-browser)';
  if (mode === 'funnel') lanes.order = o.campaignsOsOrders ? `campaigns-os typed-card orders (${o.campaignsOsOrders})` : 'not run yet (record, or campaigns-os for a scripted order)';
  let campaignsOs = null;
  if (o.campaignsOsVerdict) {
    ({ assertions: rows, info: campaignsOs } = foldCampaignsOs(rows, o.campaignsOsVerdict));
    lanes.campaigns_os = 'verdict folded in';
  } else if (mode === 'funnel') {
    const det = COS.detect(o);
    if (det.run) {
      const ran = await COS.run(det, { baseUrl: o.campaignsOsBaseUrl || url, outDir, o });
      if (ran.verdict) {
        ({ assertions: rows, info: campaignsOs } = foldCampaignsOs(rows, ran.verdict));
        campaignsOs.command = ran.command;
        lanes.campaigns_os = `ran (${det.kind}, ${ran.seconds} s)`;
      } else {
        rows.push(U.assertion({ id: 'campaigns-os:run', family: 'campaigns-os', status: 'skipped', severity: 'info', message: `campaigns-os qa did not produce a verdict (exit ${ran.code}): ${ran.tail}. Log: campaigns-os/campaigns-os.log` }));
        lanes.campaigns_os = 'failed (see campaigns-os/campaigns-os.log)';
      }
    } else lanes.campaigns_os = `not run: ${det.reason}`;
  }
  rows = R.applyByDesign(rows, rules);
  const result = {
    schema: 'next-launch-check/results/v2',
    tool_version: VERSION,
    run_id: `${U.timestamp(started)}-${Math.random().toString(36).slice(2, 6)}`,
    mode, scope, entry_url: url,
    started_at: started.toISOString(), finished_at: new Date().toISOString(),
    options: { browser: o.browser !== false, speed: o.speed !== false, rules: rules.source, diff: o.diff || null, mask_ids: !!o.maskIds, live_beacons: !!o.liveBeacons, max_pages: o.maxPages || null },
    lanes,
    pages: pages.map((p) => ({ label: p.label, url: p.url, final_url: p.finalUrl, status: p.status, type: p.kind.type, role: p.role, in_scope: !!p.in_scope, title: p.title, via: p.via, sdk_version: p.sdk.version || null })),
    campaigns_os: campaignsOs,
  };
  finish(result, rows, result.pages, { rules, extra: mode === 'design' ? DESIGN_ITEMS : [] });
  report.write(outDir, result);
  return { result, outDir };
}

async function runRecord(url, o, how = 'record') {
  const rec = require('./browser/record');
  const rules = R.load(o.rules);
  const gate = rec.paramGate(url, { confirmed: !!o.confirmTestParams, allowed: rules.allowedParams });
  if (!gate.ok) { const e = new Error(gate.reason); e.code = 3; throw e; }
  const started = new Date();
  const into = o.into ? path.resolve(o.into) : null;
  const outDir = into || path.resolve(o.out || path.join('next-launch-check-output', `${U.hostSlug(url)}-${U.timestamp(started)}-order`));
  fs.mkdirSync(path.join(outDir, 'screenshots'), { recursive: true });
  let assertions;
  if (how === 'order') {
    const customer = { email: o.email || 'qa+launchcheck@example.com', firstName: o.firstName || 'QA', lastName: o.lastName || 'Launchcheck', phone: o.phone || '', country: o.country || 'US', address1: o.address1 || '1600 Amphitheatre Pkwy', city: o.city || 'Mountain View', province: o.province || 'CA', postal: o.postal || '94043' };
    ({ rows: assertions } = await require('./browser/order').run({ entryUrl: url, outDir, options: o, rules, customer }));
  } else ({ assertions } = await rec.record({ entryUrl: url, outDir, options: o, rules }));
  let result;
  let pages;
  if (into) {
    result = JSON.parse(fs.readFileSync(path.join(into, 'results.json'), 'utf8'));
    pages = result.pages;
    // An order is evidence for the checkout and upsells even when the run
    // tested only one page type.
    for (const p of pages) if (['checkout', 'upsell', 'receipt'].includes(p.role)) p.in_scope = p.in_scope || p.role === 'checkout';
    // Earlier order rows stay: each order has its own label.
    const ids = new Set(assertions.map((a) => a.id));
    const rows = result.assertions.filter((a) => !ids.has(a.id)).concat(assertions);
    result.lanes.order = how === 'order' ? (o.dryRun ? `rehearsed, no order sent (${o.orders || 'standard:accept'})` : `placed by the script (${o.orders || 'standard:accept'})`) : 'recorded (tester placed the order)';
    result.finished_at = new Date().toISOString();
    finish(result, rows, pages, { rules, prior: result.checklist });
  } else {
    pages = [{ label: U.pageLabel(url), url, role: 'checkout', in_scope: true, status: 200 }];
    result = {
      schema: 'next-launch-check/results/v2', tool_version: VERSION, run_id: `${U.timestamp(started)}-order`, mode: 'record', scope: 'checkout', entry_url: url,
      started_at: started.toISOString(), finished_at: new Date().toISOString(), options: { live_beacons: !!o.liveBeacons, affiliate_params: gate.found.map(([k]) => k) },
      lanes: { order: 'recorded (tester placed the order)' }, pages, campaigns_os: null,
    };
    finish(result, assertions, pages, { rules });
  }
  report.write(outDir, result);
  return { result, outDir };
}

// Speed test only: mobile and desktop for the URL and any --page URLs.
async function runSpeed(url, o) {
  const started = new Date();
  const outDir = path.resolve(o.out || path.join('next-launch-check-output', `${U.hostSlug(url)}-${U.timestamp(started)}-speed`));
  fs.mkdirSync(outDir, { recursive: true });
  const pages = [];
  for (const u of [url, ...o.pages]) { const p = await P.load(u); p.via = u === url ? 'entry' : 'named'; p.role = 'landing'; p.in_scope = true; pages.push(p); }
  const rows = await require('./lib/checks/speed')({ pages, speedPages: pages.filter((p) => p.ok), options: { speed: true } });
  for (const p of pages) if (!p.ok) rows.push(U.assertion({ id: `http:${p.label}`, family: 'http', page: p.label, url: p.url, status: 'fail', severity: 'blocker', message: `Page answered HTTP ${p.status || 'error'}.` }));
  const result = {
    schema: 'next-launch-check/results/v2', tool_version: VERSION, run_id: `${U.timestamp(started)}-speed`, mode: 'speed', scope: 'speed', entry_url: url,
    started_at: started.toISOString(), finished_at: new Date().toISOString(), options: { speed: true }, lanes: { speed: 'ran' },
    pages: pages.map((p) => ({ label: p.label, url: p.url, final_url: p.finalUrl, status: p.status, type: p.kind.type, role: null, in_scope: true, title: p.title, via: p.via })),
    campaigns_os: null,
  };
  const speedItem = { id: 'speed-test', section: 'Speed', text: 'Speed test, mobile and desktop', roles: ['speed'], severity: 'warn', auto: ['speed:*', 'http:*'] };
  result.assertions = rows;
  result.counts = report.counts(rows);
  result.disposition_automated = report.automatedDisposition(rows);
  result.checklist = CL.build([{ label: '-', role: 'speed', in_scope: true }], rows, { extra: [speedItem] }).filter((i) => i.id === 'speed-test');
  result.disposition = CL.disposition(result.checklist, result.disposition_automated);
  report.write(outDir, result);
  return { result, outDir };
}

async function finalize(dir, answersFile, o = {}) {
  const file = path.join(dir, 'results.json');
  const result = JSON.parse(fs.readFileSync(file, 'utf8'));
  const answers = JSON.parse(fs.readFileSync(answersFile, 'utf8'));
  const list = Array.isArray(answers) ? answers : Object.entries(answers).map(([id, v]) => (typeof v === 'string' ? { id, answer: v } : { id, ...v }));
  for (const a of list) {
    const item = result.checklist.find((x) => x.id === a.id);
    if (!item) throw new Error(`no checklist item ${a.id} in this run`);
    if (!['pass', 'fail', 'na', 'warn'].includes(a.answer)) throw new Error(`${a.id}: answer must be pass, fail, warn or na`);
    item.answer = a.answer;
    item.note = a.note || null;
    item.status = CL.finalStatus(item);
  }
  result.disposition = CL.disposition(result.checklist, result.disposition_automated);
  result.finished_at = new Date().toISOString();
  report.write(dir, result);
  if (o.pdf !== false) await report.pdf(dir);
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
    if (cmd === 'finalize') { if (!target || !o.answers) throw new Error('finalize needs <run-dir> and --answers <file>'); done = await finalize(path.resolve(target), o.answers, o); }
    else if (cmd === 'record') done = await runRecord(checkUrl(target || ''), o);
    else if (cmd === 'order') done = await runRecord(checkUrl(target || ''), o, 'order');
    else if (cmd === 'check') done = await runCheck(o.scope || 'funnel', checkUrl(target || ''), o);
    else if (cmd === 'speed') done = await runSpeed(checkUrl(target || ''), o);
    else if (cmd === 'funnel' || cmd === 'storefront' || cmd === 'design') done = await runCheck(cmd === 'funnel' ? (o.scope || 'funnel') : cmd, checkUrl(target || ''), o);
    else throw new Error(`unknown command ${cmd}`);
    if (cmd !== 'finalize' && o.pdf !== false) await report.pdf(done.outDir);
    const r = done.result;
    const open = r.checklist.filter((i) => i.status === 'open').length;
    const tally = (s) => r.checklist.filter((i) => i.status === s).length;
    process.stdout.write(`${r.disposition.toUpperCase()}  (${r.scope || r.mode})\n`);
    process.stdout.write(`Checklist: ${tally('pass')} pass · ${tally('fail')} fail · ${tally('warn')} warn · ${open} for you to check · ${tally('na')} n/a  (of ${r.checklist.length})\n`);
    const has = (f) => fs.existsSync(path.join(done.outDir, f));
    process.stdout.write(`Report: ${path.join(done.outDir, 'report.html')}${has('report.pdf') ? ' (and report.pdf)' : ''}\n`);
    process.stdout.write(`Developer / AI notes: ${path.join(done.outDir, 'report.md')}\n`);
    process.stdout.write('These files are the report. Give the tester report.html (or report.pdf) and a developer report.md; do not write a separate report.\n');
    if (o.json) process.stdout.write(JSON.stringify(r, null, 2) + '\n');
    return r.disposition === 'blocked' ? 4 : 0;
  } catch (e) {
    process.stderr.write(`next-launch-check: ${e.message}\n`);
    return e.code === 3 ? 3 : 2;
  }
}

if (require.main === module) main(process.argv.slice(2)).then((code) => { process.exitCode = code; });

module.exports = { main, parseArgs, runCheck, runRecord, runSpeed, finalize, foldCampaignsOs, assignRoles, VERSION, SCOPES };
