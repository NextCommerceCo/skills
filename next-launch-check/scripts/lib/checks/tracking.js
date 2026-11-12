'use strict';
const { assertion } = require('../util');

// Tracking found in page source, and whether it is the same on every page.
// A tag that only loads through a tag manager will not show here; the
// browser lane reports what actually fires.
const KINDS = [
  ['gtm', 'Google Tag Manager'],
  ['ga4', 'Google Analytics 4'],
  ['meta', 'Meta pixel'],
  ['tiktok', 'TikTok pixel'],
];

module.exports = async function tracking(ctx) {
  const out = [];
  const pages = ctx.pages.filter((p) => p.ok);
  if (!pages.length) return out;
  const show = (ids) => ids.map(ctx.showId);
  const required = new Set((ctx.rules.require && ctx.rules.require.tracking) || []);
  for (const [kind, name] of KINDS) {
    const per = pages.map((p) => ({ p, ids: p.tracking[kind] }));
    const withIds = per.filter((x) => x.ids.length);
    if (!withIds.length) {
      if (required.has(kind)) out.push(assertion({ id: `tracking-consistency:${kind}`, family: 'tracking-consistency', status: 'fail', severity: 'warn', message: `${name} is required by your rules file but was not found in any page source.` }));
      continue;
    }
    const all = [...new Set(withIds.flatMap((x) => x.ids))];
    const missing = per.filter((x) => !x.ids.length).map((x) => x.p.label);
    const differs = withIds.filter((x) => x.ids.join() !== withIds[0].ids.join());
    if (missing.length) {
      out.push(assertion({ id: `tracking-consistency:${kind}:missing`, family: 'tracking-consistency', status: 'warn', severity: 'warn', message: `${name} (${show(all).join(', ')}) is in the source of some pages but not: ${missing.join(', ')}.`, actual: { missing } }));
    }
    if (differs.length) {
      out.push(assertion({ id: `tracking-consistency:${kind}:differs`, family: 'tracking-consistency', status: 'warn', severity: 'warn', message: `${name} ids differ between pages.`, actual: Object.fromEntries(withIds.map((x) => [x.p.label, show(x.ids)])) }));
    }
    if (!missing.length && !differs.length) {
      out.push(assertion({ id: `tracking-consistency:${kind}`, family: 'tracking-consistency', status: 'pass', severity: 'warn', message: `${name} ${show(all).join(', ')} on all ${pages.length} page(s).` }));
    }
  }
  const ef = pages.map((p) => ({ p, e: p.tracking.everflow }));
  const efPages = ef.filter((x) => x.e.present);
  if (efPages.length) {
    const missing = ef.filter((x) => !x.e.present && x.p.kind.type !== 'receipt').map((x) => x.p.label);
    out.push(assertion({ id: 'tracking-consistency:everflow', family: 'tracking-consistency', status: missing.length ? 'warn' : 'pass', severity: 'warn', message: missing.length ? `Everflow click script found on some pages but not: ${missing.join(', ')}.` : `Everflow click script on ${efPages.length} page(s).`, actual: { offers: [...new Set(efPages.flatMap((x) => x.e.offers))] } }));
  } else if (required.has('everflow')) {
    out.push(assertion({ id: 'tracking-consistency:everflow', family: 'tracking-consistency', status: 'fail', severity: 'warn', message: 'Everflow is required by your rules file but no click script was found in page source. It may load through the tag manager; check the browser lane.' }));
  }
  if (!out.length) {
    out.push(assertion({ id: 'tracking-consistency:none', family: 'tracking-consistency', status: 'manual_review', severity: 'warn', message: 'No tracking ids in any page source. If tracking loads another way, confirm it in the browser lane or with your tag manager preview.' }));
  }
  return out;
};
