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
      const opts = parseOptions(argv);
      const target = argv.find((arg) => !arg.startsWith('--'));
      if (!target) throw new Error('validate-package requires a package directory');
      validatePackage(path.resolve(target), opts['non-strict'] !== true);
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
  node scripts/theme-figma.js validate-package <dir> [--non-strict]

new-package options:
  --figma-url URL
  --store STORE
  --repo PATH
  --preview-url URL
  --theme-id ID
  --mode design-audit|handoff-prep|implementation-handoff
  --routes "/,/products/example"
  --fixture FILE
  --force
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

  const fixture = opts.fixture ? readFixture(path.resolve(String(opts.fixture))) : null;
  const figmaUrl = opts['figma-url'] || fixture?.handoff?.figma?.url || '';
  const figma = figmaUrl ? parseFigmaInput(figmaUrl) : {};
  const generatedAt = new Date().toISOString();
  const routes = parseRoutes(opts.routes);

  fs.mkdirSync(out, { recursive: true });

  const outputFiles = [
    'figma-handoff.json',
    'routes.json',
    'sections.json',
    'assets.json',
    'spark-divergence-ledger.json',
    'viewport-coverage.json',
    'validation-checklist.md',
    'notes.md',
  ];
  const existing = outputFiles.filter((file) => fs.existsSync(path.join(out, file)));
  if (existing.length && opts.force !== true) {
    throw new Error(`refusing to overwrite existing package files (${existing.join(', ')}); pass --force to replace them`);
  }

  const handoff = fixture?.handoff || {
    schema_version: SCHEMA.handoff,
    generated_at: generatedAt,
    generator: 'next-theme-figma',
    project,
    mode,
    figma: {
      url: figmaUrl,
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
  };
  writeJson(path.join(out, 'figma-handoff.json'), handoff);

  writeJson(path.join(out, 'routes.json'), fixture?.routes || {
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

  writeJson(path.join(out, 'sections.json'), fixture?.sections || {
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

  writeJson(path.join(out, 'assets.json'), buildAssetsManifest(project, figma, fixture?.assets));

  writeJson(path.join(out, 'spark-divergence-ledger.json'), fixture?.divergence || {
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

  writeJson(path.join(out, 'viewport-coverage.json'), fixture?.coverage || {
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

function buildAssetsManifest(project, figma, supplied) {
  const source = supplied || {
    schema_version: SCHEMA.assets,
    figma_file_key: figma.file_key || '',
    project,
    assets: [
      {
        asset_id: 'example-asset',
        section_id: 'example-1',
        path: '',
        asset_url_path: '',
        figma_node_id: '',
        source_layer_name: '',
        prefix: 'img',
        role: '',
        alt: '',
        format: 'png',
        expected_width: 0,
        expected_height: 0,
        requires_alpha: false,
        canvas_rendered: true,
        optimization_status: 'not-started',
        replace_with_backend_product_media: false,
        clean_export_verified: false,
        notes: 'Replace this example or delete it.',
      },
    ],
  };

  return {
    schema_version: source.schema_version || SCHEMA.assets,
    figma_file_key: source.figma_file_key || figma.file_key || '',
    project: source.project || project,
    assets: (source.assets || []).map((asset) => normalizeAsset(asset)),
  };
}

function normalizeAsset(asset) {
  const assetPath = String(asset.path || '');
  const extension = path.posix.extname(assetPath).slice(1).toLowerCase();
  const normalized = {
    asset_id: asset.asset_id || '',
    section_id: asset.section_id || '',
    path: assetPath,
    asset_url_path: asset.asset_url_path ?? assetPath.replace(/^assets\//, ''),
    figma_node_id: asset.figma_node_id || '',
    source_layer_name: asset.source_layer_name || '',
    prefix: asset.prefix || 'img',
    role: asset.role || '',
    alt: asset.alt ?? '',
    format: asset.format || extension || 'png',
    expected_width: asset.expected_width ?? 0,
    expected_height: asset.expected_height ?? 0,
    requires_alpha: asset.requires_alpha ?? false,
    canvas_rendered: asset.canvas_rendered ?? true,
    optimization_status: asset.optimization_status || 'not-started',
    replace_with_backend_product_media: asset.replace_with_backend_product_media ?? false,
    clean_export_verified: asset.clean_export_verified ?? false,
    notes: asset.notes || '',
  };
  for (const key of ['max_bytes', 'forbid_badges', 'forbid_baked_text', 'decorative', 'source']) {
    if (Object.prototype.hasOwnProperty.call(asset, key)) normalized[key] = asset[key];
  }
  return normalized;
}

function validatePackage(dir, strict = true) {
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

  const routeEntries = expectArray(routes, 'routes', 'routes.json', errors);
  const sectionEntries = expectArray(sections, 'sections', 'sections.json', errors);
  const assetEntries = expectArray(assets, 'assets', 'assets.json', errors);
  const divergenceEntries = expectArray(divergence, 'entries', 'spark-divergence-ledger.json', errors);
  const viewportConfig = expectObject(coverage, 'viewports', 'viewport-coverage.json', errors);
  const coverageEntries = expectArray(coverage, 'coverage', 'viewport-coverage.json', errors);

  if (routeEntries) {
    if (!routeEntries.length) issue(strict, errors, warnings, 'routes.json: no routes recorded');
    for (const route of routeEntries) {
      if (!route.route_id) errors.push('routes.json: route missing route_id');
      if (!route.storefront_path) errors.push(`${route.route_id || 'route'}: missing storefront_path`);
      if (!route.section_order || !route.section_order.length) {
        issue(strict, errors, warnings, `${route.route_id || 'route'}: section_order is empty`);
      }
      if (!route.theme_template) issue(strict, errors, warnings, `${route.route_id || 'route'}: missing theme_template`);
      if (!route.figma_frames || !Object.values(route.figma_frames).some((frame) => frame && frame.node_id)) {
        issue(strict, errors, warnings, `${route.route_id || 'route'}: no Figma route frame node IDs recorded`);
      }
    }
  }

  if (sectionEntries) {
    if (!sectionEntries.length) issue(strict, errors, warnings, 'sections.json: no sections recorded');
    for (const section of sectionEntries) {
      const id = section.section_id || 'section';
      if (!section.section_id) errors.push('sections.json: section missing section_id');
      if (!CLASSIFICATIONS.has(section.classification)) {
        errors.push(`${id}: invalid classification "${section.classification}"`);
      }
      if (section.classification === 'screenshot-fallback' && !section.screenshot_fallback_approved) {
        errors.push(`${id}: screenshot-fallback requires screenshot_fallback_approved=true`);
      }
      if (!section.classification_rationale) issue(strict, errors, warnings, `${id}: missing classification_rationale`);
      if (!section.figma_nodes || !Object.values(section.figma_nodes).some(Boolean)) {
        issue(strict, errors, warnings, `${id}: no Figma section node IDs recorded`);
      }
      if (!section.route_id) issue(strict, errors, warnings, `${id}: missing route_id`);
      if (!section.implementation_target?.template) issue(strict, errors, warnings, `${id}: missing implementation target template`);
    }
  }

  if (assetEntries) {
    if (!assetEntries.length) issue(strict, errors, warnings, 'assets.json: no assets recorded');
    for (const asset of assetEntries) {
      const id = asset.asset_id || 'asset';
      if (!asset.asset_id) issue(strict, errors, warnings, 'assets.json: asset missing asset_id');
      if (!asset.section_id) issue(strict, errors, warnings, `${id}: missing section_id`);
      if (!ASSET_PREFIXES.has(asset.prefix)) errors.push(`${id}: invalid prefix "${asset.prefix}"`);
      if (!asset.figma_node_id) issue(strict, errors, warnings, `${id}: missing figma_node_id`);
      if (!asset.path) issue(strict, errors, warnings, `${id}: missing path`);
      if (!asset.asset_url_path) issue(strict, errors, warnings, `${id}: missing asset_url_path`);
      if (!asset.role) issue(strict, errors, warnings, `${id}: missing role`);
      if (!asset.format) issue(strict, errors, warnings, `${id}: missing format`);
      if (asset.format && !ASSET_FORMATS.has(asset.format)) {
        errors.push(`${id}: invalid format "${asset.format}"`);
      }
      if (typeof asset.requires_alpha !== 'boolean') {
        issue(strict, errors, warnings, `${id}: requires_alpha should be true or false`);
      }
      if (!Number.isInteger(asset.expected_width) || asset.expected_width <= 0) {
        issue(strict, errors, warnings, `${id}: expected_width must be a positive integer`);
      }
      if (!Number.isInteger(asset.expected_height) || asset.expected_height <= 0) {
        issue(strict, errors, warnings, `${id}: expected_height must be a positive integer`);
      }
      if (typeof asset.canvas_rendered !== 'boolean') {
        issue(strict, errors, warnings, `${id}: canvas_rendered should be true or false`);
      }
      if (!asset.optimization_status) {
        errors.push(`${id}: missing optimization_status`);
      } else if (!OPTIMIZATION_STATUSES.has(asset.optimization_status)) {
        errors.push(`${id}: invalid optimization_status "${asset.optimization_status}"`);
      }
      if (typeof asset.replace_with_backend_product_media !== 'boolean') {
        issue(strict, errors, warnings, `${id}: replace_with_backend_product_media should be true or false`);
      }
      if (typeof asset.clean_export_verified !== 'boolean') {
        issue(strict, errors, warnings, `${id}: clean_export_verified should be true or false`);
      }
      if (asset.prefix === 'img-group' && asset.clean_export_verified !== true) {
        errors.push(`${id}: img-group requires clean_export_verified=true after source review`);
      }
    }
  }

  if (divergenceEntries) {
    if (!divergenceEntries.length) issue(strict, errors, warnings, 'spark-divergence-ledger.json: no divergence entries recorded');
    for (const entry of divergenceEntries) {
      const id = entry.divergence_id || entry.surface || 'divergence';
      if (!entry.surface) issue(strict, errors, warnings, `${id}: missing surface`);
      if (!entry.pages || !entry.pages.length) issue(strict, errors, warnings, `${id}: missing pages`);
      if (!entry.figma_expectation) issue(strict, errors, warnings, `${id}: missing figma_expectation`);
      if (!entry.spark_platform_behavior) issue(strict, errors, warnings, `${id}: missing spark_platform_behavior`);
      if (!entry.implementation_guardrail) issue(strict, errors, warnings, `${id}: missing implementation_guardrail`);
      if (!entry.decision) {
        errors.push(`${id}: missing decision`);
      } else if (!DIVERGENCE_DECISIONS.has(entry.decision)) {
        errors.push(`${id}: invalid decision "${entry.decision}"`);
      }
      if (!entry.status) {
        errors.push(`${id}: missing status`);
      } else if (!DIVERGENCE_STATUSES.has(entry.status)) {
        errors.push(`${id}: invalid status "${entry.status}"`);
      }
    }
  }

  if (viewportConfig) {
    for (const name of ['desktop', 'tablet', 'mobile']) {
      const vp = viewportConfig[name];
      if (!vp || typeof vp.available !== 'boolean') {
        issue(strict, errors, warnings, `viewport-coverage.json: ${name} availability not recorded`);
        continue;
      }
      if (vp.expected_width) {
        const width = Number(vp.expected_width);
        if (!Number.isFinite(width)) {
          errors.push(`viewport-coverage.json: ${name} expected_width must be a number`);
        } else if (!VIEWPORT_WIDTHS[name].has(width)) {
          errors.push(`viewport-coverage.json: ${name} expected_width must be one of ${Array.from(VIEWPORT_WIDTHS[name]).join(', ')}`);
        }
      }
    }
  }
  if (coverageEntries && !coverageEntries.length) issue(strict, errors, warnings, 'viewport-coverage.json: no route coverage recorded');

  for (const warning of warnings) console.log(`Warning: ${warning}`);
  if (errors.length) {
    for (const error of errors) console.log(`Error: ${error}`);
    process.exit(1);
  }
  console.log(`[next-theme-figma] PASS (${strict ? 'strict' : 'non-strict'}) with ${warnings.length} warning(s)`);
}

function issue(strict, errors, warnings, message) {
  (strict ? errors : warnings).push(message);
}

function readFixture(file) {
  const fixture = JSON.parse(fs.readFileSync(file, 'utf8'));
  for (const key of ['handoff', 'routes', 'sections', 'assets', 'divergence', 'coverage']) {
    if (!fixture[key] || typeof fixture[key] !== 'object') throw new Error(`fixture missing ${key}`);
  }
  return fixture;
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

function expectArray(value, key, label, errors) {
  if (!value) return null;
  if (!Array.isArray(value[key])) {
    errors.push(`${label}: ${key} must be an array`);
    return null;
  }
  return value[key];
}

function expectObject(value, key, label, errors) {
  if (!value) return null;
  if (!value[key] || typeof value[key] !== 'object' || Array.isArray(value[key])) {
    errors.push(`${label}: ${key} must be an object`);
    return null;
  }
  return value[key];
}

function printJson(value) {
  console.log(JSON.stringify(value, null, 2));
}
