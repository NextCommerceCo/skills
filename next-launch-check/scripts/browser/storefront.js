'use strict';
// Storefront browser checks: search with a nonsense term shows an empty
// state, and the login form refuses an empty submit. All write requests are
// blocked, so no account, cart or order is created.
const fs = require('fs');
const path = require('path');
const { assertion } = require('../lib/util');
const PW = require('./playwright');
const probes = require('./probes');

async function run(ctx) {
  const loaded = PW.load();
  if (!loaded) return [assertion({ id: 'storefront:browser-unavailable', family: 'storefront-search', status: 'skipped', severity: 'info', message: 'Playwright is not installed, so search and login were not exercised.' })];
  const out = [];
  const shots = path.join(ctx.outDir, 'screenshots');
  fs.mkdirSync(shots, { recursive: true });
  const browser = await loaded.pw.chromium.launch();
  const open = async () => {
    const log = PW.newLog();
    log.blockAllWrites = true;
    const context = await browser.newContext({ viewport: { width: 1366, height: 900 } });
    await PW.guard(context, { liveBeacons: false, log });
    return { context, tab: await context.newPage() };
  };
  try {
    {
      const { context, tab } = await open();
      const errors = [];
      tab.on('pageerror', (e) => errors.push(String(e.message || e).slice(0, 150)));
      await tab.goto(ctx.entryUrl, { waitUntil: 'load', timeout: 45000 });
      const box = tab.locator('input[type="search"], input[name="q"], input[name="search"], input[placeholder*="search" i]').first();
      if (await box.count()) {
        if (!(await box.isVisible())) { await tab.locator('[aria-label*="search" i], .search-toggle, [data-search-toggle]').first().click({ timeout: 3000 }).catch(() => {}); }
        await box.fill('zzqx launch check').catch(() => {});
        await Promise.all([tab.waitForLoadState('load').catch(() => {}), box.press('Enter').catch(() => {})]);
        await tab.waitForTimeout(1500);
        const file = 'storefront-search-empty.jpg';
        await tab.screenshot({ path: path.join(shots, file), fullPage: false, type: 'jpeg', quality: 60 });
        const s = await tab.evaluate(probes.snapshot);
        const status = errors.length || s.badText.length ? 'fail' : 'manual_review';
        out.push(assertion({ id: 'storefront-search:empty', family: 'storefront-search', page: '/', status, severity: 'warn', message: status === 'fail' ? 'Searching for a nonsense term caused errors on the page.' : 'Searched for a nonsense term. Confirm the screenshot shows a clean "no results" message.', evidence: [...errors, ...s.badText], screenshots: [path.join('screenshots', file)] }));
      } else {
        out.push(assertion({ id: 'storefront-search:empty', family: 'storefront-search', status: 'manual_review', severity: 'info', message: 'No search box found on the home page. If the store has search, check it by hand (S-02).' }));
      }
      await context.close();
    }
    {
      const { context, tab } = await open();
      const candidates = ['/login', '/accounts/login/', '/account/login', '/customer/login'];
      let found = false;
      for (const c of candidates) {
        const r = await tab.goto(new URL(c, ctx.entryUrl).href, { waitUntil: 'load', timeout: 30000 }).catch(() => null);
        if (!r || r.status() >= 400) continue;
        const pwd = tab.locator('input[type="password"]').first();
        if (!(await pwd.count())) continue;
        found = true;
        const form = pwd.locator('xpath=ancestor::form[1]');
        const submit = form.locator('[type="submit"], button:not([type="button"])').first();
        await submit.click({ timeout: 4000 }).catch(() => {});
        await tab.waitForTimeout(1200);
        const st = await tab.evaluate(probes.formState);
        const file = 'storefront-login-empty.jpg';
        await tab.screenshot({ path: path.join(shots, file), type: 'jpeg', quality: 60 });
        out.push(st.visibleErrors || st.invalidFields
          ? assertion({ id: 'storefront-login:empty', family: 'storefront-login', page: new URL(tab.url()).pathname, status: 'pass', severity: 'warn', message: 'Login refuses an empty submit and shows an error.', screenshots: [path.join('screenshots', file)] })
          : assertion({ id: 'storefront-login:empty', family: 'storefront-login', page: new URL(tab.url()).pathname, status: 'manual_review', severity: 'warn', message: 'Submitted the empty login form; no error was visible to this check. Look at the screenshot.', screenshots: [path.join('screenshots', file)] }));
        break;
      }
      if (!found) out.push(assertion({ id: 'storefront-login:empty', family: 'storefront-login', status: 'skipped', severity: 'info', message: 'No login page found at the usual addresses.' }));
      await context.close();
    }
  } finally {
    await browser.close();
  }
  return out;
}

module.exports = { run };
