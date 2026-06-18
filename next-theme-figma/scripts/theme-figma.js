#!/usr/bin/env node

const fs = require('fs');
const path = require('path');

const SCHEMA = {
  handoff: 'next-theme-figma/handoff/v0',
  routes: 'next-theme-figma/routes/v0',
  sections: 'next-theme-figma/sections/v0',
  assets: 'next-theme-figma/assets/v0',
  divergence: 'next-theme-figma/spark-divergence/v0',
  coverage: 'next-theme-figma/viewport-coverage/v0',
};

const CLASSIFICATIONS = new Set([
  'semantic-rebuild',
  'composed-asset',
  'background-asset',
  'live-spark-component',
  'platform-app-hook',
  'screenshot-fallback',
]);

const ASSET_PREFIXES = new Set(['img', 'bg', 'img-group']);
const ASSET_FORMATS = new Set(['png', 'jpg', 'jpeg', 'svg', 'webp']);
const OPTIMIZATION_STATUSES = new Set(['not-started', 'source-selected', 'optimized', 'blocked']);
const DIVERGENCE_DECISIONS = new Set(['spark-wins', 'figma-wins-with-guardrails', 'needs-approval', 'blocked']);
const DIVERGENCE_STATUSES = new Set(['open', 'approved', 'implemented', 'blocked', 'accepted-gap']);
const MODES = new Set(['design-audit', 'handoff-prep', 'implementation-handoff']);
const VIEWPORT_WIDTHS = {
  desktop: new Set([1440]),
  tablet: new Set([768]),
  mobile: new Set([375, 390]),
};

main();

function main() {
  const [command, ...argv] = process.argv.slice(2);
  try {
    if (!command || command === 'help' || command === '--help' || command === '-h') {
      printHelp();
      process.exit(command ? 0 : 2);
    }

    if (command === 'parse-url') {
      const input = argv.find((arg) => !arg.startsWith('--'));
      if (!input) throw new Error('parse-url requires a Figma URL or node id');
      printJson(parseFigmaInput(input));
      return;
    }

    if (command === 'infer-section') {
      const input = argv.join(' ').trim();
      if (!input) throw new Error('infer-section requires a Figma frame name');
      printJson(inferSection(input));
      return;
    }

    if (command === 'new-package') {
      const opts = parseOptions(argv);
      createPackage(opts);
      return;
    }

    if (command === 'validate-package') {
      const target = argv.find((arg) => !arg.startsWith('--'));
      if (!target) throw new Error('validate-package requires a package directory');
      validatePackage(path.resolve(target));
      return;
    }

    throw new Error(`Unknown command "${command}"`);
  } catch (error) {
    console.error(`Error: ${error.message}`);
    process.exit(1);
  }
}

function printHelp() {
  console.log(`theme-figma helper

Usage:
  node scripts/theme-figma.js parse-url "<figma-url-or-node-id>"
  node scripts/theme-figma.js infer-section "hero1-desktop"
  node scripts/theme-figma.js new-package --out <dir> --project <slug> [options]
  node scripts/theme-figma.js validate-package <dir>

new-package options:
  --figma-url URL
  --store STORE
  --repo PATH
  --preview-url URL
  --theme-id ID
  --mode design-audit|handoff-prep|implementation-handoff
  --routes "/,/products/example"
`);
}

function parseOptions(argv) {
  const opts = {};
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (!arg.startsWith('--')) continue;
    const eq = arg.indexOf('=');
    if (eq !== -1) {
      opts[arg.slice(2, eq)] = arg.slice(eq + 1);
      continue;
    }
    const key = arg.slice(2);
    const next = argv[i + 1];
    if (!next || next.startsWith('--')) {
      opts[key] = true;
      continue;
    }
    opts[key] = next;
    i += 1;
  }
  return opts;
}

function parseFigmaInput(value) {
  const raw = String(value || '').trim();
  const fileKeyMatch = raw.match(/figma\.com\/(?:design|file)\/([^/?#]+)/i);
  const nodeMatch = raw.match(/[?&]node-id=([^&#]+)/i);
  const nodeId = nodeMatch ? decodeURIComponent(nodeMatch[1]).replace(/-/g, ':') : normalizeNodeId(raw);

  return {
    input: raw,
    file_key: fileKeyMatch ? fileKeyMatch[1] : '',
    node_id: nodeId,
    node_id_url: nodeId ? nodeId.replace(/:/g, '-') : '',
    is_figma_url: /figma\.com\/(?:design|file)\//i.test(raw),
  };
}

function normalizeNodeId(value) {
  const raw = String(value || '').trim();
  if (/^\d+[:-]\d+$/.test(raw)) return raw.replace(/-/g, ':');
  return '';
}

function inferSection(frameName) {
  const raw = String(frameName || '').trim();
  const cleaned = raw
    .replace(/\s+/g, '-')
    .replace(/_+/g, '-')
    .replace(/[^A-Za-z0-9-]+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '');

  const bpMatch = cleaned.match(/(?:^|-)(desktop|tablet|mobile)$/i);
  const breakpoint = bpMatch ? bpMatch[1].toLowerCase() : '';
  const base = breakpoint
    ? cleaned.replace(new RegExp(`-?${breakpoint}$`, 'i'), '')
    : cleaned;

  const lower = base.toLowerCase();
  let category = '';
  let number = '';

  const compact = lower.match(/^([a-z][a-z-]*?)(\d+)$/);
  const separated = lower.match(/^([a-z][a-z-]*)-(\d+)$/);
  if (compact) {
    category = compact[1];
    number = compact[2];
  } else if (separated) {
    category = separated[1];
    number = separated[2];
  }

  if (category === 'sticky') category = 'bottomcta';

  const sectionName = category && number ? `${category}-${number}` : lower;
  return {
    frame_name: raw,
    normalized_base: lower,
    section_id: sectionName,
    category,
    number,
    breakpoint,
    valid_contract_name: Boolean(category && number && breakpoint),
    expected_pattern: '{category}{number}-{breakpoint}',
  };
}

function createPackage(opts) {
  const out = path.resolve(requireOpt(opts, 'out'));
  const project = requireOpt(opts, 'project');
  const mode = opts.mode || 'handoff-prep';
  if (!MODES.has(mode)) {
    throw new Error(`--mode must be one of ${Array.from(MODES).join(', ')}`);
  }

  const figma = opts['figma-url'] ? parseFigmaInput(opts['figma-url']) : {};
  const generatedAt = new Date().toISOString();
  const routes = parseRoutes(opts.routes);

  fs.mkdirSync(out, { recursive: true });

  writeJson(path.join(out, 'figma-handoff.json'), {
    schema_version: SCHEMA.handoff,
    generated_at: generatedAt,
    generator: 'next-theme-figma',
    project,
    mode,
    figma: {
      url: opts['figma-url'] || '',
      file_key: figma.file_key || '',
      entry_node_id: figma.node_id || '',
    },
    target: {
      store: opts.store || '',
      repo: opts.repo || '',
      preview_url: opts['preview-url'] || '',
      theme_id: opts['theme-id'] || '',
      theme_family: '',
    },
    manifests: {
      routes: 'routes.json',
      sections: 'sections.json',
      assets: 'assets.json',
      spark_divergence_ledger: 'spark-divergence-ledger.json',
      viewport_coverage: 'viewport-coverage.json',
    },
    unresolved_questions: [],
  });

  writeJson(path.join(out, 'routes.json'), {
    schema_version: SCHEMA.routes,
    routes: routes.map((route, index) => ({
      route_id: routeId(route, index),
      storefront_path: route,
      theme_template: '',
      figma_frames: emptyFrames(),
      section_order: [],
      reference_screenshots: emptyScreenshotMap(),
      existing_preview_screenshots: emptyScreenshotMap(),
      status: 'draft',
      notes: '',
    })),
  });

  writeJson(path.join(out, 'sections.json'), {
    schema_version: SCHEMA.sections,
    sections: [
      {
        section_id: 'example-1',
        route_id: routes.length ? routeId(routes[0], 0) : '',
        order: 1,
        figma_names: emptyNameMap(),
        figma_nodes: emptyNodeMap(),
        classification: 'semantic-rebuild',
        classification_rationale: 'Replace this example with the real section decision.',
        implementation_target: {
          template: '',
          partials: [],
          assets: [],
          settings: [],
        },
        commerce_surface: '',
        asset_ids: [],
        divergence_ids: [],
        responsive_notes: '',
        behavior_notes: '',
        unresolved_gaps: [],
        screenshot_fallback_approved: false,
      },
    ],
  });

  writeJson(path.join(out, 'assets.json'), {
    schema_version: SCHEMA.assets,
    assets: [
      {
        asset_id: 'example-asset',
        section_id: 'example-1',
        source_node_id: '',
        source_layer_name: '',
        prefix: 'img',
        target_path: '',
        asset_url_path: '',
        format: 'png',
        expected_dimensions: { width: 0, height: 0 },
        requires_alpha: false,
        canvas_rendered: true,
        optimization_status: 'not-started',
        replace_with_backend_product_media: false,
        clean_export_verified: false,
        notes: 'Replace this example or delete it.',
      },
    ],
  });

  writeJson(path.join(out, 'spark-divergence-ledger.json'), {
    schema_version: SCHEMA.divergence,
    entries: [
      {
        divergence_id: 'example-divergence',
        surface: '',
        pages: [],
        figma_expectation: '',
        spark_platform_behavior: '',
        decision: 'spark-wins',
        implementation_guardrail: '',
        status: 'open',
        approved_by: '',
        notes: 'Replace this example or delete it.',
      },
    ],
  });

  writeJson(path.join(out, 'viewport-coverage.json'), {
    schema_version: SCHEMA.coverage,
    viewports: {
      desktop: { expected_width: 1440, available: false },
      tablet: { expected_width: 768, available: false },
      mobile: { expected_width: 375, available: false },
    },
    coverage: routes.map((route, index) => ({
      route_id: routeId(route, index),
      desktop: { figma_ref: '', preview_ref: '', status: 'missing' },
      tablet: { figma_ref: '', preview_ref: '', status: 'missing' },
      mobile: { figma_ref: '', preview_ref: '', status: 'missing' },
      notes: '',
    })),
  });

  writeText(path.join(out, 'validation-checklist.md'), checklistTemplate(project));
  writeText(path.join(out, 'notes.md'), notesTemplate(project));

  console.log(`[next-theme-figma] package created: ${out}`);
}

function validatePackage(dir) {
  const errors = [];
  const warnings = [];
  const required = [
    'figma-handoff.json',
    'routes.json',
    'sections.json',
    'assets.json',
    'spark-divergence-ledger.json',
    'viewport-coverage.json',
    'validation-checklist.md',
  ];

  for (const file of required) {
    if (!fs.existsSync(path.join(dir, file))) errors.push(`missing ${file}`);
  }

  const handoff = readJson(path.join(dir, 'figma-handoff.json'), errors);
  const routes = readJson(path.join(dir, 'routes.json'), errors);
  const sections = readJson(path.join(dir, 'sections.json'), errors);
  const assets = readJson(path.join(dir, 'assets.json'), errors);
  const divergence = readJson(path.join(dir, 'spark-divergence-ledger.json'), errors);
  const coverage = readJson(path.join(dir, 'viewport-coverage.json'), errors);

  expectSchema(handoff, SCHEMA.handoff, 'figma-handoff.json', errors);
  expectSchema(routes, SCHEMA.routes, 'routes.json', errors);
  expectSchema(sections, SCHEMA.sections, 'sections.json', errors);
  expectSchema(assets, SCHEMA.assets, 'assets.json', errors);
  expectSchema(divergence, SCHEMA.divergence, 'spark-divergence-ledger.json', errors);
  expectSchema(coverage, SCHEMA.coverage, 'viewport-coverage.json', errors);

  if (handoff && !handoff.figma?.url && !handoff.figma?.file_key) {
    errors.push('figma-handoff.json: no Figma URL or file key recorded');
  }

  if (routes && Array.isArray(routes.routes)) {
    if (!routes.routes.length) warnings.push('routes.json: no routes recorded');
    for (const route of routes.routes) {
      if (!route.route_id) errors.push('routes.json: route missing route_id');
      if (!route.storefront_path) errors.push(`${route.route_id || 'route'}: missing storefront_path`);
      if (!route.section_order || !route.section_order.length) {
        warnings.push(`${route.route_id || 'route'}: section_order is empty`);
      }
    }
  }

  if (sections && Array.isArray(sections.sections)) {
    if (!sections.sections.length) warnings.push('sections.json: no sections recorded');
    for (const section of sections.sections) {
      const id = section.section_id || 'section';
      if (!section.section_id) errors.push('sections.json: section missing section_id');
      if (!CLASSIFICATIONS.has(section.classification)) {
        errors.push(`${id}: invalid classification "${section.classification}"`);
      }
      if (section.classification === 'screenshot-fallback' && !section.screenshot_fallback_approved) {
        errors.push(`${id}: screenshot-fallback requires screenshot_fallback_approved=true`);
      }
      if (!section.classification_rationale) warnings.push(`${id}: missing classification_rationale`);
      if (!section.figma_nodes || !Object.values(section.figma_nodes).some(Boolean)) {
        warnings.push(`${id}: no Figma section node IDs recorded`);
      }
    }
  }

  if (assets && Array.isArray(assets.assets)) {
    for (const asset of assets.assets) {
      const id = asset.asset_id || 'asset';
      if (!ASSET_PREFIXES.has(asset.prefix)) errors.push(`${id}: invalid prefix "${asset.prefix}"`);
      if (!asset.source_node_id) warnings.push(`${id}: missing source_node_id`);
      if (!asset.target_path) warnings.push(`${id}: missing target_path`);
      if (!asset.format) warnings.push(`${id}: missing format`);
      if (asset.format && !ASSET_FORMATS.has(asset.format)) {
        errors.push(`${id}: invalid format "${asset.format}"`);
      }
      if (typeof asset.requires_alpha !== 'boolean') {
        warnings.push(`${id}: requires_alpha should be true or false`);
      }
      if (typeof asset.canvas_rendered !== 'boolean') {
        warnings.push(`${id}: canvas_rendered should be true or false`);
      }
      if (asset.optimization_status && !OPTIMIZATION_STATUSES.has(asset.optimization_status)) {
        errors.push(`${id}: invalid optimization_status "${asset.optimization_status}"`);
      }
      if (typeof asset.replace_with_backend_product_media !== 'boolean') {
        warnings.push(`${id}: replace_with_backend_product_media should be true or false`);
      }
      if (asset.prefix === 'img-group' && asset.clean_export_verified !== true) {
        errors.push(`${id}: img-group requires clean_export_verified=true after source review`);
      }
    }
  }

  if (divergence && Array.isArray(divergence.entries)) {
    for (const entry of divergence.entries) {
      const id = entry.divergence_id || entry.surface || 'divergence';
      if (!entry.surface) warnings.push(`${id}: missing surface`);
      if (!entry.spark_platform_behavior) warnings.push(`${id}: missing spark_platform_behavior`);
      if (!entry.implementation_guardrail) warnings.push(`${id}: missing implementation_guardrail`);
      if (entry.decision && !DIVERGENCE_DECISIONS.has(entry.decision)) {
        errors.push(`${id}: invalid decision "${entry.decision}"`);
      }
      if (entry.status && !DIVERGENCE_STATUSES.has(entry.status)) {
        errors.push(`${id}: invalid status "${entry.status}"`);
      }
    }
  }

  if (coverage && coverage.viewports) {
    for (const name of ['desktop', 'tablet', 'mobile']) {
      const vp = coverage.viewports[name];
      if (!vp || typeof vp.available !== 'boolean') {
        warnings.push(`viewport-coverage.json: ${name} availability not recorded`);
        continue;
      }
      if (vp.expected_width && !VIEWPORT_WIDTHS[name].has(Number(vp.expected_width))) {
        errors.push(`viewport-coverage.json: ${name} expected_width must be one of ${Array.from(VIEWPORT_WIDTHS[name]).join(', ')}`);
      }
    }
  }

  for (const warning of warnings) console.log(`Warning: ${warning}`);
  if (errors.length) {
    for (const error of errors) console.log(`Error: ${error}`);
    process.exit(1);
  }
  console.log(`[next-theme-figma] PASS with ${warnings.length} warning(s)`);
}

function parseRoutes(value) {
  if (!value) return ['/'];
  return String(value)
    .split(',')
    .map((route) => route.trim())
    .filter(Boolean);
}

function routeId(route, index) {
  if (route === '/') return 'home';
  const cleaned = route
    .replace(/^\/+|\/+$/g, '')
    .replace(/[^a-z0-9]+/gi, '-')
    .replace(/^-|-$/g, '')
    .toLowerCase();
  return cleaned || `route-${index + 1}`;
}

function emptyFrames() {
  return {
    desktop: { name: '', node_id: '', url: '' },
    tablet: { name: '', node_id: '', url: '' },
    mobile: { name: '', node_id: '', url: '' },
  };
}

function emptyScreenshotMap() {
  return { desktop: '', tablet: '', mobile: '' };
}

function emptyNameMap() {
  return { desktop: '', tablet: '', mobile: '' };
}

function emptyNodeMap() {
  return { desktop: '', tablet: '', mobile: '' };
}

function checklistTemplate(project) {
  return `# ${project} Figma Handoff Checklist

- [ ] Intake fields are complete.
- [ ] Target routes and theme templates are identified.
- [ ] Figma refs are captured for every available viewport.
- [ ] Missing desktop/tablet/mobile refs are documented.
- [ ] Sections are ordered and classified.
- [ ] Screenshot fallbacks have explicit approval or are removed.
- [ ] Assets use source node IDs and img/bg/img-group prefixes.
- [ ] Product media replacement decisions are recorded.
- [ ] Spark divergence ledger covers PDP/cart/header/app surfaces.
- [ ] Visual mismatches are marked fix-now, spark-divergence, designer-input-needed, or accepted-gap.
- [ ] Package validates with theme-figma.js validate-package.
- [ ] Handoff notes tell next-theme-dev where to start.
`;
}

function notesTemplate(project) {
  return `# ${project} Handoff Notes

## Summary

## Implementation Priority

1.

## Unresolved Design Gaps

- 

## Visual Verification Notes

- 

## Handoff To next-theme-dev

- 
`;
}

function requireOpt(opts, key) {
  if (!opts[key]) throw new Error(`Missing required --${key}`);
  return opts[key];
}

function writeJson(file, value) {
  fs.writeFileSync(file, JSON.stringify(value, null, 2) + '\n');
}

function writeText(file, value) {
  fs.writeFileSync(file, value);
}

function readJson(file, errors) {
  if (!fs.existsSync(file)) return null;
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch (error) {
    errors.push(`${path.basename(file)}: invalid JSON (${error.message})`);
    return null;
  }
}

function expectSchema(value, schema, label, errors) {
  if (!value) return;
  if (value.schema_version !== schema) {
    errors.push(`${label}: schema_version must be "${schema}"`);
  }
}

function printJson(value) {
  console.log(JSON.stringify(value, null, 2));
}
