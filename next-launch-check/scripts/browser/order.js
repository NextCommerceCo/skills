'use strict';
// Automated test-card orders, after the tester says yes. For each planned
// order it opens the checkout, fills it with test data and the platform's
// public test card, submits once, then accepts or declines every upsell and
// stops at the receipt. It records the order and upsell calls (redacted),
// dataLayer events and the parameters on every page, like the recorder.
// One plan = one order. Nothing is ever resubmitted once an order call has
// been sent.
const fs = require('fs');
const path = require('path');
const { assertion, pageLabel } = require('../lib/util');
const PW = require('./playwright');
const REC = require('./record');

const CARDS = {
  standard: { number: '6011111111111117', label: 'standard test card' },
  '3ds': { number: '6011000990139424', label: '3-D Secure test card' },
};
const TEST_PARAMS = { utm_source: 'next-launch-check', utm_medium: 'qa', utm_campaign: 'launch-check-order' };

// Field names a Campaign Cart checkout uses, on SDK 0.4.x and 0.5.
const API = { fname: 'first_name', lname: 'last_name', address1: 'line1', province: 'state', postal: 'postcode', phone: 'phone_number' };
const KEY05 = { 'cc-number': 'card.number', cvv: 'card.cvv', 'cc-expiry': 'card.expiry', phone: 'phone' };
function field(name) {
  const api = API[name];
  const keys = [...new Set([KEY05[name] || api || name, ...(api ? [api] : [])])];
  return [`[data-next-checkout-field="${name}"]`, ...(api ? [`[data-next-checkout-field="${api}"]`] : []), ...keys.map((k) => `[data-next-field="${k}"]`)].join(', ');
}
const NUMBER_FRAME = 'iframe[id^="spreedly-number-frame"], iframe[id^="spreedly-hosted-number"]';
const CVV_FRAME = 'iframe[id^="spreedly-cvv-frame"], iframe[id^="spreedly-hosted-cvv"]';
const ACCEPT = '[data-next-upsell-action="add"], [data-next-upsell-action="accept"], [data-next-action="acceptUpsell"]';
const DECLINE = '[data-next-upsell-action="skip"], [data-next-upsell-action="decline"], [data-next-action="skipUpsell"]';
const RECEIPT = '[data-next-order-items], [data-next-order], meta[name="next-page-type"][content="receipt"]';

// "standard:accept", "3ds:decline@2" → { card, upsell, pkg }
function parsePlans(spec) {
  return String(spec || 'standard:accept').split(',').map((s) => s.trim()).filter(Boolean).map((s) => {
    const m = /^(standard|3ds)(?::(accept|decline|accept-decline|decline-accept))?(?:@(\d+))?$/i.exec(s);
    if (!m) throw new Error(`bad order plan "${s}": use card[:accept|decline|accept-decline][@package], e.g. standard:accept,3ds:decline@2`);
    return { card: m[1].toLowerCase(), upsell: (m[2] || 'accept').toLowerCase(), pkg: m[3] ? parseInt(m[3], 10) : null, label: s.replace(/[^a-z0-9]+/gi, '-').toLowerCase() };
  });
}

async function usable(loc) {
  try { return (await loc.count()) > 0 && (await loc.isVisible()); } catch (_) { return false; }
}

async function fill(page, name, value, required = true) {
  const loc = page.locator(field(name)).first();
  if (!(await usable(loc))) { if (required) throw new Error(`checkout field "${name}" not found`); return false; }
  await loc.fill(value, { timeout: 5000 });
  return true;
}

async function choose(page, name, wanted, required = true) {
  const loc = page.locator(field(name)).first();
  if (!(await usable(loc))) { if (required) throw new Error(`checkout select "${name}" not found`); return null; }
  const tag = await loc.evaluate((el) => el.tagName.toLowerCase());
  if (tag !== 'select') { await loc.fill(wanted); return wanted; }
  const options = await loc.evaluate((el) => [...el.options].map((o) => o.value).filter(Boolean));
  const pick = options.includes(wanted) ? wanted : options[0];
  if (pick) await loc.selectOption(pick, { timeout: 5000 });
  return pick || null;
}

async function fillCheckout(page, d) {
  const reveal = page.locator('.checkout-form--reveal:not(.is-revealed) [data-checkout-reveal-trigger]').first();
  if (await usable(reveal)) await reveal.click().catch(() => {});
  await fill(page, 'email', d.email);
  await fill(page, 'fname', d.firstName);
  await fill(page, 'lname', d.lastName);
  const phone = page.locator(field('phone')).first();
  if (d.phone || (await usable(phone) && await phone.evaluate((el) => el.required))) await fill(page, 'phone', d.phone || '+16505550100', false);
  const country = await choose(page, 'country', d.country, false);
  await fill(page, 'address1', d.address1);
  await page.keyboard.press('Escape').catch(() => {});
  await fill(page, 'city', d.city, false);
  await choose(page, 'province', d.province, false);
  await fill(page, 'postal', d.postal, false);
  // Close any address-suggestion list so it cannot cover the submit button.
  await page.keyboard.press('Escape').catch(() => {});
  const close = page.locator('[class*="suggestion"] [aria-label*="close" i], [class*="autocomplete"] button[aria-label*="close" i], .pac-container').first();
  if (await usable(close)) await close.click().catch(() => {});
  await page.locator('h1, h2').first().click({ timeout: 2000 }).catch(() => {});
  const same = page.locator('#use_shipping_address, [data-next-field="billing_same_as_shipping"]').first();
  if (await same.count().catch(() => 0)) await same.check({ timeout: 3000 }).catch(() => {});
  return { country };
}

async function fillCard(page, card) {
  for (const sel of ['#combo_mode_credit', '[data-next-payment-method="credit"]', '[data-next-payment-method="card"]', 'input[name="payment_method"][value="credit"]', 'input[name="payment_method"][value="card"]', '[data-next-field="payment_method"][value="card_token"]']) {
    const t = page.locator(sel).first();
    if (await t.count().catch(() => 0)) { await t.click({ force: true }).catch(() => {}); break; }
  }
  const expiry = page.locator(field('cc-expiry')).first();
  const month = page.locator(field('exp-month')).first();
  if (!(await usable(month)) && await usable(expiry)) await expiry.fill('12 / 30');
  else { await choose(page, 'exp-month', '12', false); await choose(page, 'exp-year', '2030', false); }
  await page.waitForFunction(() => !document.querySelector('.next-loading-spreedly'), null, { timeout: 20000 }).catch(() => {});
  const hosted = (await page.locator(NUMBER_FRAME).count()) > 0;
  if (hosted) {
    const num = page.frameLocator(NUMBER_FRAME).locator('input').first();
    const cvv = page.frameLocator(CVV_FRAME).locator('input').first();
    await num.click(); await num.pressSequentially(card, { delay: 20 });
    await cvv.click(); await cvv.pressSequentially('123', { delay: 20 });
  } else {
    await fill(page, 'cc-number', card);
    await fill(page, 'cvv', '123');
  }
  return hosted ? 'hosted card fields' : 'plain card fields';
}

async function submit(page) {
  const form = 'form[data-next-checkout="form"], form[data-next-checkout=""], form[data-next-checkout-step], form';
  const notWallet = ':not([data-next-express-checkout]):not([data-next-express-checkout] *)';
  const btn = page.locator([`[os-checkout-payment="combo"]`, ...['button[type="submit"]', 'input[type="submit"]'].map((c) => `${form.split(', ')[0]} ${c}${notWallet}`), `button[type="submit"]${notWallet}`].join(', ')).first();
  await btn.waitFor({ state: 'visible', timeout: 10000 });
  await btn.scrollIntoViewIfNeeded().catch(() => {});
  await btn.click();
}

// Best effort for a 3-D Secure challenge: click an approve/complete button in
// any frame. When none is found the order stays for a person to finish.
async function passChallenge(page) {
  for (const frame of page.frames()) {
    const b = frame.locator('button, input[type="submit"], a').filter({ hasText: /complete|authenticate|authorize|approve|submit|continue|success/i }).first();
    if (await b.count().catch(() => 0) && await b.isVisible().catch(() => false)) { await b.click().catch(() => {}); return true; }
  }
  return false;
}

async function atReceipt(page) {
  return page.evaluate((sel) => !!document.querySelector(sel) || /\/(receipt|thank-?you|confirmation)(\/|\.html?|$)/i.test(location.pathname), RECEIPT).catch(() => false);
}

async function placeOne(browser, ctx, plan, n) {
  const log = PW.newLog();
  const dry = !!ctx.options.dryRun;
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  // A rehearsal keeps order calls blocked as a second safeguard.
  await PW.guard(context, { liveBeacons: ctx.options.liveBeacons, log, blockOrders: dry });
  const { events, pages, calls } = await REC.instrument(context);
  const page = await context.newPage();
  REC.watchPages(page, pages);
  const label = `${n}-${plan.label}`;
  const steps = [];
  let receipt = false;
  let error = null;
  try {
    const u = new URL(ctx.entryUrl);
    for (const [k, v] of Object.entries(TEST_PARAMS)) if (!u.searchParams.has(k)) u.searchParams.set(k, v);
    await page.goto(u.href, { waitUntil: 'load', timeout: 60000 });
    await page.waitForFunction(() => document.documentElement.hasAttribute('data-next-ready') || document.documentElement.classList.contains('next-display-ready'), null, { timeout: 15000 }).catch(() => {});
    steps.push('checkout opened');
    if (plan.pkg) {
      const cards = page.locator('[data-next-bundle-card], [data-next-package-id][data-next-selector-card], [data-next-selector-card]');
      const count = await cards.count();
      if (plan.pkg > count) throw new Error(`package ${plan.pkg} asked for, but the page shows ${count} package option(s)`);
      await cards.nth(plan.pkg - 1).click();
      steps.push(`package ${plan.pkg} selected`);
    }
    const filled = await fillCheckout(page, ctx.customer);
    steps.push(`customer filled (country ${filled.country || 'n/a'})`);
    steps.push(`card filled (${await fillCard(page, CARDS[plan.card].number)})`);
    if (calls.some((c) => c.kind === 'order')) throw new Error('an order call was already sent before submit; stopping');
    if (dry) {
      const held = await page.frameLocator(NUMBER_FRAME).locator('input').first().inputValue().catch(() => null);
      steps.push(held ? `card number field holds ${held.replace(/\D/g, '').length} digits` : 'card number typed');
      steps.push('stopped before submit (rehearsal)');
      throw Object.assign(new Error('rehearsal'), { rehearsal: true });
    }
    await submit(page);
    steps.push('submitted once');
    // Wait for the order call and the next page; help a 3DS challenge along.
    const deadline = Date.now() + 60000;
    while (Date.now() < deadline) {
      if (await atReceipt(page)) { receipt = true; break; }
      if (await page.locator(ACCEPT).count().catch(() => 0)) break;
      if (plan.card === '3ds') await passChallenge(page);
      await page.waitForTimeout(1000);
    }
    const order = calls.find((c) => c.kind === 'order');
    if (!order) throw new Error('no order call was sent after submit (a form error, or the card was not accepted)');
    // Upsells.
    let step = 0;
    while (!receipt && step < 6) {
      const acceptBtn = page.locator(ACCEPT).first();
      const declineBtn = page.locator(DECLINE).first();
      if (!(await acceptBtn.count().catch(() => 0)) && !(await declineBtn.count().catch(() => 0))) break;
      const mode = plan.upsell === 'accept-decline' ? (step % 2 ? 'decline' : 'accept') : plan.upsell === 'decline-accept' ? (step % 2 ? 'accept' : 'decline') : plan.upsell;
      const target = mode === 'accept' && await acceptBtn.count() ? acceptBtn : declineBtn;
      const before = page.url();
      await target.scrollIntoViewIfNeeded().catch(() => {});
      await Promise.all([page.waitForURL((x) => x.href !== before, { timeout: 30000 }).catch(() => null), target.click()]);
      steps.push(`upsell ${step + 1}: ${mode} on ${pageLabel(before)}`);
      await page.waitForTimeout(1500);
      receipt = await atReceipt(page);
      step++;
    }
    if (!receipt) receipt = await atReceipt(page);
    if (receipt) await page.waitForTimeout(4000); // let the receipt events fire
  } catch (e) {
    if (!e.rehearsal) error = String(e.message || e).slice(0, 300);
  }
  const file = `order-${label}${dry ? '-rehearsal' : ''}.jpg`;
  await page.screenshot({ path: path.join(ctx.outDir, 'screenshots', file), fullPage: true, type: 'jpeg', quality: 55 }).catch(() => {});
  await context.close();
  const rec = REC.toRecord(ctx.entryUrl, { pages, calls, events, beacons: log.beacons.length, receiptReached: receipt });
  rec.plan = plan; rec.steps = steps; rec.error = error; rec.rehearsal = dry;
  if (dry) {
    const ok = !error && steps.includes('stopped before submit (rehearsal)');
    return { rec, rows: [assertion({ id: `order:rehearsal:${label}`, family: 'order-rehearsal', status: ok ? 'pass' : 'fail', severity: 'warn', message: ok ? `Rehearsal: the checkout could be filled with the ${CARDS[plan.card].label} and was ready to submit. No order was sent.` : `Rehearsal stopped: ${error}`, evidence: { steps }, screenshots: [path.join('screenshots', file)] })] };
  }
  const rows = REC.judge(rec, label).map((r) => (r.id.startsWith('order:created:') ? { ...r, message: `${CARDS[plan.card].label}, upsells: ${plan.upsell}${plan.pkg ? `, package ${plan.pkg}` : ''}. ${r.message}${error ? ` Stopped: ${error}` : ''}`, screenshots: [path.join('screenshots', file)], evidence: { steps, order: r.evidence } } : r));
  return { rec, rows };
}

async function run(ctx) {
  const loaded = PW.load();
  if (!loaded) throw new Error('Playwright is not installed; automated orders need it.');
  const plans = parsePlans(ctx.options.orders);
  const max = ctx.options.maxOrders || 4;
  if (plans.length > max) throw Object.assign(new Error(`${plans.length} orders planned, more than the cap of ${max}. Raise --max-orders only if the tester agreed to that many.`), { code: 3 });
  const browser = await loaded.pw.chromium.launch({ headless: !ctx.options.headed });
  const records = [];
  const rows = [];
  try {
    for (let i = 0; i < plans.length; i++) {
      const r = await placeOne(browser, ctx, plans[i], i + 1);
      records.push(r.rec);
      rows.push(...r.rows);
    }
  } finally {
    await browser.close();
  }
  fs.writeFileSync(path.join(ctx.outDir, 'order-record.json'), JSON.stringify(records, null, 2) + '\n');
  return { rows, records };
}

module.exports = { run, parsePlans, field, CARDS };
