'use strict';
// Browser lane against the fixture funnels. Runs only where Playwright and a
// Chromium build are installed (not in the catalog's CI). Offline: every
// request outside 127.0.0.1 is answered locally.
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('path');
const fs = require('fs');
const os = require('os');
const server = require('./server');
const S = path.join(__dirname, '..', '..', 'scripts');
const PW = require(path.join(S, 'browser/playwright'));
const cli = require(path.join(S, 'launch-check.js'));

process.env.NEXT_LAUNCH_CHECK_OFFLINE = '1';
function canRun() {
  const loaded = PW.load();
  if (!loaded) return 'Playwright is not installed';
  try { if (!fs.existsSync(loaded.pw.chromium.executablePath())) return 'Chromium for Playwright is not installed'; } catch (_) { return 'Chromium for Playwright is not installed'; }
  return false;
}
const skip = canRun();
const tmp = () => fs.mkdtempSync(path.join(os.tmpdir(), 'nlc-br-'));
const row = (r, id) => r.assertions.find((a) => a.id === id);

test('browser lane: good funnel', { skip, timeout: 240000 }, async () => {
  const s = await server.start('good');
  try {
    const { result } = await cli.runCheck('funnel', `${s.base}/`, cli.parseArgs(['--browser', '--out', tmp()]));
    assert.equal(row(result, 'browser-runtime:/checkout:sdk-ready').status, 'pass');
    assert.equal(row(result, 'param-propagation:/landing->/checkout').status, 'pass');
    assert.equal(row(result, 'form-negative:/checkout:empty').status, 'pass');
    assert.equal(row(result, 'locale:/checkout').status, 'pass');
    assert.equal(row(result, 'locale:/checkout:typed-amounts'), undefined, 'amounts inside SDK summaries are not typed amounts');
    assert.equal(row(result, 'responsive:/checkout').status, 'pass');
    assert.equal(row(result, 'tags-observed:/checkout').status, 'pass');
    assert.equal(s.calls.length, 0, 'the browser lane never places an order');
  } finally { await s.close(); }
});

test('browser lane: broken funnel', { skip, timeout: 240000 }, async () => {
  const s = await server.start('broken');
  try {
    const { result } = await cli.runCheck('funnel', `${s.base}/`, cli.parseArgs(['--browser', '--page', `${s.base}/upsell/`, '--out', tmp()]));
    assert.equal(row(result, 'param-propagation:/landing->/checkout').status, 'fail');
    assert.equal(row(result, 'form-negative:/checkout:empty').severity, 'blocker');
    assert.equal(row(result, 'form-negative:/checkout:empty').status, 'fail');
    assert.equal(row(result, 'responsive:/upsell').status, 'warn');
    assert.equal(s.calls.length, 0, 'the unvalidated form was submitted, but the order call was blocked');
  } finally { await s.close(); }
});

test('recorder: observes one order, the upsell and the purchase event', { skip, timeout: 120000 }, async () => {
  const s = await server.start('good');
  const out = tmp();
  try {
    const code = await cli.main(['record', `${s.base}/checkout/?autotest=1&utm_source=fb`, '--headless', '--record-minutes', '0.5', '--out', out]);
    assert.equal(code, 0);
    const r = JSON.parse(fs.readFileSync(path.join(out, 'results.json'), 'utf8'));
    for (const id of ['order:created', 'order:upsells', 'order:purchase-event', 'order:attribution', 'order:receipt']) assert.equal(row(r, id).status, 'pass', id);
    const record = JSON.parse(fs.readFileSync(path.join(out, 'order-record.json'), 'utf8'));
    assert.equal(JSON.stringify(record).includes('qa@example.com'), false, 'emails are redacted');
  } finally { await s.close(); }
});
