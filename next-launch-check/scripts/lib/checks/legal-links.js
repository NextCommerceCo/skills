'use strict';
const { assertion, resolve } = require('../util');
const net = require('../net');

const KINDS = [
  ['terms', 'Terms', /\bterms\b|terms[-_ ]of|conditions/i],
  ['privacy', 'Privacy policy', /privacy/i],
  ['returns', 'Returns / refunds', /\breturns?\b|refund/i],
  ['shipping', 'Shipping', /shipping|delivery[-_ ]policy/i],
  ['contact', 'Contact', /contact|support|help[-_ ]?center/i],
  ['faq', 'FAQ', /\bfaqs?\b|frequently/i],
];

module.exports = async function legalLinks(ctx) {
  const out = [];
  const pages = ctx.pages.filter((p) => p.ok);
  const required = new Set((ctx.rules.require && ctx.rules.require.legal) || ['terms', 'privacy', 'returns', 'shipping', 'contact']);
  const statusCache = new Map();
  const check = async (u) => { if (!statusCache.has(u)) statusCache.set(u, net.status(u)); return statusCache.get(u); };
  for (const [kind, name, re] of KINDS) {
    const found = [];
    const absentOn = [];
    for (const p of pages) {
      const a = p.anchors.find((x) => re.test(x.text) || re.test(x.href || ''));
      if (!a) { absentOn.push(p.label); continue; }
      found.push({ p, a, abs: resolve(a.href, p.finalUrl) });
    }
    if (!found.length) {
      out.push(required.has(kind)
        ? assertion({ id: `legal-links:${kind}`, family: 'legal-links', status: 'fail', severity: 'warn', message: `No ${name} link on any page.${['returns', 'shipping'].includes(kind) ? ` If this policy is part of the Terms page, answer n/a for this item.` : ''}` })
        : assertion({ id: `legal-links:${kind}`, family: 'legal-links', status: 'pass', severity: 'info', message: `No ${name} link on the page. It is optional, so there is nothing to check.` }));
      continue;
    }
    const targets = [...new Set(found.map((f) => f.abs).filter(Boolean))];
    const results = await Promise.all(targets.map(async (u) => ({ u, r: await check(u) })));
    const broken = results.filter((x) => !x.r.ok);
    const sameTab = found.filter((f) => f.a.target !== '_blank' && ['checkout', 'upsell'].includes(f.p.kind.type)).map((f) => f.p.label);
    if (broken.length) {
      out.push(assertion({ id: `legal-links:${kind}`, family: 'legal-links', status: 'fail', severity: 'warn', message: `${name} link does not load: ${broken.map((b) => `${b.u} (HTTP ${b.r.status || 'error'})`).join(', ')}.` }));
    } else if (!targets.length) {
      out.push(assertion({ id: `legal-links:${kind}`, family: 'legal-links', status: 'manual_review', severity: 'info', message: `${name} link opens a popup or script, not a page. Check it opens and shows the right text.` }));
    } else {
      out.push(assertion({ id: `legal-links:${kind}`, family: 'legal-links', status: 'pass', severity: 'warn', message: `${name} link loads on ${found.length}/${pages.length} page(s).`, actual: targets }));
    }
    if (found.length && absentOn.length && required.has(kind)) {
      out.push(assertion({ id: `legal-links:${kind}:coverage`, family: 'legal-links', status: 'warn', severity: 'info', message: `${name} link missing on: ${absentOn.join(', ')}.` }));
    }
    if (sameTab.length) {
      out.push(assertion({ id: `legal-links:${kind}:new-tab`, family: 'legal-links', status: 'warn', severity: 'warn', message: `${name} link on ${sameTab.join(', ')} opens in the same tab, which takes the shopper off the checkout or upsell.` }));
    }
  }
  return out;
};
