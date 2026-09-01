#!/usr/bin/env node

/**
 * assert-geometry — deterministic per-element geometry assertion.
 *
 * Compares the boxes a browser actually laid out against the boxes the
 * next-theme-figma handoff extracted from Figma metadata (`geometry.json`).
 * It reports per-element deltas in pixels: position within the section, size,
 * shared-edge alignment, and sibling gaps.
 *
 * This exists because mean per-section pixel mismatch is a bad acceptance
 * instrument. A wrong indent moves a text block 40px and costs about 0.3% of
 * the pixels in a section, so percentage scoring accepts a layout any human
 * eye rejects immediately. Run this gate FIRST; treat pixel mismatch as
 * telemetry afterwards.
 *
 * The script never launches a browser. `probe` emits a self-contained snippet
 * to evaluate in whatever browser capability the environment already has, and
 * `compare` reads the resulting box file. That keeps the skill free of a
 * browser-automation dependency and makes every check testable offline.
 *
 *   node scripts/assert-geometry.mjs probe \
 *     --manifest handoff/geometry.json --route product --viewport desktop \
 *     --out /tmp/probe.js
 *   # evaluate /tmp/probe.js in a page at the route + viewport, save its JSON
 *   node scripts/assert-geometry.mjs compare \
 *     --manifest handoff/geometry.json --route product --viewport desktop \
 *     --boxes /tmp/boxes.json --report /tmp/geometry-report.json
 */

import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const SCHEMA = 'next-theme-figma/geometry/v1';
const BOXES_SCHEMA = 'next-theme-dev/geometry-boxes/v1';
const REPORT_SCHEMA = 'next-theme-dev/geometry-report/v1';

// Measured defaults: 8px is roughly the smallest position error that has
// survived a full review round unnoticed, and a broken shared edge is visible
// well below that, so alignment is held tighter than position.
const DEFAULT_TOLERANCES = {
  desktop: { position: 8, size: 8, alignment: 4, gap: 4 },
  tablet: { position: 8, size: 8, alignment: 4, gap: 4 },
  mobile: { position: 6, size: 6, alignment: 4, gap: 4 },
};

const VIEWPORT_NAMES = ['desktop', 'tablet', 'mobile'];

// A Figma text layer's box is its text frame, which is not always the DOM
// block box: a hug-width layer measures the glyphs, a fill-width layer
// measures the column. Elements opt out of the checks their extraction cannot
// support, rather than the comparator guessing per role.
const DEFAULT_ASSERTIONS = ['position-x', 'position-y', 'width', 'height'];

function main(argv) {
  const [command, ...rest] = argv;
  if (!command || command === 'help' || command === '--help' || command === '-h') {
    printHelp();
    return command ? 0 : 2;
  }
  const opts = parseOptions(rest);
  if (command === 'probe') return runProbe(opts);
  if (command === 'selectors') return runSelectors(opts);
  if (command === 'compare') return runCompare(opts);
  process.stderr.write(`unknown command: ${command}\n`);
  printHelp();
  return 2;
}

function printHelp() {
  process.stdout.write(`assert-geometry — assert rendered DOM boxes against the Figma geometry manifest

Commands:
  probe      --manifest <geometry.json> --route <id> --viewport <name> [--out <file>]
             Emit a browser snippet that returns the measured boxes as JSON.
  selectors  --manifest <geometry.json> --route <id> --viewport <name>
             List the selectors the manifest maps, one per line.
  compare    --manifest <geometry.json> --route <id> --viewport <name> --boxes <file>
             [--report <file>] [--position-tolerance N] [--size-tolerance N]
             [--alignment-tolerance N] [--gap-tolerance N] [--scale-mode exact|fit]
             Report per-element deltas. Exits 1 on any failure.
`);
}

function parseOptions(argv) {
  const opts = {};
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (!arg.startsWith('--')) continue;
    const key = arg.slice(2);
    const next = argv[index + 1];
    if (next === undefined || next.startsWith('--')) {
      opts[key] = true;
    } else {
      opts[key] = next;
      index += 1;
    }
  }
  return opts;
}

function fail(message) {
  process.stderr.write(`assert-geometry: ${message}\n`);
  return 2;
}

function readJson(file) {
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}

function requireOpt(opts, key) {
  const value = opts[key];
  if (typeof value !== 'string' || !value) throw new Error(`--${key} is required`);
  return value;
}

/** Resolve one route+viewport frame out of the manifest. */
function loadFrame(opts) {
  const manifestPath = requireOpt(opts, 'manifest');
  const routeId = requireOpt(opts, 'route');
  const viewport = requireOpt(opts, 'viewport');
  if (!VIEWPORT_NAMES.includes(viewport)) {
    throw new Error(`--viewport must be one of ${VIEWPORT_NAMES.join(', ')}`);
  }
  const manifest = readJson(manifestPath);
  if (manifest.schema_version !== SCHEMA) {
    throw new Error(`${manifestPath}: schema_version must be "${SCHEMA}"`);
  }
  const route = (manifest.routes || []).find((entry) => entry.route_id === routeId);
  if (!route) throw new Error(`${manifestPath}: no route "${routeId}"`);
  const frame = (route.viewports || {})[viewport];
  if (!frame) throw new Error(`${manifestPath}: route "${routeId}" has no ${viewport} frame`);
  return { manifest, route, frame, routeId, viewport, manifestPath };
}

/** Every selector the manifest maps, as [{key, selector}]. */
function frameTargets(frame) {
  const targets = [];
  for (const section of frame.sections || []) {
    targets.push({ key: section.section_id, selector: section.selector });
    for (const element of section.elements || []) {
      targets.push({
        key: `${section.section_id}::${element.element_id}`,
        selector: element.selector,
      });
    }
  }
  return targets;
}

function runSelectors(opts) {
  const { frame } = loadFrame(opts);
  for (const target of frameTargets(frame)) {
    process.stdout.write(`${target.key}\t${target.selector}\n`);
  }
  return 0;
}

function runProbe(opts) {
  const { frame, routeId, viewport } = loadFrame(opts);
  const targets = frameTargets(frame);
  const script = probeScript({ routeId, viewport, targets });
  if (typeof opts.out === 'string') {
    fs.mkdirSync(path.dirname(path.resolve(opts.out)), { recursive: true });
    fs.writeFileSync(opts.out, script, 'utf8');
    process.stdout.write(`${opts.out}\n`);
  } else {
    process.stdout.write(script);
  }
  return 0;
}

/**
 * The emitted snippet is a plain expression: it evaluates to a JSON string, so
 * it works with `eval`-style browser bridges that stringify the last value and
 * with `JSON.parse(await page.evaluate(...))` alike. Boxes are document
 * relative (rect + scroll offset) so a mid-page scroll cannot shift them.
 */
function probeScript({ routeId, viewport, targets }) {
  const payload = JSON.stringify(targets, null, 2);
  return `(() => {
  const targets = ${payload};
  const boxes = {};
  for (const target of targets) {
    let nodes = [];
    try {
      nodes = Array.from(document.querySelectorAll(target.selector));
    } catch (error) {
      boxes[target.key] = { found: false, count: 0, error: String(error && error.message || error) };
      continue;
    }
    if (nodes.length !== 1) {
      boxes[target.key] = { found: false, count: nodes.length };
      continue;
    }
    const rect = nodes[0].getBoundingClientRect();
    boxes[target.key] = {
      found: true,
      count: 1,
      x: rect.left + window.scrollX,
      y: rect.top + window.scrollY,
      width: rect.width,
      height: rect.height,
    };
  }
  return JSON.stringify({
    schema_version: ${JSON.stringify(BOXES_SCHEMA)},
    route_id: ${JSON.stringify(routeId)},
    viewport: ${JSON.stringify(viewport)},
    viewport_width: window.innerWidth,
    device_pixel_ratio: window.devicePixelRatio,
    url: location.href,
    boxes,
  });
})()
`;
}

function tolerancesFor(viewport, opts) {
  const base = { ...DEFAULT_TOLERANCES[viewport] };
  const overrides = {
    position: opts['position-tolerance'],
    size: opts['size-tolerance'],
    alignment: opts['alignment-tolerance'],
    gap: opts['gap-tolerance'],
  };
  for (const [key, value] of Object.entries(overrides)) {
    if (value === undefined) continue;
    const parsed = Number(value);
    if (!Number.isFinite(parsed) || parsed < 0) {
      throw new Error(`--${key}-tolerance must be a non-negative number`);
    }
    base[key] = parsed;
  }
  return base;
}

function runCompare(opts) {
  const { frame, routeId, viewport } = loadFrame(opts);
  const boxesPath = requireOpt(opts, 'boxes');
  const measured = readJson(boxesPath);
  if (measured.schema_version !== BOXES_SCHEMA) {
    throw new Error(`${boxesPath}: schema_version must be "${BOXES_SCHEMA}"`);
  }
  if (measured.route_id !== routeId || measured.viewport !== viewport) {
    throw new Error(
      `${boxesPath}: measured ${measured.route_id}/${measured.viewport}, `
      + `asked for ${routeId}/${viewport}`,
    );
  }
  const tolerances = tolerancesFor(viewport, opts);
  const scaleMode = opts['scale-mode'] || 'exact';
  if (!['exact', 'fit'].includes(scaleMode)) {
    throw new Error('--scale-mode must be exact or fit');
  }

  const frameWidth = frame.frame_width;
  const measuredWidth = measured.viewport_width;
  let scale = 1;
  if (typeof measuredWidth !== 'number' || !Number.isFinite(measuredWidth)) {
    throw new Error(`${boxesPath}: viewport_width must be a number`);
  }
  if (measuredWidth !== frameWidth) {
    if (scaleMode === 'exact') {
      throw new Error(
        `viewport width mismatch: measured ${measuredWidth}px, frame is ${frameWidth}px. `
        + 'Re-measure at the frame width, or pass --scale-mode fit to scale the manifest.',
      );
    }
    scale = measuredWidth / frameWidth;
  }

  const report = compareFrame({ frame, measured, tolerances, scale, routeId, viewport, frameWidth });
  if (typeof opts.report === 'string') {
    fs.mkdirSync(path.dirname(path.resolve(opts.report)), { recursive: true });
    fs.writeFileSync(opts.report, `${JSON.stringify(report, null, 2)}\n`, 'utf8');
  }
  printReport(report);
  return report.status === 'pass' ? 0 : 1;
}

export function compareFrame({ frame, measured, tolerances, scale, routeId, viewport, frameWidth }) {
  const boxes = measured.boxes || {};
  const checks = [];

  const box = (key) => {
    const entry = boxes[key];
    if (!entry || entry.found !== true) return null;
    return entry;
  };

  for (const section of frame.sections || []) {
    const sectionKey = section.section_id;
    const sectionBox = box(sectionKey);
    if (!sectionBox) {
      checks.push({
        check: 'presence',
        section_id: section.section_id,
        element_id: null,
        selector: section.selector,
        status: 'missing',
        detail: describeMissing(boxes[sectionKey]),
      });
      continue;
    }

    // Element boxes are section-relative in the manifest and document-relative
    // in the measurement, so subtracting the section origin is what makes the
    // comparison immune to unrelated vertical drift higher up the page.
    const measuredElements = new Map();
    for (const element of section.elements || []) {
      const key = `${section.section_id}::${element.element_id}`;
      const elementBox = box(key);
      if (!elementBox) {
        checks.push({
          check: 'presence',
          section_id: section.section_id,
          element_id: element.element_id,
          selector: element.selector,
          status: 'missing',
          detail: describeMissing(boxes[key]),
        });
        continue;
      }
      const relative = {
        x: elementBox.x - sectionBox.x,
        y: elementBox.y - sectionBox.y,
        width: elementBox.width,
        height: elementBox.height,
      };
      measuredElements.set(element.element_id, relative);

      const expected = scaleBox(element.box, scale);
      const tolerance = elementTolerance(element, tolerances);
      const asserted = new Set(element.assert || DEFAULT_ASSERTIONS);
      const anchor = element.align_anchor || 'left';
      const common = {
        section_id: section.section_id,
        element_id: element.element_id,
        selector: element.selector,
      };
      if (asserted.has('position-x')) {
        // A centered text block is a different width in Figma than in the
        // DOM, so its left edge is not comparable while its centre is. The
        // anchor says which point the design actually fixes.
        pushDelta(checks, {
          ...common,
          check: 'position',
          axis: anchor === 'left' ? 'x' : `x-${anchor}`,
          expected: anchorX(expected, anchor),
          actual: anchorX(relative, anchor),
          tolerance: tolerance.position,
        });
      }
      if (asserted.has('position-y')) {
        pushDelta(checks, {
          ...common,
          check: 'position',
          axis: 'y',
          expected: expected.y,
          actual: relative.y,
          tolerance: tolerance.position,
        });
      }
      if (asserted.has('width')) {
        pushDelta(checks, {
          ...common,
          check: 'size',
          axis: 'width',
          expected: expected.width,
          actual: relative.width,
          tolerance: tolerance.size,
        });
      }
      if (asserted.has('height')) {
        pushDelta(checks, {
          ...common,
          check: 'size',
          axis: 'height',
          expected: expected.height,
          actual: relative.height,
          tolerance: tolerance.size,
        });
      }
    }

    for (const group of section.alignment_groups || []) {
      const values = [];
      let incomplete = false;
      for (const id of group.element_ids || []) {
        const relative = measuredElements.get(id);
        if (!relative) {
          incomplete = true;
          break;
        }
        values.push({ id, value: edgeValue(relative, group.edge) });
      }
      if (incomplete) continue;
      const numbers = values.map((entry) => entry.value);
      const spread = Math.max(...numbers) - Math.min(...numbers);
      const worst = values.reduce(
        (acc, entry) => (Math.abs(entry.value - median(numbers)) > Math.abs(acc.value - median(numbers)) ? entry : acc),
        values[0],
      );
      checks.push({
        check: 'alignment',
        section_id: section.section_id,
        element_id: worst.id,
        group_id: group.group_id,
        edge: group.edge,
        expected: 0,
        actual: round(spread),
        delta: round(spread),
        tolerance: tolerances.alignment,
        status: spread > tolerances.alignment ? 'fail' : 'pass',
        detail: values.map((entry) => `${entry.id}=${round(entry.value)}`).join(' '),
      });
    }

    for (const gap of section.gaps || []) {
      const from = measuredElements.get(gap.from);
      const to = measuredElements.get(gap.to);
      if (!from || !to) continue;
      const actual = gap.axis === 'vertical'
        ? to.y - (from.y + from.height)
        : to.x - (from.x + from.width);
      pushDelta(checks, {
        check: 'gap',
        section_id: section.section_id,
        element_id: gap.gap_id,
        axis: gap.axis,
        expected: gap.value * scale,
        actual,
        tolerance: tolerances.gap,
      });
    }
  }

  const failures = checks.filter((check) => check.status === 'fail');
  const missing = checks.filter((check) => check.status === 'missing');
  return {
    schema_version: REPORT_SCHEMA,
    route_id: routeId,
    viewport,
    frame_width: frameWidth,
    measured_viewport_width: measured.viewport_width,
    url: measured.url || null,
    scale: round(scale),
    tolerances,
    checks,
    summary: {
      checks: checks.length,
      failures: failures.length,
      missing: missing.length,
    },
    status: failures.length || missing.length ? 'fail' : 'pass',
  };
}

function describeMissing(entry) {
  if (!entry) return 'selector not measured';
  if (entry.error) return `selector error: ${entry.error}`;
  if (entry.count === 0) return 'selector matched no element';
  return `selector matched ${entry.count} elements; it must match exactly one`;
}

function elementTolerance(element, tolerances) {
  if (typeof element.tolerance_px !== 'number') return tolerances;
  return { ...tolerances, position: element.tolerance_px, size: element.tolerance_px };
}

function pushDelta(checks, entry) {
  const delta = entry.actual - entry.expected;
  checks.push({
    ...entry,
    expected: round(entry.expected),
    actual: round(entry.actual),
    delta: round(delta),
    status: Math.abs(delta) > entry.tolerance ? 'fail' : 'pass',
  });
}

function scaleBox(box, scale) {
  return {
    x: box.x * scale,
    y: box.y * scale,
    width: box.width * scale,
    height: box.height * scale,
  };
}

function anchorX(box, anchor) {
  if (anchor === 'center') return box.x + box.width / 2;
  if (anchor === 'right') return box.x + box.width;
  return box.x;
}

function edgeValue(box, edge) {
  if (edge === 'left') return box.x;
  if (edge === 'right') return box.x + box.width;
  if (edge === 'top') return box.y;
  return box.y + box.height;
}

function median(values) {
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

function round(value) {
  return Math.round(value * 100) / 100;
}

function printReport(report) {
  const problems = report.checks.filter((check) => check.status !== 'pass');
  problems.sort((a, b) => Math.abs(b.delta || 0) - Math.abs(a.delta || 0));
  for (const problem of problems) {
    const target = problem.element_id
      ? `${problem.section_id}.${problem.element_id}`
      : problem.section_id;
    if (problem.status === 'missing') {
      process.stdout.write(`MISSING  ${target}  ${problem.selector}  (${problem.detail})\n`);
      continue;
    }
    const axis = problem.axis || problem.edge || '';
    process.stdout.write(
      `FAIL     ${target}  ${problem.check}${axis ? `.${axis}` : ''}  `
      + `expected ${problem.expected}px, measured ${problem.actual}px, `
      + `delta ${problem.delta >= 0 ? '+' : ''}${problem.delta}px `
      + `(tolerance ${problem.tolerance}px)`
      + `${problem.detail ? `  [${problem.detail}]` : ''}\n`,
    );
  }
  process.stdout.write(
    `[assert-geometry] ${report.status.toUpperCase()} ${report.route_id}/${report.viewport}: `
    + `${report.summary.checks} checks, ${report.summary.failures} failed, `
    + `${report.summary.missing} missing\n`,
  );
}

if (import.meta.url === `file://${process.argv[1]}`) {
  try {
    process.exit(main(process.argv.slice(2)));
  } catch (error) {
    process.exit(fail(error.message));
  }
}

export { main, probeScript, frameTargets, DEFAULT_TOLERANCES, DEFAULT_ASSERTIONS };
