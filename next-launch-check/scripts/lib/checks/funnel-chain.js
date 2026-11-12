'use strict';
const { assertion, resolve, pathKey, pageLabel } = require('../util');

// Route tags tie the funnel together: the checkout names the page after a
// paid order (next-success-url; next-next-url is a legacy alias) and each
// upsell names where accept and decline go.
module.exports = async function funnelChain(ctx) {
  const out = [];
  const byKey = new Map(ctx.pages.map((p) => [pathKey(p.finalUrl), p]));
  const commerce = ctx.pages.filter((p) => p.ok && ['checkout', 'upsell', 'receipt'].includes(p.kind.type));
  if (!commerce.length) {
    out.push(assertion({ id: 'funnel-chain:none', family: 'funnel-chain', status: 'skipped', severity: 'info', message: 'No checkout, upsell or receipt page found from this entry. Name them with --page if the funnel has them.' }));
    return out;
  }
  const checkTarget = async (p, tag, href, required) => {
    const id = `funnel-chain:${p.label}:${tag}`;
    const base = { id, family: 'funnel-chain', page: p.label, url: p.url };
    if (!href) {
      out.push(assertion({ ...base, status: required ? 'fail' : 'warn', severity: 'warn', message: `${tag} is not set. The SDK will fall back to its default route, which may not be the next page you intend.`, expected: tag }));
      return;
    }
    const abs = resolve(href, p.finalUrl);
    if (!abs) {
      out.push(assertion({ ...base, status: 'fail', severity: 'blocker', message: `${tag} has an unusable value.`, actual: href }));
      return;
    }
    const target = byKey.get(pathKey(abs)) || (await ctx.loadPage(abs));
    if (!target.ok) {
      out.push(assertion({ ...base, status: 'fail', severity: 'blocker', message: `${tag} points to a page that does not load (HTTP ${target.status || 'error'}).`, actual: href }));
      return;
    }
    if (pathKey(abs) === pathKey(p.finalUrl)) {
      out.push(assertion({ ...base, status: 'fail', severity: 'blocker', message: `${tag} points back to this same page.`, actual: href }));
      return;
    }
    out.push(assertion({ ...base, status: 'pass', severity: 'blocker', message: `${tag} → ${pageLabel(abs)} (${target.kind.type}).`, actual: href }));
  };
  for (const p of commerce) {
    const r = p.routes;
    if (p.kind.type === 'checkout') {
      await checkTarget(p, r.successTag || 'next-success-url', r.success, false);
      if (r.successTag === 'next-next-url') {
        out.push(assertion({ id: `funnel-chain:${p.label}:legacy-tag`, family: 'funnel-chain', page: p.label, url: p.url, status: 'warn', severity: 'info', message: 'Uses the legacy next-next-url tag. Current SDK docs name next-success-url.' }));
      }
    } else if (p.kind.type === 'upsell') {
      await checkTarget(p, 'next-upsell-accept-url', r.accept, false);
      await checkTarget(p, 'next-upsell-decline-url', r.decline, false);
    }
  }
  // Named page order: if the tester gave pages in order, the declared next
  // page should be the next named page.
  const named = ctx.pages.filter((p) => p.via === 'entry' || p.via === 'named');
  if (ctx.options.pages && ctx.options.pages.length) {
    for (let i = 0; i < named.length - 1; i++) {
      const p = named[i];
      const nextKey = pathKey(named[i + 1].finalUrl);
      const declared = [p.routes.success, p.routes.accept, p.routes.decline].map((h) => resolve(h, p.finalUrl)).filter(Boolean).map(pathKey);
      if (!declared.length) continue;
      if (!declared.includes(nextKey)) {
        out.push(assertion({ id: `funnel-chain:${p.label}:order`, family: 'funnel-chain', page: p.label, url: p.url, status: 'warn', severity: 'warn', message: `The page you listed next (${named[i + 1].label}) is not where this page's route tags lead.`, expected: named[i + 1].label, actual: declared.map((k) => pageLabel(k)) }));
      }
    }
  }
  const funnels = [...new Set(commerce.map((p) => p.routes.funnel).filter(Boolean))];
  if (funnels.length > 1) {
    out.push(assertion({ id: 'funnel-chain:next-funnel', family: 'funnel-chain', status: 'warn', severity: 'warn', message: 'Pages carry different next-funnel names.', actual: funnels }));
  }
  return out;
};
