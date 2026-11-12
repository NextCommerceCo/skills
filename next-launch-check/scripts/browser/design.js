'use strict';
// Built page against the design: typography and colours read from the
// rendered page and compared with CSS copied from Figma, plus screenshots
// at the frame width next to any exported frame image. Every finding is for
// a person to judge, so nothing here can block a launch on its own.
const fs = require('fs');
const path = require('path');
const { assertion } = require('../lib/util');
const F = require('../lib/figma');
const PW = require('./playwright');

function styles() {
  const out = [];
  const seen = new Set();
  for (const el of document.querySelectorAll('h1,h2,h3,h4,h5,h6,p,a,button,li,label,span,strong,small,div')) {
    const own = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim().length > 1);
    if (!own) continue;
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    const s = getComputedStyle(el);
    const key = [s.fontFamily, s.fontSize, s.fontWeight, s.color].join('|');
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({ tag: el.tagName.toLowerCase(), text: el.textContent.trim().slice(0, 50), family: s.fontFamily, size: parseFloat(s.fontSize), weight: parseInt(s.fontWeight, 10), lineHeight: s.lineHeight, color: s.color, bg: s.backgroundColor });
    if (out.length > 400) break;
  }
  return out;
}

async function run(ctx) {
  const loaded = PW.load();
  if (!loaded) return [assertion({ id: 'design-parity:unavailable', family: 'design-parity', status: 'skipped', severity: 'info', message: 'Playwright is not installed; design mode needs the browser.' })];
  const out = [];
  const shots = path.join(ctx.outDir, 'screenshots');
  fs.mkdirSync(shots, { recursive: true });
  const blocks = ctx.options.figmaCss ? F.parse(fs.readFileSync(ctx.options.figmaCss, 'utf8')) : [];
  const frames = (ctx.options.frames || []).map((f) => ({ file: f, size: F.pngSize(fs.readFileSync(f)) }));
  const widths = [...new Set([...(frames.map((f) => f.size && f.size.width).filter(Boolean)), ctx.options.width || 1440])];
  const browser = await loaded.pw.chromium.launch();
  let found = [];
  try {
    for (const w of widths) {
      const log = PW.newLog();
      const context = await browser.newContext({ viewport: { width: w, height: 900 } });
      await PW.guard(context, { liveBeacons: false, log });
      const tab = await context.newPage();
      await tab.goto(ctx.entryUrl, { waitUntil: 'load', timeout: 45000 });
      await tab.waitForTimeout(1500);
      const file = `design-page-${w}.jpg`;
      await tab.screenshot({ path: path.join(shots, file), fullPage: true, type: 'jpeg', quality: 70 });
      if (!found.length) found = await tab.evaluate(styles);
      const frame = frames.find((f) => f.size && f.size.width === w);
      const shotList = [path.join('screenshots', file)];
      if (frame) { const copy = `design-frame-${w}.png`; fs.copyFileSync(frame.file, path.join(shots, copy)); shotList.unshift(path.join('screenshots', copy)); }
      out.push(assertion({ id: `design-parity:screens-${w}`, family: 'design-parity', status: 'manual_review', severity: 'info', message: frame ? `Design frame and built page at ${w}px, side by side in the screenshots. Compare layout, spacing and imagery.` : `Built page at ${w}px. No exported frame of this width was given; compare with the design by eye.`, screenshots: shotList }));
      await context.close();
    }
  } finally {
    await browser.close();
  }
  if (!blocks.length) {
    out.push(assertion({ id: 'design-parity:css', family: 'design-parity', status: 'skipped', severity: 'info', message: 'No Figma CSS given (--figma-css), so typography and colours were not compared.' }));
    return out;
  }
  const missing = [];
  let matched = 0;
  for (const b of blocks) {
    const size = F.px(b.props['font-size']);
    if (!size) continue;
    const fam = F.family(b.props['font-family']);
    const wt = F.weight(b.props['font-weight']);
    const hit = found.find((s) => Math.abs(s.size - size) <= 1 && (!fam || F.family(s.family) === fam) && (!wt || s.weight === wt));
    if (hit) { matched++; continue; }
    const near = found.filter((s) => Math.abs(s.size - size) <= 2).slice(0, 3).map((s) => `${s.tag} "${s.text}" ${F.family(s.family)} ${s.size}px/${s.weight}`);
    missing.push({ layer: b.name, want: `${fam || 'any font'} ${size}px${wt ? `/${wt}` : ''}`, nearest: near });
  }
  out.push(missing.length
    ? assertion({ id: 'design-parity:typography', family: 'design-parity', status: 'manual_review', severity: 'info', message: `${matched} text style(s) from the design are on the page; ${missing.length} are not found exactly.`, evidence: missing.slice(0, 25) })
    : assertion({ id: 'design-parity:typography', family: 'design-parity', status: 'pass', severity: 'info', message: `All ${matched} text style(s) from the design were found on the page.` }));
  const want = F.colors(blocks);
  const have = new Set(found.flatMap((s) => [F.hex(s.color), F.hex(s.bg)]).filter(Boolean));
  const absent = want.filter((c) => !have.has(c));
  if (want.length) out.push(absent.length
    ? assertion({ id: 'design-parity:colours', family: 'design-parity', status: 'manual_review', severity: 'info', message: `${want.length - absent.length}/${want.length} design colours used on text or backgrounds; not found: ${absent.join(', ')}. Some may be on borders, images or icons.` })
    : assertion({ id: 'design-parity:colours', family: 'design-parity', status: 'pass', severity: 'info', message: `All ${want.length} design colours found on the page.` }));
  return out;
}

module.exports = { run };
