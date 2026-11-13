'use strict';
// Browser lane for funnels: renders each page in Chromium with vendor
// beacons answered locally and order calls blocked, then reports runtime
// errors, SDK readiness, tags actually loaded, layout at phone widths and
// zoom levels, parameter carry-through, content toggles, a comma-decimal
// locale pass and checkout form errors. It never submits an order.
const fs = require('fs');
const path = require('path');
const { assertion, pathKey, pageLabel } = require('../lib/util');
const PW = require('./playwright');
const probes = require('./probes');
const { isCommerce } = require('../lib/detect');

const TEST_PARAMS = { utm_source: 'next-launch-check', utm_medium: 'qa', utm_campaign: 'launch-check' };
const TOGGLES = ['exit', 'timer', 'reviews', 'loading', 'banner', 'seen'];
const IGNORE_CONSOLE = [/favicon\.ico/i, /net::ERR_ABORTED/i, /ERR_BLOCKED_BY_CLIENT/i, /blockedbyclient/i, /Failed to load resource.*204/i];

function withParams(url, params) {
  const u = new URL(url);
  for (const [k, v] of Object.entries(params)) if (!u.searchParams.has(k)) u.searchParams.set(k, v);
  return u.href;
}

// Full-page screenshots, capped at 6000 CSS px so a very long page does not
// make a huge file. JPEG keeps them small enough for the PDF.
async function capture(tab, file, quality = 55) {
  const h = await tab.evaluate(() => Math.max(document.documentElement.scrollHeight, document.body ? document.body.scrollHeight : 0)).catch(() => 6000);
  const w = (tab.viewportSize() || { width: 1366 }).width;
  await tab.screenshot({ path: file, fullPage: true, clip: { x: 0, y: 0, width: w, height: Math.min(h, 6000) }, type: 'jpeg', quality });
}

function shotName(label, suffix) {
  return `${(label === '/' ? 'root' : label.replace(/^\//, '')).replace(/[^a-z0-9]+/gi, '-').slice(0, 50)}-${suffix}.jpg`;
}

async function settle(page, sdkExpected, timeout = 10000) {
  if (sdkExpected) {
    await page.waitForFunction(() => document.documentElement.hasAttribute('data-next-ready') || document.documentElement.classList.contains('next-display-ready'), null, { timeout }).catch(() => {});
  }
  await page.waitForTimeout(800);
}

async function run(ctx) {
  const loaded = PW.load();
  if (!loaded) {
    return [assertion({ id: 'browser:unavailable', family: 'browser-runtime', status: 'skipped', severity: 'info', message: 'Playwright is not installed, so the browser lane did not run. Install it (see SKILL.md) and run again with --browser.' })];
  }
  const { pw } = loaded;
  const out = [];
  const shots = path.join(ctx.outDir, 'screenshots');
  fs.mkdirSync(shots, { recursive: true });
  const rel = (f) => path.join('screenshots', f);
  const browser = await pw.chromium.launch();
  const allPages = (ctx.allPages || ctx.pages).filter((p) => p.ok);
  const pages = ctx.pages.filter((p) => p.ok).slice(0, ctx.options.maxBrowserPages || 8);
  const newContext = async (opts = {}) => {
    const log = PW.newLog();
    const context = await browser.newContext({ viewport: { width: 1366, height: 900 }, ...opts });
    await PW.guard(context, { liveBeacons: ctx.options.liveBeacons, log });
    return { context, log };
  };
  try {
    // 1. Runtime, tags and layout, per page.
    for (const p of pages) {
      const sdkExpected = p.sdk.present;
      const commerce = isCommerce(p);
      const base = { page: p.label, url: p.url };
      const desk = await newContext();
      const tab = await desk.context.newPage();
      const errors = [];
      tab.on('console', (m) => { if (m.type() === 'error' && !IGNORE_CONSOLE.some((re) => re.test(m.text()))) errors.push(m.text().slice(0, 200)); });
      tab.on('pageerror', (e) => errors.push(`page error: ${String(e.message || e).slice(0, 200)}`));
      tab.on('response', (r) => { if (r.status() >= 400 && /\.(js|css)(\?|$)/i.test(r.url())) errors.push(`HTTP ${r.status()} ${r.url().slice(0, 150)}`); });
      let snap = null;
      try {
        await tab.goto(withParams(p.finalUrl, TEST_PARAMS), { waitUntil: 'load', timeout: 45000 });
        await settle(tab, sdkExpected);
        snap = await tab.evaluate(probes.snapshot);
        await capture(tab, path.join(shots, shotName(p.label, 'desktop')), 60);
      } catch (e) {
        errors.push(`navigation failed: ${String(e.message || e).slice(0, 200)}`);
      }
      const uniqErrors = [...new Set(errors)];
      if (snap && sdkExpected && !snap.ready) {
        out.push(assertion({ ...base, id: `browser-runtime:${p.label}:sdk-ready`, family: 'browser-runtime', status: 'fail', severity: commerce ? 'blocker' : 'warn', message: 'The SDK did not reach ready within 10 seconds (no data-next-ready attribute on 0.5, no next-display-ready class on 0.4). Prices and buttons may not work. Open the page with ?debug=1 (0.5) or ?debugger=true (0.4) to see why.', screenshots: [rel(shotName(p.label, 'desktop'))] }));
      } else if (snap && sdkExpected) {
        out.push(assertion({ ...base, id: `browser-runtime:${p.label}:sdk-ready`, family: 'browser-runtime', status: 'pass', severity: 'blocker', message: `SDK ready (${snap.readySignal}).` }));
      }
      out.push(uniqErrors.length
        ? assertion({ ...base, id: `browser-runtime:${p.label}:errors`, family: 'browser-runtime', status: 'warn', severity: 'warn', message: `${uniqErrors.length} console or script error(s).`, evidence: uniqErrors.slice(0, 12) })
        : assertion({ ...base, id: `browser-runtime:${p.label}:errors`, family: 'browser-runtime', status: 'pass', severity: 'warn', message: 'No console or script errors.' }));
      if (snap && snap.badText.length) {
        out.push(assertion({ ...base, id: `browser-runtime:${p.label}:bad-text`, family: 'browser-runtime', status: 'fail', severity: 'warn', message: 'Visible text shows script output that did not render.', evidence: snap.badText }));
      }
      // Tags: libraries requested and beacons that would have been sent.
      if (snap) {
        const libs = [...new Set(desk.log.tagLibs.map((t) => t.lib))];
        const beacons = {};
        for (const b of desk.log.beacons) beacons[b.vendor] = (beacons[b.vendor] || 0) + 1;
        const events = [...new Set([...snap.dataLayer, ...snap.nextDataLayer])].filter((e) => !/^gtm\.|^(js|config|set|consent)$/.test(e));
        const expectedGtm = p.tracking.gtm.length > 0;
        const missing = expectedGtm && !libs.includes('gtm');
        const t = p.tracking;
        const hasSourceIds = !!(t.gtm.length || t.ga4.length || t.meta.length || t.tiktok.length || t.everflow.present);
        // Google Analytics and Everflow, as loaded in the browser (often through GTM).
        const ga = libs.includes('gtag') || !!beacons.ga4 || t.ga4.length > 0;
        out.push(ga
          ? assertion({ ...base, id: `tags-observed:${p.label}:ga4`, family: 'tags-observed', status: 'pass', severity: 'warn', message: `Google Analytics loads${beacons.ga4 ? ` and tried to send ${beacons.ga4} hit(s)` : ''}${libs.includes('gtag') ? ' (gtag.js)' : ''}.` })
          : assertion({ ...base, id: `tags-observed:${p.label}:ga4`, family: 'tags-observed', status: 'warn', severity: 'warn', message: `Google Analytics did not load on this page${libs.includes('gtm') ? ', even through GTM' : ''}. If GA4 runs only server-side or after consent, answer n/a.` }));
        const ef = libs.includes('everflow') || !!beacons.everflow || t.everflow.present;
        out.push(ef
          ? assertion({ ...base, id: `tags-observed:${p.label}:everflow`, family: 'tags-observed', status: 'pass', severity: 'warn', message: `Everflow click script loads${beacons.everflow ? ` and tried to send ${beacons.everflow} call(s)` : ''}.` })
          : assertion({ ...base, id: `tags-observed:${p.label}:everflow`, family: 'tags-observed', status: 'manual_review', severity: 'info', message: 'No Everflow script on this page. Confirm: pass if the store uses the NEXT Everflow app (it reports conversions server-side), n/a if this funnel does not use Everflow, fail if the click script is missing.' }));
        out.push(assertion({ ...base, id: `tags-observed:${p.label}`, family: 'tags-observed', status: missing ? 'fail' : libs.length || Object.keys(beacons).length ? 'pass' : 'warn', severity: missing || hasSourceIds ? 'warn' : 'info', message: missing ? 'GTM is in the source but gtm.js never loaded.' : libs.length || Object.keys(beacons).length ? `Loaded: ${libs.join(', ') || 'no tag libraries'}. Beacons${ctx.options.liveBeacons ? ' sent' : ' held back'}: ${Object.entries(beacons).map(([k, v]) => `${k} ×${v}`).join(', ') || 'none'}.` : 'No tag library loaded and no beacon was attempted on this page.', evidence: events.length ? [`dataLayer events: ${events.join(', ')}`] : undefined }));
      }
      // Images that failed to load (broken icon or empty box for shoppers).
      if (snap) {
        const broken = await tab.evaluate(() => [...document.images].filter((im) => { const r = im.getBoundingClientRect(); return im.complete && im.naturalWidth === 0 && (im.currentSrc || im.src) && r.width > 0 && r.height > 0; }).map((im) => (im.currentSrc || im.src).slice(0, 160))).catch(() => []);
        out.push(broken.length
          ? assertion({ ...base, id: `images:${p.label}`, family: 'images', status: 'fail', severity: 'warn', message: `${broken.length} image(s) do not load.`, evidence: broken.slice(0, 8), screenshots: [rel(shotName(p.label, 'desktop'))] })
          : assertion({ ...base, id: `images:${p.label}`, family: 'images', status: 'pass', severity: 'warn', message: 'Every visible image loads.' }));
      }
      // Layout: mobile, then mobile zoomed to 125% and 150% (simulated by a
      // narrower viewport at the same device scale, as browser zoom does).
      const layouts = [['mobile', 390], ['zoom-125', 312], ['zoom-150', 260]];
      const overflow = [];
      for (const [name, width] of layouts) {
        const mob = await newContext({ viewport: { width, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2, userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1 next-launch-check' });
        const mt = await mob.context.newPage();
        try {
          await mt.goto(withParams(p.finalUrl, TEST_PARAMS), { waitUntil: 'load', timeout: 45000 });
          await settle(mt, sdkExpected, 8000);
          const s = await mt.evaluate(probes.snapshot);
          const file = shotName(p.label, name);
          await capture(mt, path.join(shots, file), 55);
          if (s.overflowX > 4) overflow.push({ name, width, by: s.overflowX, file: rel(file), culprits: await mt.evaluate(probes.widest) });
        } catch (e) {
          overflow.push({ name, width, error: String(e.message || e).slice(0, 120) });
        }
        await mob.context.close();
      }
      for (const [name] of layouts) {
        const o = overflow.find((x) => x.name === name);
        const label = name === 'mobile' ? 'a 390 px phone screen' : name === 'zoom-125' ? 'phone zoom 125%' : 'phone zoom 150%';
        out.push(o && !o.error
          ? assertion({ ...base, id: `responsive:${p.label}:${name}`, family: 'responsive', status: 'warn', severity: 'warn', message: `At ${label}, content runs ${o.by}px off the right edge. Shoppers have to scroll sideways.`, evidence: o.culprits.length ? o.culprits : undefined, screenshots: [o.file] })
          : o && o.error
            ? assertion({ ...base, id: `responsive:${p.label}:${name}`, family: 'responsive', status: 'skipped', severity: 'warn', message: `Could not load at ${label}: ${o.error}` })
            : assertion({ ...base, id: `responsive:${p.label}:${name}`, family: 'responsive', status: 'pass', severity: 'warn', message: `No sideways overflow at ${label}.`, screenshots: [rel(shotName(p.label, name))] }));
      }
      await desk.context.close();
    }

    // 2. Parameter carry-through across content pages up to the checkout.
    out.push(...(await propagation(ctx, allPages, newContext)));
    // 3. Content toggles on the entry page and the checkout.
    out.push(...(await toggles(ctx, pages, newContext, shots, rel)));
    // 4. Locale pass and form errors on the checkout.
    const checkout = pages.find((p) => p.role === 'checkout');
    if (checkout) {
      out.push(...(await addonsAndCurrency(ctx, checkout, browser)));
      out.push(...(await locale(ctx, checkout, browser)));
      out.push(...(await formNegative(ctx, checkout, newContext, shots, rel)));
    } else {
      out.push(assertion({ id: 'form-negative', family: 'form-negative', status: 'skipped', severity: 'info', message: 'No checkout page found, so form error cases and the locale pass did not run.' }));
    }
  } finally {
    await browser.close();
  }
  return out;
}

async function propagation(ctx, pages, newContext) {
  const out = [];
  // Only when a presell or landing page is being tested.
  if (!ctx.pages.some((p) => ['presell', 'landing'].includes(p.role))) return out;
  const content = pages.filter((p) => !isCommerce(p));
  if (!content.length) return out;
  const paths = pages.map((p) => { const u = new URL(p.finalUrl); let s = u.pathname.replace(/\/index\.html?$/i, '/'); if (s.length > 1 && s.endsWith('/')) s = s.slice(0, -1); return s; });
  const params = { ...TEST_PARAMS, ...(ctx.options.params || {}) };
  const { context } = await newContext();
  const tab = await context.newPage();
  try {
    await tab.goto(withParams(content[0].finalUrl, params), { waitUntil: 'load', timeout: 45000 });
    const hops = [];
    for (let i = 0; i < 4; i++) {
      await tab.waitForTimeout(800);
      const links = (await tab.evaluate(probes.linksTo, paths)).filter((l) => l.visible);
      if (!links.length) break;
      const from = pageLabel(tab.url());
      const target = links[0];
      const hrefCarries = Object.keys(params).every((k) => new URL(target.href).searchParams.has(k));
      await Promise.all([tab.waitForNavigation({ timeout: 20000 }).catch(() => null), tab.click(`a[href="${new URL(target.href).pathname}"], a[href^="${new URL(target.href).pathname}?"], a[href="${target.href}"]`).catch(async () => { await tab.goto(target.href); })]);
      const landed = new URL(tab.url());
      const lost = Object.keys(params).filter((k) => !landed.searchParams.has(k));
      hops.push({ from, to: pageLabel(landed.href), lost, hrefCarries });
      if (pages.find((p) => pathKey(p.finalUrl) === pathKey(landed.href) && isCommerce(p))) break;
    }
    if (!hops.length) {
      out.push(assertion({ id: 'param-propagation', family: 'param-propagation', status: 'manual_review', severity: 'warn', message: 'Could not find a visible link from the entry page to the next funnel page. Click through by hand with ?utm_source=test and check the parameters survive.' }));
    }
    for (const h of hops) {
      const fromPage = pages.find((p) => p.label === h.from);
      const sdkKeeps = fromPage && fromPage.sdk.present;
      out.push(h.lost.length
        ? assertion({ id: `param-propagation:${h.from}->${h.to}`, family: 'param-propagation', page: h.from, status: sdkKeeps ? 'warn' : 'fail', severity: 'warn', message: sdkKeeps
          ? `Going from ${h.from} to ${h.to}, the URL loses ${h.lost.join(', ')}. The SDK on ${h.from} stores attribution in the session, so the order may still carry them: confirm with a test order (order:attribution). Pages or tags that read parameters from the URL will not see them.`
          : `Going from ${h.from} to ${h.to} drops ${h.lost.join(', ')}, and no SDK on ${h.from} stores them. Ad and affiliate attribution needs these to reach the checkout and the order.`, actual: h })
        : assertion({ id: `param-propagation:${h.from}->${h.to}`, family: 'param-propagation', page: h.from, status: 'pass', severity: 'warn', message: `${h.from} → ${h.to} keeps all test parameters.` }));
    }
  } catch (e) {
    out.push(assertion({ id: 'param-propagation', family: 'param-propagation', status: 'skipped', severity: 'info', message: `Could not run: ${String(e.message || e).slice(0, 150)}` }));
  } finally {
    await context.close();
  }
  return out;
}

// Which content toggles each page type is expected to support.
const TOGGLES_BY_ROLE = {
  presell: ['exit', 'timer', 'reviews', 'loading', 'banner', 'seen'],
  landing: ['exit', 'timer', 'reviews', 'loading', 'banner', 'seen'],
  checkout: ['exit', 'timer', 'reviews', 'loading', 'banner'],
  upsell: ['timer', 'reviews', 'banner'],
};

async function toggles(ctx, pages, newContext, shots, rel) {
  const out = [];
  const targets = pages.filter((p) => p.in_scope !== false && TOGGLES_BY_ROLE[p.role]).slice(0, 5);
  for (const p of targets) {
    const names = ctx.options.toggles || TOGGLES_BY_ROLE[p.role];
    const { context } = await newContext();
    const tab = await context.newPage();
    const base = { family: 'param-toggles', page: p.label, url: p.url };
    try {
      await tab.goto(p.finalUrl, { waitUntil: 'load', timeout: 45000 });
      await settle(tab, p.sdk.present, 8000);
      const before = await tab.evaluate(probes.visibleSummary);
      const baseShot = shotName(p.label, 'baseline');
      await capture(tab, path.join(shots, baseShot), 50);
      const WHAT = { timer: 'countdown timer', reviews: 'reviews or testimonials', exit: 'exit-intent pop-up', banner: 'promotional banner', seen: '"As seen on" section', loading: 'loading or progress bar' };
      for (const name of names) {
        const targetsBefore = await tab.evaluate(probes.toggleTargets, name);
        await tab.goto(withParams(p.finalUrl, { [name]: 'n' }), { waitUntil: 'load', timeout: 45000 });
        await settle(tab, p.sdk.present, 8000);
        const after = await tab.evaluate(probes.visibleSummary);
        const targetsAfter = await tab.evaluate(probes.toggleTargets, name);
        const diff = before.elements - after.elements;
        const file = shotName(p.label, `${name}-n`);
        await capture(tab, path.join(shots, file), 50);
        const shotsFor = [rel(baseShot), rel(file)];
        const id = `param-toggles:${p.label}:${name}`;
        if (name === 'exit') {
          // The pop-up is hidden until the shopper tries to leave, so only its presence can be read.
          out.push(targetsBefore.present
            ? assertion({ ...base, id, status: 'manual_review', severity: 'warn', message: `The page has exit-intent pop-up markup (${targetsBefore.sample.join(', ')}). Open it with ?exit=n, move the mouse out of the window, and confirm no pop-up appears.`, screenshots: shotsFor })
            : assertion({ ...base, id, status: 'pass', severity: 'info', message: 'No exit-intent pop-up markup on this page, so ?exit=n has nothing to hide.' }));
          continue;
        }
        if (targetsBefore.visible) {
          const hidden = targetsBefore.visible - targetsAfter.visible;
          const changed = hidden > 0 || diff > 0 || before.textLength - after.textLength > 20;
          out.push(targetsAfter.visible === 0
            ? assertion({ ...base, id, status: 'pass', severity: 'warn', message: `?${name}=n hides the ${WHAT[name]} (${targetsBefore.visible} element(s): ${targetsBefore.sample.join(', ')}).`, screenshots: shotsFor })
            : changed
              // The toggle is wired (something disappeared), but elements that
              // look like a ${name} are still there: a person decides whether they count.
              ? assertion({ ...base, id, status: 'manual_review', severity: 'warn', message: `?${name}=n works: it hid ${hidden > 0 ? hidden : Math.max(diff, 1)} element(s). Still showing ${targetsAfter.visible} element(s) that look like a ${WHAT[name]} (${targetsBefore.sample.join(', ')}). Compare the two screenshots and confirm nothing that should hide is left.`, screenshots: shotsFor })
              : assertion({ ...base, id, status: 'fail', severity: 'warn', message: `?${name}=n changes nothing: ${targetsAfter.visible} ${WHAT[name]} element(s) stay visible (${targetsBefore.sample.join(', ')}). The toggle is not wired on this page.`, screenshots: shotsFor }));
        } else if (diff > 0 || before.textLength - after.textLength > 20) {
          out.push(assertion({ ...base, id, status: 'manual_review', severity: 'warn', message: `?${name}=n hides ${diff > 0 ? `${diff} element(s)` : 'some text'}, but this check could not tell what a ${WHAT[name]} looks like here. Compare the screenshots.`, screenshots: shotsFor }));
        } else {
          out.push(assertion({ ...base, id, status: 'pass', severity: 'info', message: `No ${WHAT[name]} found when the page loads, and ?${name}=n changes nothing. If one appears only after an action (for example after picking a package), check it by hand.`, screenshots: [rel(baseShot)] }));
        }
      }
    } catch (e) {
      out.push(assertion({ ...base, id: `param-toggles:${p.label}`, status: 'skipped', severity: 'info', message: `Could not run: ${String(e.message || e).slice(0, 150)}` }));
    } finally {
      await context.close();
    }
  }
  return out;
}

// Pre-ticked paid add-ons, and whether prices change with the currency.
async function addonsAndCurrency(ctx, p, browser) {
  const out = [];
  const base = { page: p.label, url: p.url };
  const read = async (url) => {
    const log = PW.newLog();
    const context = await browser.newContext({ viewport: { width: 1366, height: 900 } });
    await PW.guard(context, { liveBeacons: false, log });
    const tab = await context.newPage();
    await tab.goto(url, { waitUntil: 'load', timeout: 45000 });
    await settle(tab, p.sdk.present, 10000);
    await tab.waitForTimeout(1500);
    const r = { addons: await tab.evaluate(probes.preselectedAddons), prices: await tab.evaluate(probes.prices), symbol: await tab.evaluate(() => ((document.body && document.body.innerText) || '').match(/[$€£]|\bUSD\b|\bEUR\b|\bGBP\b/g) || []) };
    await context.close();
    return r;
  };
  try {
    const start = await read(p.finalUrl);
    out.push(start.addons.length
      ? assertion({ ...base, id: `addons:${p.label}:preselected`, family: 'addons', status: 'warn', severity: 'warn', message: `${start.addons.length} paid add-on(s) are already selected when the page loads: ${start.addons.map((a) => `"${a.text.slice(0, 60)}"${a.price ? ` (${a.price})` : ''}`).join('; ')}. Pre-ticked paid extras are a chargeback and compliance risk in many markets; confirm the client wants this.`, evidence: start.addons })
      : assertion({ ...base, id: `addons:${p.label}:preselected`, family: 'addons', status: 'pass', severity: 'warn', message: 'No paid add-on is pre-selected.' }));
    // Currency: the SDK takes ?currency=XXX. Prices should change, not just the symbol.
    const baseNums = numbers(start.prices.bound.map((b) => b.text));
    for (const cur of ['GBP', 'EUR']) {
      const other = await read(withParams(p.finalUrl, { currency: cur }));
      const sym = { GBP: '£', EUR: '€' }[cur];
      const switched = other.symbol.includes(sym) || other.symbol.includes(cur);
      if (!switched) { out.push(assertion({ ...base, id: `currency:${p.label}:${cur}`, family: 'currency', status: 'pass', severity: 'info', message: `?currency=${cur} does not switch this checkout (single-currency campaign, or not enabled for ${cur}).` })); continue; }
      const nums = numbers(other.prices.bound.map((b) => b.text));
      const same = nums.length && nums.length === baseNums.length && nums.every((n, i) => Math.abs(n - baseNums[i]) < 0.011);
      out.push(same
        ? assertion({ ...base, id: `currency:${p.label}:${cur}`, family: 'currency', status: 'fail', severity: 'warn', message: `With ?currency=${cur} the page shows ${sym} but the same numbers as the default currency (${other.prices.bound.slice(0, 3).map((b) => b.text).join(', ')}). The campaign charges the same numbers in ${cur} as in the default currency, which is a different real price. Set ${cur} prices in the campaign, or confirm the client wants the same numbers.`, evidence: { default: start.prices.bound.slice(0, 5).map((b) => b.text), [cur]: other.prices.bound.slice(0, 5).map((b) => b.text) } })
        : assertion({ ...base, id: `currency:${p.label}:${cur}`, family: 'currency', status: 'pass', severity: 'warn', message: `${cur} prices differ from the default currency (${other.prices.bound.slice(0, 2).map((b) => b.text).join(', ')}).` }));
    }
  } catch (e) {
    out.push(assertion({ ...base, id: `addons:${p.label}`, family: 'addons', status: 'skipped', severity: 'info', message: `Could not run: ${String(e.message || e).slice(0, 150)}` }));
  }
  return out;
}

function numbers(texts) {
  return texts.map((t) => {
    const digits = t.replace(/[^\d.,]/g, '');
    const m = /^(.*?)[.,](\d{2})$/.exec(digits);
    return m ? parseFloat(m[1].replace(/[.,\s]/g, '') + '.' + m[2]) : parseFloat(digits.replace(/[.,\s]/g, ''));
  });
}

async function locale(ctx, p, browser) {
  const out = [];
  const read = async (loc) => {
    const log = PW.newLog();
    const context = await browser.newContext({ locale: loc, viewport: { width: 1366, height: 900 } });
    await PW.guard(context, { liveBeacons: false, log });
    const tab = await context.newPage();
    await tab.goto(p.finalUrl, { waitUntil: 'load', timeout: 45000 });
    await settle(tab, p.sdk.present, 10000);
    const r = await tab.evaluate(probes.prices);
    const s = await tab.evaluate(probes.snapshot);
    await context.close();
    return { ...r, badText: s.badText };
  };
  try {
    const en = await read('en-US');
    const de = await read('de-DE');
    const a = numbers(en.bound.map((b) => b.text));
    const b = numbers(de.bound.map((x) => x.text));
    const mismatch = en.bound.length === de.bound.length ? en.bound.map((x, i) => ({ key: x.key, en: x.text, de: de.bound[i].text, ok: Math.abs(a[i] - b[i]) < 0.011 || Number.isNaN(a[i]) })).filter((x) => !x.ok) : [];
    if (de.badText.length && !en.badText.length) {
      out.push(assertion({ id: `locale:${p.label}`, family: 'locale', page: p.label, url: p.url, status: 'fail', severity: 'warn', message: 'In a German-locale browser the page shows NaN, undefined or raw template text. Custom code is probably parsing formatted prices.', evidence: de.badText }));
    } else if (mismatch.length) {
      out.push(assertion({ id: `locale:${p.label}`, family: 'locale', page: p.label, url: p.url, status: 'fail', severity: 'warn', message: 'Amounts differ between an en-US and a de-DE browser. Custom code is reading formatted prices.', evidence: mismatch }));
    } else {
      out.push(assertion({ id: `locale:${p.label}`, family: 'locale', page: p.label, url: p.url, status: en.bound.length ? 'pass' : 'manual_review', severity: 'warn', message: en.bound.length ? `${en.bound.length} SDK-bound amount(s) agree between en-US and de-DE browsers.` : 'No SDK-bound amounts found on the checkout to compare.' }));
    }
    if (!en.free.length) {
      out.push(assertion({ id: `locale:${p.label}:typed-amounts`, family: 'locale', page: p.label, url: p.url, status: 'pass', severity: 'warn', message: 'Every visible amount comes from the SDK, so a reprice or currency switch updates all of them.' }));
    } else {
      out.push(assertion({ id: `locale:${p.label}:typed-amounts`, family: 'locale', page: p.label, url: p.url, status: 'manual_review', severity: 'info', message: `${en.free.length} visible amount(s) are typed into the page rather than bound to the SDK. They will not follow a reprice or a currency switch; confirm each is meant to be fixed copy.`, evidence: [...new Set(en.free)].slice(0, 12) }));
    }
  } catch (e) {
    out.push(assertion({ id: `locale:${p.label}`, family: 'locale', page: p.label, status: 'skipped', severity: 'info', message: `Could not run: ${String(e.message || e).slice(0, 150)}` }));
  }
  return out;
}

const SUBMIT_SELECTORS = ['[data-next-checkout-submit]', 'form[data-next-checkout-step] [type="submit"]', 'form[data-next-checkout-step] button:not([type="button"])', '[data-next-checkout="submit"]', 'form [type="submit"]'];
const EMAIL_SELECTORS = ['[data-next-checkout-field="email"]', 'input[type="email"]', 'input[name*="email" i]'];

async function formNegative(ctx, p, newContext, shots, rel) {
  const out = [];
  const { context, log } = await newContext();
  log.blockAllWrites = true; // nothing leaves the browser except page loads
  const tab = await context.newPage();
  const base = { family: 'form-negative', page: p.label, url: p.url };
  try {
    await tab.goto(p.finalUrl, { waitUntil: 'load', timeout: 45000 });
    await settle(tab, p.sdk.present, 10000);
    let submit = null;
    for (const sel of SUBMIT_SELECTORS) {
      const loc = tab.locator(sel).filter({ visible: true }).first();
      if (await loc.count()) { submit = loc; break; }
    }
    if (!submit) {
      const byText = tab.getByRole('button', { name: /complete|place.*order|pay now|submit|buy now|checkout/i }).first();
      if (await byText.count()) submit = byText;
    }
    if (!submit) {
      out.push(assertion({ ...base, id: `form-negative:${p.label}`, status: 'manual_review', severity: 'warn', message: 'Could not find the checkout submit button. Submit the empty form by hand and confirm each required field shows an error.' }));
      return out;
    }
    const startUrl = tab.url();
    await submit.click({ timeout: 5000 }).catch(() => {});
    await tab.waitForTimeout(1500);
    const empty = await tab.evaluate(probes.formState);
    const file = shotName(p.label, 'empty-submit');
    await capture(tab, path.join(shots, file), 55);
    const tried = log.blockedOrders.length;
    if (tried) {
      out.push(assertion({ ...base, id: `form-negative:${p.label}:empty`, status: 'fail', severity: 'blocker', message: 'Submitting the empty checkout tried to create an order (the call was blocked by this check). Required fields are not validated before submit.', screenshots: [rel(file)] }));
    } else if (empty.visibleErrors || empty.invalidFields) {
      out.push(assertion({ ...base, id: `form-negative:${p.label}:empty`, status: 'pass', severity: 'warn', message: `Empty submit is stopped: ${empty.visibleErrors} visible error message(s), ${empty.invalidFields} field(s) marked invalid.`, evidence: empty.sample, screenshots: [rel(file)] }));
    } else {
      out.push(assertion({ ...base, id: `form-negative:${p.label}:empty`, status: 'manual_review', severity: 'warn', message: `Empty submit showed no error this check could see${tab.url() !== startUrl ? ' and the page changed' : ''}. Look at the screenshot.`, screenshots: [rel(file)] }));
    }
    let email = null;
    for (const sel of EMAIL_SELECTORS) {
      const loc = tab.locator(sel).filter({ visible: true }).first();
      if (await loc.count()) { email = loc; break; }
    }
    if (email) {
      await email.fill('not-an-email');
      await email.blur().catch(() => {});
      await submit.click({ timeout: 5000 }).catch(() => {});
      await tab.waitForTimeout(1200);
      const bad = await email.evaluate((el) => ({ invalid: !el.checkValidity() || el.getAttribute('aria-invalid') === 'true' || el.classList.contains('next-error-field') || !!el.closest('.has-error, .is-invalid, .next-error') }));
      const st = await tab.evaluate(probes.formState);
      out.push(bad.invalid || st.visibleErrors
        ? assertion({ ...base, id: `form-negative:${p.label}:email`, status: 'pass', severity: 'warn', message: 'An invalid email is rejected.' })
        : assertion({ ...base, id: `form-negative:${p.label}:email`, status: 'fail', severity: 'warn', message: 'An invalid email ("not-an-email") was not flagged.' }));
    }
    const errorTexts = [...empty.sample];
    out.push(...(await checkoutExtras(ctx, p, tab, log, shots, rel, base, errorTexts)));
    out.push(assertion({ ...base, id: `form-negative:${p.label}:no-order`, status: log.blockedOrders.length ? 'fail' : 'pass', severity: log.blockedOrders.length ? 'blocker' : 'info', message: log.blockedOrders.length ? `The checkout tried to create an order during the error tests ${log.blockedOrders.length} time(s); the calls were blocked. A form with an invalid card or empty fields should not reach the order step.` : `No order was sent. Every write request during this check was blocked (${log.blockedWrites.length} held back).` }));
  } catch (e) {
    out.push(assertion({ ...base, id: `form-negative:${p.label}`, status: 'skipped', severity: 'info', message: `Could not run: ${String(e.message || e).slice(0, 150)}` }));
  } finally {
    await context.close();
  }
  return out;
}

// Text lines that look like form errors and were not on the page before.
async function newErrorLines(tab, beforeText) {
  const now = await tab.evaluate(() => (document.body ? document.body.innerText : '')).catch(() => '');
  const had = new Set(String(beforeText).split('\n').map((l) => l.trim()));
  return [...new Set(now.split('\n').map((l) => l.trim()))]
    .filter((l) => l && l.length < 140 && !had.has(l) && /invalid|not valid|incorrect|declined|required|enter (a|your)|check (your|the)|error|expired|ungültig|inválid|non valide|erforderlich|obligatoire|obligatorio/i.test(l))
    .slice(0, 6);
}

const SAME_AS = '#use_shipping_address, [data-next-field="billing_same_as_shipping"], [data-next-checkout-field="use_shipping_address"], input[name="use_shipping_address"], input[name="billing_same_as_shipping"]';
const BILLING = '[data-next-checkout-field^="billing"], [data-next-field^="billing"], input[name^="billing"], select[name^="billing"]';
const SUGGESTIONS = '.pac-container .pac-item, [class*="autocomplete"] li, [class*="suggestion"] li, [class*="autocomplete-item"], [role="listbox"] [role="option"]';

// Checkout behaviour that needs no order: "same as billing", address
// autocomplete, an invalid card, an invalid CVV, and the language of the
// errors. All writes stay blocked (the caller set log.blockAllWrites).
async function checkoutExtras(ctx, p, tab, log, shots, rel, base, errorTexts) {
  const O = require('./order');
  const out = [];
  const reload = async () => { await tab.goto(p.finalUrl, { waitUntil: 'load', timeout: 45000 }); await settle(tab, p.sdk.present, 10000); };
  // Visible to a shopper: has a box, and no ancestor hides it (display,
  // visibility, opacity, or a collapsed box that clips it).
  const visibleCount = (sel) => tab.evaluate((q) => [...document.querySelectorAll(q)].filter((el) => {
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) return false;
    for (let a = el; a && a !== document.body; a = a.parentElement) {
      const st = getComputedStyle(a);
      if (st.display === 'none' || st.visibility === 'hidden' || st.opacity === '0') return false;
      if (a !== el && /(hidden|clip)/.test(st.overflow + st.overflowY)) {
        const ar = a.getBoundingClientRect();
        if (ar.height < 2 || r.bottom <= ar.top || r.top >= ar.bottom) return false;
      }
    }
    return true;
  }).length, sel).catch(() => 0);

  // Phone required? Then a blank phone cannot go through, which settles that item.
  try {
    await reload();
    const phone = tab.locator(O.field('phone')).first();
    if (await O.usable(phone) && await phone.evaluate((el) => el.required || el.getAttribute('aria-required') === 'true')) {
      out.push(assertion({ ...base, id: `form-negative:${p.label}:phone-required`, status: 'pass', severity: 'warn', message: 'The phone field is required, so a checkout with a blank phone cannot go through by design.' }));
    }
  } catch (_) { /* the order run covers it */ }

  // "Shipping same as billing".
  try {
    const same = tab.locator(SAME_AS).first();
    if (!(await same.count())) {
      out.push(assertion({ ...base, id: `form-negative:${p.label}:same-as-billing`, status: 'pass', severity: 'info', message: 'This checkout has no "billing same as shipping" option, so there is nothing to toggle.' }));
    } else {
      await same.setChecked(true, { force: true, timeout: 3000 }).catch(() => {});
      await tab.waitForTimeout(500);
      const on = await visibleCount(BILLING);
      await same.setChecked(false, { force: true, timeout: 3000 }).catch(() => same.click({ force: true }).catch(() => {}));
      await tab.waitForTimeout(700);
      const off = await visibleCount(BILLING);
      const file = shotName(p.label, 'billing-fields');
      await capture(tab, path.join(shots, file), 50);
      await same.setChecked(true, { force: true, timeout: 3000 }).catch(() => {});
      await tab.waitForTimeout(500);
      const back = await visibleCount(BILLING);
      out.push(off > on && back <= on
        ? assertion({ ...base, id: `form-negative:${p.label}:same-as-billing`, status: 'pass', severity: 'warn', message: `Unticking "same as shipping" shows ${off - on} billing field(s); ticking it again hides them.`, screenshots: [rel(file)] })
        : assertion({ ...base, id: `form-negative:${p.label}:same-as-billing`, status: 'fail', severity: 'warn', message: `Toggling "same as shipping" did not show and hide the billing fields (visible billing fields: ticked ${on}, unticked ${off}, ticked again ${back}).`, screenshots: [rel(file)] }));
    }
  } catch (e) {
    out.push(assertion({ ...base, id: `form-negative:${p.label}:same-as-billing`, status: 'skipped', severity: 'info', message: `Could not run: ${String(e.message || e).slice(0, 120)}` }));
  }

  // Address autocomplete.
  try {
    const cfg = await tab.evaluate(() => { const c = window.nextConfig || window.next_config || {}; return { enabled: c.addressConfig && typeof c.addressConfig.enableAutocomplete === 'boolean' ? c.addressConfig.enableAutocomplete : null, key: !!(c.googleMaps && c.googleMaps.apiKey) }; }).catch(() => ({ enabled: null }));
    const addr = tab.locator(O.field('address1')).first();
    if (cfg.enabled === false) {
      out.push(assertion({ ...base, id: `form-negative:${p.label}:autocomplete`, status: 'pass', severity: 'info', message: 'Address autocomplete is switched off in the campaign config, so there is nothing to check.' }));
    } else if (await O.usable(addr)) {
      const textBefore = await tab.evaluate(() => (document.body ? document.body.innerText : '')).catch(() => '');
      await addr.click().catch(() => {});
      await addr.pressSequentially('1600 Amphitheatre', { delay: 60 });
      await tab.waitForTimeout(3000);
      // Suggestions, by common markup or as new visible lines that extend
      // what was typed (any autocomplete widget shows those).
      const textAfter = await tab.evaluate(() => (document.body ? document.body.innerText : '')).catch(() => '');
      const had = new Set(textBefore.split('\n').map((l) => l.trim()));
      const newLines = textAfter.split('\n').map((l) => l.trim()).filter((l) => l && !had.has(l) && /amphitheatre/i.test(l) && l.length > '1600 Amphitheatre'.length);
      const n = Math.max(await visibleCount(SUGGESTIONS), newLines.length);
      const file = shotName(p.label, 'autocomplete');
      await capture(tab, path.join(shots, file), 50);
      out.push(n
        ? assertion({ ...base, id: `form-negative:${p.label}:autocomplete`, status: 'pass', severity: 'warn', message: `Typing an address shows ${n} suggestion(s).`, screenshots: [rel(file)] })
        : cfg.enabled
          ? assertion({ ...base, id: `form-negative:${p.label}:autocomplete`, status: 'warn', severity: 'warn', message: `Autocomplete is switched on in the campaign config, but typing an address showed no suggestions.${cfg.key ? '' : ' No Google Maps key is set in the config.'}`, screenshots: [rel(file)] })
          : assertion({ ...base, id: `form-negative:${p.label}:autocomplete`, status: 'pass', severity: 'info', message: 'No address suggestions, and autocomplete is not switched on in the campaign config, so there is nothing to check.', screenshots: [rel(file)] }));
      await addr.fill('').catch(() => {});
      await tab.keyboard.press('Escape').catch(() => {});
    }
  } catch (e) {
    out.push(assertion({ ...base, id: `form-negative:${p.label}:autocomplete`, status: 'skipped', severity: 'info', message: `Could not run: ${String(e.message || e).slice(0, 120)}` }));
  }

  // Invalid card number, then an invalid CVV. Writes are blocked, so even a
  // checkout that tried to pay could not send anything.
  const cardCase = async (kind, number, cvv) => {
    const id = `form-negative:${p.label}:${kind}`;
    try {
      await reload();
      await O.fillCheckout(tab, O.DEFAULT_CUSTOMER);
      await O.fillCard(tab, number, cvv);
      const before = await tab.evaluate(() => (document.body ? document.body.innerText : '')).catch(() => '');
      const orders = log.blockedOrders.length;
      await O.submit(tab);
      await tab.waitForTimeout(3500);
      const lines = await newErrorLines(tab, before);
      const st = await tab.evaluate(probes.formState);
      const file = shotName(p.label, kind);
      await capture(tab, path.join(shots, file), 55);
      errorTexts.push(...lines, ...st.sample);
      const what = kind === 'invalid-card' ? 'an invalid card number' : 'an invalid CVV';
      if (log.blockedOrders.length > orders) return assertion({ ...base, id, status: 'fail', severity: 'warn', message: `With ${what} the checkout went on to create the order (the call was blocked by this check) instead of showing an error.`, screenshots: [rel(file)] });
      if (lines.length || st.visibleErrors) return assertion({ ...base, id, status: 'pass', severity: 'warn', message: `${what[0].toUpperCase()}${what.slice(1)} shows an error${lines.length ? `: "${lines[0]}"` : ''}.`, evidence: lines.length ? lines : st.sample, screenshots: [rel(file)] });
      return assertion({ ...base, id, status: 'manual_review', severity: 'warn', message: `With ${what} no error text appeared on the page and no order was attempted. The error may show inside the card field. Look at the screenshot.`, screenshots: [rel(file)] });
    } catch (e) {
      return assertion({ ...base, id, status: 'skipped', severity: 'info', message: `Could not run: ${String(e.message || e).slice(0, 140)}` });
    }
  };
  if (p.sdk.present) {
    out.push(await cardCase('invalid-card', '4111111111111112', '123'));
    out.push(await cardCase('invalid-cvv', O.CARDS.standard.number, '1'));
  }

  // Error language: the errors should be in the page's language.
  const lang = String((await tab.evaluate(() => document.documentElement.lang).catch(() => '')) || p.lang || '').toLowerCase();
  const texts = [...new Set(errorTexts.filter(Boolean))];
  if (texts.length) {
    const english = texts.filter((t) => /\b(required|invalid|please|enter|field|valid|error)\b/i.test(t));
    out.push(!lang || lang.startsWith('en')
      ? assertion({ ...base, id: `form-negative:${p.label}:error-language`, status: english.length ? 'pass' : 'manual_review', severity: 'warn', message: english.length ? `Errors are in English, the page's language${lang ? ` (${lang})` : ''}.` : `The page is in ${lang || 'an unset language'}; read the error texts and confirm.`, evidence: texts.slice(0, 5) })
      : assertion({ ...base, id: `form-negative:${p.label}:error-language`, status: english.length ? 'fail' : 'pass', severity: 'warn', message: english.length ? `The page is in ${lang} but ${english.length} error message(s) are in English.` : `Errors are not in English on this ${lang} page.`, evidence: texts.slice(0, 5) }));
  }
  return out;
}

module.exports = { run, withParams, numbers, capture, TEST_PARAMS, TOGGLES };
