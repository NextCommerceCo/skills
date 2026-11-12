'use strict';

const STATUSES = ['pass', 'fail', 'warn', 'skipped', 'manual_review'];
const SEVERITIES = ['blocker', 'warn', 'info'];

function assertion(fields) {
  const a = {
    id: fields.id,
    family: fields.family,
    page: fields.page || null,
    url: fields.url || null,
    status: fields.status,
    severity: fields.severity || 'info',
    message: fields.message || '',
  };
  if (fields.expected !== undefined) a.expected = fields.expected;
  if (fields.actual !== undefined) a.actual = fields.actual;
  if (fields.evidence !== undefined) a.evidence = fields.evidence;
  if (fields.screenshots && fields.screenshots.length) a.screenshots = fields.screenshots;
  if (!STATUSES.includes(a.status)) throw new Error(`bad status ${a.status} on ${a.id}`);
  if (!SEVERITIES.includes(a.severity)) throw new Error(`bad severity ${a.severity} on ${a.id}`);
  return a;
}

function resolve(href, base) {
  if (!href) return null;
  const h = href.trim();
  if (!h || /^(javascript:|mailto:|tel:|data:|#)/i.test(h)) return null;
  try { return new URL(h, base).href; } catch (_) { return null; }
}

function sameOrigin(a, b) {
  try { return new URL(a).origin === new URL(b).origin; } catch (_) { return false; }
}

function pathKey(url) {
  try {
    const u = new URL(url);
    let p = u.pathname.replace(/\/index\.html?$/i, '/');
    if (p.length > 1 && p.endsWith('/')) p = p.slice(0, -1);
    return u.origin + p;
  } catch (_) { return url; }
}

function pageLabel(url) {
  try {
    const u = new URL(url);
    const p = u.pathname.replace(/\/index\.html?$/i, '/').replace(/\/$/, '');
    return p || '/';
  } catch (_) { return url; }
}

// Tracking ids are visible in any page source, but reports often travel into
// tickets. maskId keeps the prefix and the last two characters.
function maskId(id) {
  const s = String(id || '');
  if (s.length <= 6) return s.replace(/.(?=.{2})/g, '•');
  const prefix = (/^(GTM-|G-|AW-|UA-)/.exec(s) || [''])[0];
  const body = s.slice(prefix.length);
  return prefix + body.slice(0, 1) + '•'.repeat(Math.max(body.length - 3, 1)) + body.slice(-2);
}

function maskKey(key) {
  const s = String(key || '');
  if (s.length <= 6) return '••••';
  return s.slice(0, 3) + '•'.repeat(Math.min(s.length - 5, 12)) + s.slice(-2);
}

function timestamp(d = new Date()) {
  return d.toISOString().replace(/[-:]/g, '').replace(/\..+/, '').replace('T', '-');
}

function hostSlug(url) {
  try { return new URL(url).hostname.replace(/[^a-z0-9.-]/gi, '-'); } catch (_) { return 'site'; }
}

module.exports = { STATUSES, SEVERITIES, assertion, resolve, sameOrigin, pathKey, pageLabel, maskId, maskKey, timestamp, hostSlug };
