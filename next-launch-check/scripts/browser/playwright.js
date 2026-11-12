'use strict';
// Find Playwright without installing anything: the campaign folder first
// (campaigns-os installs it there), then this skill folder, then the global
// npm root, then PLAYWRIGHT_MODULE. Returns null when it is not available.
const path = require('path');
const { execSync } = require('child_process');

function locate() {
  const tries = [process.env.PLAYWRIGHT_MODULE, process.cwd(), path.join(__dirname, '..', '..')].filter(Boolean);
  for (const base of tries) {
    try { return require.resolve(base.endsWith('playwright') || base.endsWith('index.js') ? base : 'playwright', { paths: [base] }); } catch (_) { /* next */ }
  }
  try { return require.resolve('playwright'); } catch (_) { /* next */ }
  try {
    const root = execSync('npm root -g', { stdio: ['ignore', 'pipe', 'ignore'], timeout: 8000 }).toString().trim();
    return require.resolve('playwright', { paths: [root] });
  } catch (_) { return null; }
}

function load() {
  const where = locate();
  if (!where) return null;
  try { return { pw: require(where), where }; } catch (_) { return null; }
}

// Requests that report to ad and analytics vendors. They are answered locally
// with 204 so a test visit or a test purchase never reaches a vendor, unless
// the tester passes --live-beacons. Tag libraries (gtm.js, gtag.js, pixel
// scripts) still load, so the run sees what would have fired.
const BEACONS = [
  ['ga4', /(^|\.)google-analytics\.com\/(g\/)?collect|analytics\.google\.com\/g\/collect/],
  ['google-ads', /googleadservices\.com\/pagead\/conversion|googleads\.g\.doubleclick\.net\/pagead|google\.com\/pagead\/1p-conversion/],
  ['meta', /facebook\.com\/tr\b|facebook\.com\/tr\/|connect\.facebook\.net\/signals\/(?!config)/],
  ['tiktok', /analytics\.tiktok\.com\/api\//],
  ['bing', /bat\.bing\.com\/action/],
  ['pinterest', /ct\.pinterest\.com\//],
  ['snap', /tr\.snapchat\.com\//],
  ['everflow', /\/sdk\/(click|conversion|impression)\b/],
  ['reddit', /alb\.reddit\.com\/rp\.gif|events\.redditmedia\.com/],
];
const TAG_LIBS = [
  ['gtm', /googletagmanager\.com\/gtm\.js/],
  ['gtag', /googletagmanager\.com\/gtag\/js/],
  ['meta', /connect\.facebook\.net\/.*fbevents\.js/],
  ['tiktok', /analytics\.tiktok\.com\/i18n\/pixel/],
  ['everflow', /everflow|\/scripts\/main\.js/],
];

function classify(url, list) {
  const hit = list.find(([, re]) => re.test(url));
  return hit ? hit[0] : null;
}

// Order-creating calls. The automated lanes never let these through.
const ORDER_CALL = /\/api\/v1\/orders\/?(\?|$)|\/api\/v1\/orders\/[^/]+\/upsells\/?|\/upsells\/?(\?|$)/i;

async function guard(context, { liveBeacons = false, log, blockOrders = true } = {}) {
  await context.route('**/*', async (route) => {
    const req = route.request();
    const url = req.url();
    const vendor = classify(url, BEACONS);
    if (vendor && !liveBeacons) {
      log.beacons.push({ vendor, url: url.slice(0, 300), method: req.method() });
      return route.fulfill({ status: 204, body: '' });
    }
    if (blockOrders && req.method() !== 'GET' && ORDER_CALL.test(url)) {
      log.blockedOrders.push({ url, method: req.method() });
      return route.abort('blockedbyclient');
    }
    if (log.blockAllWrites && req.method() !== 'GET' && req.method() !== 'HEAD' && req.method() !== 'OPTIONS' && !vendor) {
      log.blockedWrites.push({ url, method: req.method() });
      return route.abort('blockedbyclient');
    }
    const lib = classify(url, TAG_LIBS);
    if (lib) log.tagLibs.push({ lib, url: url.slice(0, 200) });
    // Offline tests: answer every outside request locally so no test run
    // touches the network.
    if (process.env.NEXT_LAUNCH_CHECK_OFFLINE === '1' && !/^https?:\/\/(127\.0\.0\.1|localhost)(:|\/)/.test(url)) {
      return route.fulfill({ status: 200, contentType: /\.js(\?|$)|gtm\.js|gtag\/js/.test(url) ? 'application/javascript' : 'text/plain', body: '' });
    }
    return route.continue();
  });
}

function newLog() { return { beacons: [], tagLibs: [], blockedOrders: [], blockedWrites: [], blockAllWrites: false }; }

module.exports = { load, locate, guard, newLog, classify, BEACONS, TAG_LIBS, ORDER_CALL };
