'use strict';
// Customer-service email and phone shown on each page, and whether every
// page shows the same ones. Read from mailto:/tel: links and visible text.
const { assertion } = require('../util');

const EMAIL = /[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi;
// A phone as people write it: optional country code, then groups of digits
// split by spaces, dots, dashes or brackets. Compared by its last 10 digits.
const PHONE = /(?:\+\d{1,3}[\s.-]?)?\(?\d{2,4}\)?[\s.-]?\d{3,4}[\s.-]?\d{3,4}(?![\d])/;
const key = (n) => n.replace(/\D/g, '').slice(-10);

function details(p) {
  const text = String(p.html || '')
    .replace(/<(script|style|noscript|template)[\s\S]*?<\/\1>/gi, ' ')
    .replace(/<[^>]+>/g, ' ');
  const emails = new Set();
  const phones = new Set();
  for (const a of p.anchors || []) {
    const href = String(a.href || '');
    if (/^mailto:/i.test(href)) emails.add(href.slice(7).split('?')[0].toLowerCase());
    if (/^tel:/i.test(href)) phones.add(href.slice(4).replace(/[^\d+]/g, ''));
  }
  for (const m of text.match(EMAIL) || []) if (!/\.(png|jpe?g|gif|webp|svg)$/i.test(m) && !/example\.(com|org)$/i.test(m)) emails.add(m.toLowerCase());
  // Phone-like text only near a support word, so prices and ids are not read as phones.
  for (const m of text.matchAll(/(call|phone|tel|support|contact|customer service)[^\d+(]{0,40}([+(]?\d[\d\s().-]{6,20})/gi)) {
    const ph = PHONE.exec(m[2]);
    if (ph) phones.add(ph[0].replace(/[^\d+]/g, ''));
  }
  const byKey = new Map();
  for (const x of phones) if (x.replace(/\D/g, '').length >= 8 && !byKey.has(key(x))) byKey.set(key(x), x);
  return { emails: [...emails], phones: [...byKey.values()] };
}

module.exports = async function contact(ctx) {
  const pages = ctx.pages.filter((p) => p.ok && p.in_scope !== false);
  if (!pages.length) return [];
  const per = pages.map((p) => ({ p, d: details(p) }));
  const emails = [...new Set(per.flatMap((x) => x.d.emails))];
  const phones = [...new Set(per.flatMap((x) => x.d.phones))];
  if (!emails.length && !phones.length) {
    return [assertion({ id: 'contact-details', family: 'contact', status: 'manual_review', severity: 'info', message: 'No customer-service email or phone number on the page(s); shoppers only have the Contact link. Confirm that is what the client wants.' })];
  }
  const differs = per.length > 1 && (emails.length > 1 || phones.length > 1);
  const shown = [emails.length ? `email ${emails.join(', ')}` : null, phones.length ? `phone ${phones.join(', ')}` : null].filter(Boolean).join('; ');
  return [differs
    ? assertion({ id: 'contact-details', family: 'contact', status: 'fail', severity: 'warn', message: `Pages show different customer-service details: ${shown}.`, evidence: Object.fromEntries(per.map((x) => [x.p.label, [...x.d.emails, ...x.d.phones].join(', ') || 'none'])) })
    : assertion({ id: 'contact-details', family: 'contact', status: 'pass', severity: 'warn', message: `Same customer-service details on every page: ${shown}. Confirm they are the client's.`, evidence: shown })];
};
module.exports.details = details;
