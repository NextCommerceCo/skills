'use strict';
const { assertion } = require('../util');
const net = require('../net');

// Optional. Uses Google's public PageSpeed Insights API, keyless or with the
// tester's own PAGESPEED_API_KEY from the environment. Results are advice
// (severity info): lab scores vary run to run.
const TARGETS = { lcp: 4000, fcp: 3000, tbt: 600, cls: 0.25 };

function reportUrl(url, strategy) {
  const u = new URL('https://pagespeed.web.dev/report');
  u.searchParams.set('url', url);
  u.searchParams.set('form_factor', strategy);
  return u.href;
}

async function one(url, strategy) {
  // Google's public PageSpeed Insights v5 endpoint.
  const api = new URL(['pagespeedonline', 'v5', 'runPagespeed'].join('/'), 'https://www.googleapis.com/');
  api.searchParams.set('url', url);
  api.searchParams.set('strategy', strategy);
  api.searchParams.set('category', 'performance');
  if (process.env.PAGESPEED_API_KEY) api.searchParams.set('key', process.env.PAGESPEED_API_KEY);
  const r = await net.getJson(api.href, { timeoutMs: 90000 });
  if (!r.ok || !r.body || !r.body.lighthouseResult) return { error: (r.body && r.body.error && r.body.error.message) || `HTTP ${r.status}` };
  const lh = r.body.lighthouseResult;
  const a = lh.audits || {};
  const v = (id) => (a[id] ? a[id].numericValue : null);
  return {
    score: Math.round((lh.categories.performance.score || 0) * 100),
    lcp: v('largest-contentful-paint'), fcp: v('first-contentful-paint'), tbt: v('total-blocking-time'), cls: v('cumulative-layout-shift'),
    report: reportUrl(url, strategy),
  };
}

module.exports = async function pagespeed(ctx) {
  if (!ctx.options.pagespeed) return [];
  const out = [];
  const pages = ctx.pages.filter((p) => p.ok).slice(0, ctx.options.pagespeedPages || 3);
  for (const p of pages) {
    const r = await one(p.finalUrl, 'mobile').catch((e) => ({ error: String(e.message || e) }));
    const base = { family: 'pagespeed', page: p.label, url: p.url };
    if (r.error) { out.push(assertion({ ...base, id: `pagespeed:${p.label}`, status: 'skipped', severity: 'info', message: `PageSpeed did not answer: ${r.error}. Set PAGESPEED_API_KEY if the keyless quota is used up.` })); continue; }
    const over = Object.entries(TARGETS).filter(([k, t]) => r[k] !== null && r[k] > t).map(([k]) => k.toUpperCase());
    out.push(assertion({ ...base, id: `pagespeed:${p.label}`, status: over.length || r.score < 50 ? 'warn' : 'pass', severity: 'info', message: `Mobile performance ${r.score}/100; LCP ${Math.round(r.lcp)} ms, FCP ${Math.round(r.fcp)} ms, TBT ${Math.round(r.tbt)} ms, CLS ${r.cls !== null ? r.cls.toFixed(2) : '—'}${over.length ? `. Over target: ${over.join(', ')}` : ''}.`, actual: r, evidence: r.report }));
  }
  return out;
};
