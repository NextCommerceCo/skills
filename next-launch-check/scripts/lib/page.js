'use strict';
const H = require('./html');
const net = require('./net');
const D = require('./detect');
const { pageLabel } = require('./util');

async function load(url, opts = {}) {
  const res = await net.getPage(url, opts);
  return build(url, res);
}

function build(url, res) {
  const html = res.html || '';
  const page = {
    url,
    finalUrl: res.finalUrl || url,
    status: res.status,
    ok: res.ok,
    error: res.error || null,
    ms: res.ms,
    contentType: res.contentType,
    html,
    label: pageLabel(url),
  };
  page.meta = H.meta(html);
  page.title = H.title(html);
  page.scripts = H.scripts(html);
  page.anchors = H.anchors(html);
  page.lang = (/<html\b[^>]*\blang=["']?([^"'\s>]+)/i.exec(html) || [])[1] || '';
  if (page.ok && html) {
    try { page.kind = D.pageType(page); } catch (_) { page.kind = { type: 'content', source: 'unknown' }; }
    page.sdk = D.sdk(page);
    page.routes = D.routes(page);
    page.tracking = D.tracking(page);
  } else {
    page.kind = { type: 'unknown', source: 'not loaded' };
    page.sdk = { present: false };
    page.routes = { success: null, accept: null, decline: null, funnel: null };
    page.tracking = { gtm: [], ga4: [], meta: [], tiktok: [], everflow: { present: false, offers: [] } };
  }
  return page;
}

module.exports = { load, build };
