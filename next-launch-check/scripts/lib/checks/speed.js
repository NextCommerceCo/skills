'use strict';
// Speed test, mobile and desktop, per page. Engines, in order:
//  1. Google PageSpeed Insights with the tester's own PAGESPEED_API_KEY;
//  2. Lighthouse on this machine (the engine PageSpeed runs), with
//     Playwright's Chromium when present — downloaded by npx on first use;
//  3. PageSpeed Insights without a key (a shared daily quota that is often
//     used up);
//  otherwise the row is skipped with a link to run it by hand.
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawn } = require('child_process');
const { assertion } = require('../util');
const net = require('../net');

const LIGHTHOUSE = 'lighthouse@12.8.2';
// Targets used by the QA report: performance score and lab metrics.
const TARGETS = { score: 50, fcp: 3000, lcp: 4000, tbt: 200, cls: 0.1, si: 4400 };

function reportUrl(url, strategy) {
  const u = new URL('https://pagespeed.web.dev/report');
  u.searchParams.set('url', url);
  u.searchParams.set('form_factor', strategy);
  return u.href;
}

function metrics(lh) {
  const a = lh.audits || {};
  const v = (id) => (a[id] && typeof a[id].numericValue === 'number' ? a[id].numericValue : null);
  return { score: Math.round(((lh.categories && lh.categories.performance && lh.categories.performance.score) || 0) * 100), fcp: v('first-contentful-paint'), lcp: v('largest-contentful-paint'), tbt: v('total-blocking-time'), cls: v('cumulative-layout-shift'), si: v('speed-index') };
}

async function psi(url, strategy, key) {
  const api = new URL(['pagespeedonline', 'v5', 'runPagespeed'].join('/'), 'https://www.googleapis.com/');
  api.searchParams.set('url', url);
  api.searchParams.set('strategy', strategy);
  api.searchParams.set('category', 'performance');
  if (key) api.searchParams.set('key', key);
  const r = await net.getJson(api.href, { timeoutMs: 120000 });
  if (!r.ok || !r.body || !r.body.lighthouseResult) throw new Error((r.body && r.body.error && r.body.error.message) || `HTTP ${r.status}`);
  return metrics(r.body.lighthouseResult);
}

function chromePath() {
  if (process.env.CHROME_PATH) return process.env.CHROME_PATH;
  try {
    const PW = require('../../browser/playwright').load();
    const p = PW && PW.pw.chromium.executablePath();
    return p && fs.existsSync(p) ? p : null;
  } catch (_) { return null; }
}

function lighthouse(url, strategy) {
  return new Promise((resolve, reject) => {
    const out = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'nlc-lh-')), 'lh.json');
    const args = ['--yes', LIGHTHOUSE, url, '--only-categories=performance', '--output=json', `--output-path=${out}`, '--quiet', '--chrome-flags=--headless=new --no-sandbox'];
    if (strategy === 'desktop') args.push('--preset=desktop');
    const env = { ...process.env };
    const chrome = chromePath();
    if (chrome) env.CHROME_PATH = chrome;
    const child = spawn(process.platform === 'win32' ? 'npx.cmd' : 'npx', args, { env, stdio: ['ignore', 'ignore', 'pipe'] });
    let err = '';
    child.stderr.on('data', (d) => { err += d; });
    const timer = setTimeout(() => { child.kill('SIGKILL'); reject(new Error('Lighthouse timed out after 150 s')); }, 150000);
    child.on('error', (e) => { clearTimeout(timer); reject(e); });
    child.on('close', (code) => {
      clearTimeout(timer);
      try {
        if (code !== 0) throw new Error(err.split('\n').filter(Boolean).slice(-2).join(' ') || `exit ${code}`);
        resolve(metrics(JSON.parse(fs.readFileSync(out, 'utf8'))));
      } catch (e) { reject(e); }
    });
  });
}

async function measure(url, strategy, tried) {
  const key = process.env.PAGESPEED_API_KEY;
  if (key) {
    try { return { engine: 'PageSpeed Insights (your key)', ...(await psi(url, strategy, key)) }; } catch (e) { tried.push(`PageSpeed with key: ${e.message}`); }
  }
  if (!tried.lighthouseDown) {
    try { return { engine: 'Lighthouse on this machine', ...(await lighthouse(url, strategy)) }; } catch (e) { tried.push(`Lighthouse: ${String(e.message).slice(0, 160)}`); tried.lighthouseDown = true; }
  }
  if (!tried.psiDown) {
    try { return { engine: 'PageSpeed Insights (no key)', ...(await psi(url, strategy)) }; } catch (e) { tried.push(`PageSpeed without key: ${String(e.message).slice(0, 160)}`); tried.psiDown = true; }
  }
  return null;
}

function judge(m) {
  const missed = [];
  if (m.score < TARGETS.score) missed.push(`score ${m.score} < ${TARGETS.score}`);
  for (const k of ['fcp', 'lcp', 'tbt', 'si']) if (m[k] !== null && m[k] > TARGETS[k]) missed.push(`${k.toUpperCase()} ${Math.round(m[k])} ms > ${TARGETS[k]}`);
  if (m.cls !== null && m.cls > TARGETS.cls) missed.push(`CLS ${m.cls.toFixed(2)} > ${TARGETS.cls}`);
  return missed;
}

// Speed visits are real page loads with no beacon blocking, so affiliate and
// click-id parameters are removed first: a speed test must never register a
// click or a conversion with an affiliate network.
const AFFILIATE = /^(affid|aff_id|affiliate_id|sub[1-9]|sub10|oid|offer_id|transaction_id|_ef_transaction_id|clickid|click_id|cid|s[1-5]|gclid|fbclid|ttclid|msclkid|evclid)$/i;
function cleanUrl(url) {
  const u = new URL(url);
  for (const k of [...u.searchParams.keys()]) if (AFFILIATE.test(k)) u.searchParams.delete(k);
  return u.href;
}

const ms = (v) => (v === null ? '—' : v >= 1000 ? `${(v / 1000).toFixed(1)} s` : `${Math.round(v)} ms`);

module.exports = async function speed(ctx) {
  if (ctx.options.speed === false) return [];
  const pages = ctx.speedPages || ctx.pages.filter((p) => p.ok).slice(0, 3);
  if (process.env.NEXT_LAUNCH_CHECK_OFFLINE === '1') {
    return pages.map((p) => assertion({ id: `speed:${p.label}`, family: 'speed', page: p.label, url: p.url, status: 'skipped', severity: 'warn', message: 'Speed test skipped in offline mode.' }));
  }
  const out = [];
  const tried = [];
  for (const p of pages) {
    for (const strategy of ['mobile', 'desktop']) {
      const base = { id: `speed:${p.label}:${strategy}`, family: 'speed', page: p.label, url: p.url };
      const m = await measure(cleanUrl(p.finalUrl), strategy, tried);
      if (!m) {
        out.push(assertion({ ...base, status: 'skipped', severity: 'warn', message: `No speed engine answered (${tried.slice(-3).join('; ')}). Run it by hand: ${reportUrl(cleanUrl(p.finalUrl), strategy)}. Setting PAGESPEED_API_KEY fixes the shared-quota problem.` }));
        continue;
      }
      const missed = judge(m);
      out.push(assertion({ ...base, status: missed.length ? 'warn' : 'pass', severity: 'warn', message: `${strategy === 'mobile' ? 'Mobile' : 'Desktop'} ${m.score}/100 — FCP ${ms(m.fcp)}, LCP ${ms(m.lcp)}, TBT ${ms(m.tbt)}, CLS ${m.cls === null ? '—' : m.cls.toFixed(2)}, Speed Index ${ms(m.si)}${missed.length ? `. Over target: ${missed.join(', ')}` : ''}. (${m.engine})`, actual: m, evidence: reportUrl(cleanUrl(p.finalUrl), strategy) }));
    }
  }
  return out;
};
module.exports.judge = judge;
module.exports.cleanUrl = cleanUrl;
module.exports.TARGETS = TARGETS;
