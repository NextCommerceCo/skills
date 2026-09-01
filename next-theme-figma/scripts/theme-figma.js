#!/usr/bin/env node

const fs = require('fs');
const path = require('path');

const SCHEMA = {
  handoff: 'next-theme-figma/handoff/v1',
  routes: 'next-theme-figma/routes/v0',
  sections: 'next-theme-figma/sections/v0',
  assets: 'next-theme-figma/assets/v0',
  divergence: 'next-theme-figma/platform-divergence/v1',
  coverage: 'next-theme-figma/viewport-coverage/v0',
  geometry: 'next-theme-figma/geometry/v1',
  copy: 'next-theme-figma/copy/v1',
};

const LEGACY_SCHEMA = {
  handoff: 'next-theme-figma/handoff/v0',
  divergence: 'next-theme-figma/spark-divergence/v0',
};

const CLASSIFICATIONS = new Set([
  'semantic-rebuild',
  'composed-asset',
  'background-asset',
  'live-commerce-component',
  'platform-app-hook',
  'screenshot-fallback',
]);

const ASSET_PREFIXES = new Set(['img', 'bg', 'img-group']);
const ASSET_FORMATS = new Set(['png', 'jpg', 'jpeg', 'svg', 'webp']);
const OPTIMIZATION_STATUSES = new Set(['not-started', 'source-selected', 'optimized', 'blocked']);
const DIVERGENCE_DECISIONS = new Set(['platform-wins', 'figma-wins-with-guardrails', 'needs-approval', 'blocked']);
const DIVERGENCE_STATUSES = new Set(['open', 'approved', 'implemented', 'blocked', 'accepted-gap']);
const MODES = new Set(['design-audit', 'handoff-prep', 'implementation-handoff']);
const THEME_FAMILIES = new Set(['spark', 'intro-bootstrap', 'custom']);
const THEME_FAMILY_RUNTIME_CONTRACTS = new Map([
  ['spark', 'web-components'],
  ['intro-bootstrap', 'jquery-core-js'],
  ['custom', null],
]);
const RUNTIME_CONTRACTS = new Set(['web-components', 'jquery-core-js', 'unknown']);
const VIEWPORT_WIDTHS = {
  desktop: new Set([1440]),
  tablet: new Set([768]),
  mobile: new Set([375, 390]),
};

const VIEWPORT_NAMES = ['desktop', 'tablet', 'mobile'];
const GEOMETRY_SOURCES = new Set(['figma-metadata']);
const GEOMETRY_ROLES = new Set(['text', 'image', 'icon', 'container', 'control']);
const GEOMETRY_EDGES = new Set(['left', 'right', 'top', 'bottom']);
const GEOMETRY_AXES = new Set(['vertical', 'horizontal']);
const GEOMETRY_ASSERTIONS = new Set(['position-x', 'position-y', 'width', 'height']);
const GEOMETRY_ANCHORS = new Set(['left', 'center', 'right']);
// Figma rounds sub-pixel frame geometry; a box may sit this far outside its
// parent before the overflow is treated as a real extraction error.
const GEOMETRY_BOUNDS_SLACK_PX = 1;

const COPY_SOURCES = new Set(['figma-text-layers']);
const COPY_ROLES = new Set(['heading', 'body', 'label', 'cta', 'legal', 'alt']);

if (require.main === module) main();

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
  --theme-family spark|intro-bootstrap|custom
  --runtime-contract web-components|jquery-core-js|unknown
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
  const fixture = opts.fixture ? readFixture(path.resolve(String(opts.fixture))) : null;
  if (fixture?.handoff?.schema_version === LEGACY_SCHEMA.handoff) {
    throw new Error(
      `new-package refuses legacy ${LEGACY_SCHEMA.handoff} fixtures; migrate the fixture to `
      + `${SCHEMA.handoff} with platform-divergence-ledger.json before generating`,
    );
  }

  const mode = opts.mode || 'handoff-prep';
  if (!MODES.has(mode)) {
    throw new Error(`--mode must be one of ${Array.from(MODES).join(', ')}`);
  }

  const fixtureHandoff = fixture?.handoff;
  const hasThemeFamilyFlag = Object.prototype.hasOwnProperty.call(opts, 'theme-family');
  const hasRuntimeContractFlag = Object.prototype.hasOwnProperty.call(opts, 'runtime-contract');
  const fixtureThemeFamily = fixtureHandoff?.target?.theme_family;
  const fixtureRuntimeContract = fixtureHandoff?.target?.runtime_contract;
  const hasFixtureThemeFamily = fixtureHandoff && !isMissingOrEmpty(fixtureThemeFamily);
  const hasFixtureRuntimeContract = fixtureHandoff && !isMissingOrEmpty(fixtureRuntimeContract);
  const themeFamily = hasFixtureThemeFamily
    ? fixtureThemeFamily
    : (hasThemeFamilyFlag ? opts['theme-family'] : 'custom');
  const runtimeContract = hasFixtureRuntimeContract
    ? fixtureRuntimeContract
    : (hasRuntimeContractFlag ? opts['runtime-contract'] : 'unknown');

  if (fixtureHandoff) {
    const conflicts = [];
    if (hasFixtureThemeFamily && hasThemeFamilyFlag
        && opts['theme-family'] !== themeFamily) {
      conflicts.push(
        `--theme-family ${JSON.stringify(opts['theme-family'])} conflicts with fixture handoff `
        + `target.theme_family ${JSON.stringify(themeFamily)}`,
      );
    }
    if (hasFixtureRuntimeContract && hasRuntimeContractFlag
        && opts['runtime-contract'] !== runtimeContract) {
      conflicts.push(
        `--runtime-contract ${JSON.stringify(opts['runtime-contract'])} conflicts with fixture handoff `
        + `target.runtime_contract ${JSON.stringify(runtimeContract)}`,
      );
    }
    if (conflicts.length) {
      throw new Error(`${conflicts.join('; ')}; fixture-provided identity governs`);
    }
  }

  const identityErrors = themeIdentityErrors(
    themeFamily,
    runtimeContract,
    hasFixtureThemeFamily ? 'fixture handoff target.theme_family' : '--theme-family',
    hasFixtureRuntimeContract ? 'fixture handoff target.runtime_contract' : '--runtime-contract',
  );
  if (identityErrors.length) throw new Error(identityErrors[0]);
  const divergenceFilename = 'platform-divergence-ledger.json';
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
    divergenceFilename,
    'viewport-coverage.json',
    'geometry.json',
    'copy.json',
    'validation-checklist.md',
    'notes.md',
  ];
  const existing = outputFiles.filter((file) => fs.existsSync(path.join(out, file)));
  if (existing.length && opts.force !== true) {
    throw new Error(`refusing to overwrite existing package files (${existing.join(', ')}); pass --force to replace them`);
  }

  const handoff = fixture?.handoff ? {
    ...fixture.handoff,
    target: {
      ...(fixture.handoff.target || {}),
      theme_family: themeFamily,
      runtime_contract: runtimeContract,
    },
  } : {
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
      theme_family: themeFamily,
      runtime_contract: runtimeContract,
    },
    manifests: {
      routes: 'routes.json',
      sections: 'sections.json',
      assets: 'assets.json',
      platform_divergence_ledger: 'platform-divergence-ledger.json',
      viewport_coverage: 'viewport-coverage.json',
      geometry: 'geometry.json',
      copy: 'copy.json',
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

  writeJson(path.join(out, divergenceFilename), fixture?.divergence || {
    schema_version: SCHEMA.divergence,
    entries: [
      {
        divergence_id: 'example-divergence',
        surface: '',
        pages: [],
        figma_expectation: '',
        platform_behavior: '',
        decision: 'platform-wins',
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

  writeJson(path.join(out, 'geometry.json'), fixture?.geometry || {
    schema_version: SCHEMA.geometry,
    project,
    source: 'figma-metadata',
    extracted_at: generatedAt,
    routes: routes.map((route, index) => ({
      route_id: routeId(route, index),
      viewports: {},
    })),
  });

  writeJson(path.join(out, 'copy.json'), fixture?.copy || {
    schema_version: SCHEMA.copy,
    project,
    source: 'figma-text-layers',
    extracted_at: generatedAt,
    strings: [],
    allowed_deviations: [],
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
    canvas_rendered: asset.canvas_rendered ?? true,
    optimization_status: asset.optimization_status || 'not-started',
    replace_with_backend_product_media: asset.replace_with_backend_product_media ?? false,
    clean_export_verified: asset.clean_export_verified ?? false,
    notes: asset.notes || '',
  };
  if (normalized.format !== 'svg') {
    normalized.requires_alpha = asset.requires_alpha ?? false;
  }
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
    'viewport-coverage.json',
    'validation-checklist.md',
  ];

  for (const file of required) {
    if (!fs.existsSync(path.join(dir, file))) errors.push(`missing ${file}`);
  }

  const rawHandoff = readJson(path.join(dir, 'figma-handoff.json'), errors);
  const legacyV0 = rawHandoff?.schema_version === LEGACY_SCHEMA.handoff;
  const divergenceFilename = legacyV0
    ? 'spark-divergence-ledger.json'
    : 'platform-divergence-ledger.json';
  if (!fs.existsSync(path.join(dir, divergenceFilename))) errors.push(`missing ${divergenceFilename}`);

  const rawDivergence = readJson(path.join(dir, divergenceFilename), errors);
  if (legacyV0 && rawHandoff?.manifests?.spark_divergence_ledger !== divergenceFilename) {
    errors.push(`figma-handoff.json: manifests.spark_divergence_ledger must be "${divergenceFilename}" for v0`);
  }
  if (legacyV0 && rawDivergence?.schema_version !== LEGACY_SCHEMA.divergence) {
    errors.push(`${divergenceFilename}: schema_version must be "${LEGACY_SCHEMA.divergence}" for v0`);
  }
  if (legacyV0) validateLegacyV0Identity(rawHandoff, strict, errors, warnings);
  const { handoff, divergence } = legacyV0
    ? normalizeLegacyV0(rawHandoff, rawDivergence)
    : { handoff: rawHandoff, divergence: rawDivergence };
  if (legacyV0) {
    warnings.push(
      'deprecated v0 handoff accepted; migrate to next-theme-figma/handoff/v1 and '
      + 'platform-divergence-ledger.json using platform_divergence_ledger, '
      + 'platform_behavior, platform-wins, and next-theme-figma/platform-divergence/v1',
    );
  }

  const routes = readJson(path.join(dir, 'routes.json'), errors);
  const rawSections = readJson(path.join(dir, 'sections.json'), errors);
  const sections = legacyV0 ? normalizeLegacyV0Sections(rawSections) : rawSections;
  const assets = readJson(path.join(dir, 'assets.json'), errors);
  const coverage = readJson(path.join(dir, 'viewport-coverage.json'), errors);

  expectSchema(handoff, SCHEMA.handoff, 'figma-handoff.json', errors);
  expectSchema(routes, SCHEMA.routes, 'routes.json', errors);
  expectSchema(sections, SCHEMA.sections, 'sections.json', errors);
  expectSchema(assets, SCHEMA.assets, 'assets.json', errors);
  expectSchema(divergence, SCHEMA.divergence, divergenceFilename, errors);
  expectSchema(coverage, SCHEMA.coverage, 'viewport-coverage.json', errors);

  if (handoff && !handoff.figma?.url && !handoff.figma?.file_key) {
    errors.push('figma-handoff.json: no Figma URL or file key recorded');
  }
  if (handoff) validateThemeIdentity(handoff, errors);
  if (handoff && !legacyV0
      && handoff.manifests?.platform_divergence_ledger !== 'platform-divergence-ledger.json') {
    errors.push('figma-handoff.json: manifests.platform_divergence_ledger must be "platform-divergence-ledger.json"');
  }

  const routeEntries = expectArray(routes, 'routes', 'routes.json', errors);
  const sectionEntries = expectArray(sections, 'sections', 'sections.json', errors);
  const assetEntries = expectArray(assets, 'assets', 'assets.json', errors);
  const divergenceEntries = expectArray(divergence, 'entries', divergenceFilename, errors);
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
      for (const name of ['desktop', 'tablet', 'mobile']) {
        checkPackageFile(
          dir,
          (route.reference_screenshots || {})[name],
          `${route.route_id || 'route'}: reference_screenshots.${name}`,
          strict,
          errors,
          warnings,
        );
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
      // Branch on the path extension, matching next-theme-dev's validator
      // (which keys the requires_alpha exemption off the file suffix, not the
      // declared format) so the two halves of the contract agree.
      const isSvg = typeof asset.path === 'string'
        && asset.path.toLowerCase().endsWith('.svg');
      if (!isSvg && typeof asset.requires_alpha !== 'boolean') {
        issue(strict, errors, warnings, `${id}: requires_alpha should be true or false`);
      } else if (isSvg
          && Object.prototype.hasOwnProperty.call(asset, 'requires_alpha')
          && typeof asset.requires_alpha !== 'boolean') {
        errors.push(`${id}: requires_alpha should be true or false when present`);
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
    if (!divergenceEntries.length) issue(strict, errors, warnings, 'platform-divergence-ledger.json: no divergence entries recorded');
    for (const entry of divergenceEntries) {
      const id = entry.divergence_id || entry.surface || 'divergence';
      if (!entry.surface) issue(strict, errors, warnings, `${id}: missing surface`);
      if (!entry.pages || !entry.pages.length) issue(strict, errors, warnings, `${id}: missing pages`);
      if (!entry.figma_expectation) issue(strict, errors, warnings, `${id}: missing figma_expectation`);
      if (!entry.platform_behavior) issue(strict, errors, warnings, `${id}: missing platform_behavior`);
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
  for (const entry of coverageEntries || []) {
    for (const name of ['desktop', 'tablet', 'mobile']) {
      if (entry[name] && typeof entry[name] === 'object') {
        for (const reference of ['figma_ref', 'preview_ref']) {
          checkPackageFile(
            dir,
            entry[name][reference],
            `${entry.route_id || 'coverage'}: ${name}.${reference}`,
            strict,
            errors,
            warnings,
          );
        }
      }
    }
  }
  if (coverageEntries && !coverageEntries.length) issue(strict, errors, warnings, 'viewport-coverage.json: no route coverage recorded');

  validateGeometry(dir, handoff, routeEntries, sectionEntries, legacyV0, strict, errors, warnings);
  validateCopy(dir, handoff, sectionEntries, legacyV0, strict, errors, warnings);

  for (const warning of warnings) console.log(`Warning: ${warning}`);
  if (errors.length) {
    for (const error of errors) console.log(`Error: ${error}`);
    process.exit(1);
  }
  console.log(`[next-theme-figma] PASS (${strict ? 'strict' : 'non-strict'}) with ${warnings.length} warning(s)`);
}

// Geometry manifest: per-element boxes extracted from Figma metadata, the
// deterministic input to next-theme-dev's scripts/assert-geometry.mjs. Position
// and size deltas are asserted per element; percentage pixel mismatch is
// telemetry, not an acceptance instrument.
function validateGeometry(dir, handoff, routeEntries, sectionEntries, legacyV0, strict, errors, warnings) {
  const filename = 'geometry.json';
  const file = path.join(dir, filename);
  const present = fs.existsSync(file);
  const mode = handoff?.mode;
  const declared = handoff?.manifests?.geometry;

  if (!present) {
    if (mode === 'implementation-handoff' && !legacyV0) {
      errors.push(
        `missing ${filename}: implementation-handoff packages must carry an extracted geometry manifest`,
      );
    } else {
      warnings.push(
        `${filename} not present; add one before promoting this package to implementation-handoff`,
      );
    }
    if (declared) errors.push(`figma-handoff.json: manifests.geometry names a missing ${filename}`);
    return;
  }

  if (declared !== filename) {
    errors.push(`figma-handoff.json: manifests.geometry must be "${filename}"`);
  }

  const geometry = readJson(file, errors);
  if (!geometry) return;
  expectSchema(geometry, SCHEMA.geometry, filename, errors);

  if (!GEOMETRY_SOURCES.has(geometry.source)) {
    errors.push(
      `${filename}: source must be one of ${Array.from(GEOMETRY_SOURCES).join(', ')}`
      + ' (geometry is extracted from Figma metadata, never transcribed by hand)',
    );
  }

  const geometryRoutes = expectArray(geometry, 'routes', filename, errors);
  if (!geometryRoutes) return;
  if (!geometryRoutes.length) issue(strict, errors, warnings, `${filename}: no routes recorded`);

  const knownRoutes = new Set((routeEntries || []).map((route) => route.route_id).filter(Boolean));
  const knownSections = new Set((sectionEntries || []).map((entry) => entry.section_id).filter(Boolean));
  const seenRoutes = new Set();

  for (const route of geometryRoutes) {
    const routeLabel = `${filename}: ${route.route_id || 'route'}`;
    if (!route.route_id) {
      errors.push(`${filename}: route missing route_id`);
      continue;
    }
    if (seenRoutes.has(route.route_id)) errors.push(`${routeLabel}: duplicate route_id`);
    seenRoutes.add(route.route_id);
    if (knownRoutes.size && !knownRoutes.has(route.route_id)) {
      errors.push(`${routeLabel}: route_id is not in routes.json`);
    }

    const viewports = expectObject(route, 'viewports', routeLabel, errors);
    if (!viewports) continue;
    const viewportNames = Object.keys(viewports);
    if (!viewportNames.length) issue(strict, errors, warnings, `${routeLabel}: no viewports recorded`);

    for (const name of viewportNames) {
      const frame = viewports[name];
      const frameLabel = `${routeLabel}.${name}`;
      if (!VIEWPORT_NAMES.includes(name)) {
        errors.push(`${frameLabel}: unknown viewport (expected ${VIEWPORT_NAMES.join(', ')})`);
        continue;
      }
      if (!frame || typeof frame !== 'object') {
        errors.push(`${frameLabel}: must be an object`);
        continue;
      }
      if (!frame.frame_node_id) issue(strict, errors, warnings, `${frameLabel}: missing frame_node_id`);

      const frameWidth = geometryNumber(frame.frame_width, `${frameLabel}.frame_width`, errors);
      const frameHeight = geometryNumber(frame.frame_height, `${frameLabel}.frame_height`, errors);
      if (frameWidth !== null && !VIEWPORT_WIDTHS[name].has(frameWidth)) {
        errors.push(
          `${frameLabel}: frame_width ${frameWidth} must be one of `
          + `${Array.from(VIEWPORT_WIDTHS[name]).join(', ')}`,
        );
      }

      const sections = expectArray(frame, 'sections', frameLabel, errors);
      if (!sections) continue;
      if (!sections.length) issue(strict, errors, warnings, `${frameLabel}: no sections recorded`);

      const seenSections = new Set();
      for (const section of sections) {
        const sectionLabel = `${frameLabel}.${section.section_id || 'section'}`;
        if (!section.section_id) {
          errors.push(`${frameLabel}: section missing section_id`);
          continue;
        }
        if (seenSections.has(section.section_id)) errors.push(`${sectionLabel}: duplicate section_id`);
        seenSections.add(section.section_id);
        if (knownSections.size && !knownSections.has(section.section_id)) {
          errors.push(`${sectionLabel}: section_id is not in sections.json`);
        }
        if (!section.node_id) issue(strict, errors, warnings, `${sectionLabel}: missing node_id`);
        if (!section.selector) errors.push(`${sectionLabel}: missing selector`);

        const box = geometryBox(section.box, sectionLabel, errors);
        if (box && frameWidth !== null && frameHeight !== null) {
          geometryWithin(box, { x: 0, y: 0, width: frameWidth, height: frameHeight },
            sectionLabel, 'frame', errors, warnings);
        }

        const elements = expectArray(section, 'elements', sectionLabel, errors);
        const elementIds = new Set();
        const selectors = new Map();
        if (elements) {
          if (!elements.length) {
            issue(strict, errors, warnings, `${sectionLabel}: no elements recorded`);
          }
          for (const element of elements) {
            const elementLabel = `${sectionLabel}.${element.element_id || 'element'}`;
            if (!element.element_id) {
              errors.push(`${sectionLabel}: element missing element_id`);
              continue;
            }
            if (elementIds.has(element.element_id)) errors.push(`${elementLabel}: duplicate element_id`);
            elementIds.add(element.element_id);
            if (!element.node_id) issue(strict, errors, warnings, `${elementLabel}: missing node_id`);
            if (!element.selector) {
              errors.push(`${elementLabel}: missing selector`);
            } else if (selectors.has(element.selector)) {
              errors.push(
                `${elementLabel}: selector "${element.selector}" is already used by `
                + `${selectors.get(element.selector)}`,
              );
            } else {
              selectors.set(element.selector, element.element_id);
            }
            if (element.role !== undefined && !GEOMETRY_ROLES.has(element.role)) {
              errors.push(`${elementLabel}: invalid role "${element.role}"`);
            }
            if (element.tolerance_px !== undefined) {
              geometryNumber(element.tolerance_px, `${elementLabel}.tolerance_px`, errors);
            }
            if (element.assert !== undefined) {
              if (!Array.isArray(element.assert) || !element.assert.length) {
                errors.push(`${elementLabel}: assert must be a non-empty array`);
              } else {
                for (const name of element.assert) {
                  if (!GEOMETRY_ASSERTIONS.has(name)) {
                    errors.push(`${elementLabel}: invalid assert entry "${name}"`);
                  }
                }
              }
            }
            if (element.align_anchor !== undefined && !GEOMETRY_ANCHORS.has(element.align_anchor)) {
              errors.push(`${elementLabel}: invalid align_anchor "${element.align_anchor}"`);
            }
            // Element boxes are section-relative, so an element that does not
            // touch its own section box means the extraction picked the wrong
            // parent — the failure the manifest exists to make impossible.
            const elementBox = geometryBox(element.box, elementLabel, errors);
            if (elementBox && box) {
              geometryWithin(
                elementBox,
                { x: 0, y: 0, width: box.width, height: box.height },
                elementLabel,
                'section',
                errors,
                warnings,
              );
            }
          }
        }

        for (const group of section.alignment_groups || []) {
          const groupLabel = `${sectionLabel}.${group.group_id || 'alignment_group'}`;
          if (!group.group_id) errors.push(`${sectionLabel}: alignment group missing group_id`);
          if (!GEOMETRY_EDGES.has(group.edge)) {
            errors.push(`${groupLabel}: invalid edge "${group.edge}"`);
          }
          const ids = Array.isArray(group.element_ids) ? group.element_ids : [];
          if (ids.length < 2) errors.push(`${groupLabel}: needs at least two element_ids`);
          for (const id of ids) {
            if (!elementIds.has(id)) errors.push(`${groupLabel}: unknown element_id "${id}"`);
          }
        }

        for (const gap of section.gaps || []) {
          const gapLabel = `${sectionLabel}.${gap.gap_id || 'gap'}`;
          if (!gap.gap_id) errors.push(`${sectionLabel}: gap missing gap_id`);
          if (!GEOMETRY_AXES.has(gap.axis)) errors.push(`${gapLabel}: invalid axis "${gap.axis}"`);
          for (const key of ['from', 'to']) {
            if (!elementIds.has(gap[key])) errors.push(`${gapLabel}: unknown ${key} element_id "${gap[key]}"`);
          }
          geometryNumber(gap.value, `${gapLabel}.value`, errors);
        }
      }
    }
  }
}

// Copy manifest: the verbatim text inventory extracted from Figma text layers
// at handoff time. scripts/copy-lint.py diffs built templates against it, so
// invented copy fails at the builder gate instead of in a review round.
function validateCopy(dir, handoff, sectionEntries, legacyV0, strict, errors, warnings) {
  const filename = 'copy.json';
  const file = path.join(dir, filename);
  const present = fs.existsSync(file);
  const declared = handoff?.manifests?.copy;

  if (!present) {
    if (handoff?.mode === 'implementation-handoff' && !legacyV0) {
      errors.push(
        `missing ${filename}: implementation-handoff packages must carry a verbatim copy inventory`,
      );
    } else {
      warnings.push(
        `${filename} not present; add one before promoting this package to implementation-handoff`,
      );
    }
    if (declared) errors.push(`figma-handoff.json: manifests.copy names a missing ${filename}`);
    return;
  }

  if (declared !== filename) errors.push(`figma-handoff.json: manifests.copy must be "${filename}"`);

  const copy = readJson(file, errors);
  if (!copy) return;
  expectSchema(copy, SCHEMA.copy, filename, errors);
  if (!COPY_SOURCES.has(copy.source)) {
    errors.push(
      `${filename}: source must be one of ${Array.from(COPY_SOURCES).join(', ')}`
      + ' (copy is extracted from Figma text layers, never retyped)',
    );
  }

  const knownSections = new Set((sectionEntries || []).map((entry) => entry.section_id).filter(Boolean));
  const strings = expectArray(copy, 'strings', filename, errors);
  const copyIds = new Set();
  if (strings) {
    if (!strings.length) issue(strict, errors, warnings, `${filename}: no strings recorded`);
    for (const entry of strings) {
      const label = `${filename}: ${entry.copy_id || 'string'}`;
      if (!entry.copy_id) {
        errors.push(`${filename}: string missing copy_id`);
        continue;
      }
      if (copyIds.has(entry.copy_id)) errors.push(`${label}: duplicate copy_id`);
      copyIds.add(entry.copy_id);
      if (typeof entry.text !== 'string' || !entry.text.trim()) {
        errors.push(`${label}: text must be a non-empty string`);
      }
      if (!entry.node_id) issue(strict, errors, warnings, `${label}: missing node_id`);
      if (entry.role !== undefined && !COPY_ROLES.has(entry.role)) {
        errors.push(`${label}: invalid role "${entry.role}"`);
      }
      if (!entry.section_id) {
        issue(strict, errors, warnings, `${label}: missing section_id`);
      } else if (knownSections.size && !knownSections.has(entry.section_id)) {
        errors.push(`${label}: section_id is not in sections.json`);
      }
    }
  }

  for (const deviation of copy.allowed_deviations || []) {
    const label = `${filename}: allowed deviation ${deviation.deviation_id || '(unnamed)'}`;
    if (!deviation.deviation_id) errors.push(`${filename}: allowed deviation missing deviation_id`);
    const hasText = typeof deviation.text === 'string' && deviation.text.length > 0;
    const hasPattern = typeof deviation.pattern === 'string' && deviation.pattern.length > 0;
    if (hasText === hasPattern) {
      errors.push(`${label}: needs exactly one of text or pattern`);
    }
    if (hasPattern) {
      try {
        new RegExp(deviation.pattern);
      } catch {
        errors.push(`${label}: pattern is not a valid regular expression`);
      }
    }
    // A deviation without a recorded reason is indistinguishable from copy
    // drift that someone silenced, which is what this manifest exists to catch.
    if (!deviation.reason) errors.push(`${label}: missing reason`);
    if (!deviation.approved_by) issue(strict, errors, warnings, `${label}: missing approved_by`);
  }
}

function geometryNumber(value, label, errors) {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    errors.push(`${label}: must be a finite number`);
    return null;
  }
  return value;
}

function geometryBox(box, label, errors) {
  if (!box || typeof box !== 'object') {
    errors.push(`${label}: missing box`);
    return null;
  }
  const parsed = {};
  for (const key of ['x', 'y', 'width', 'height']) {
    const value = geometryNumber(box[key], `${label}.box.${key}`, errors);
    if (value === null) return null;
    parsed[key] = value;
  }
  if (parsed.width < 0 || parsed.height < 0) {
    errors.push(`${label}: box width and height must not be negative`);
    return null;
  }
  return parsed;
}

function geometryWithin(box, parent, label, parentLabel, errors, warnings) {
  const intersects = box.x < parent.width && box.y < parent.height
    && box.x + box.width > 0 && box.y + box.height > 0;
  if (!intersects) {
    errors.push(`${label}: box lies entirely outside its ${parentLabel} box`);
    return;
  }
  const overflow = Math.max(
    -box.x,
    -box.y,
    box.x + box.width - parent.width,
    box.y + box.height - parent.height,
  );
  if (overflow > GEOMETRY_BOUNDS_SLACK_PX) {
    warnings.push(`${label}: box extends ${Math.round(overflow)}px beyond its ${parentLabel} box`);
  }
}

function normalizeLegacyV0(handoff, divergence) {
  const normalizedHandoff = handoff ? {
    ...handoff,
    schema_version: SCHEMA.handoff,
    target: {
      ...(handoff.target || {}),
      theme_family: 'spark',
      runtime_contract: 'web-components',
    },
    manifests: {
      ...(handoff.manifests || {}),
      platform_divergence_ledger: 'platform-divergence-ledger.json',
    },
  } : handoff;
  const normalizedDivergence = divergence ? {
    ...divergence,
    schema_version: SCHEMA.divergence,
    entries: Array.isArray(divergence.entries)
      ? divergence.entries.map((entry) => ({
        ...entry,
        platform_behavior: entry.platform_behavior ?? entry.spark_platform_behavior,
        decision: entry.decision === 'spark-wins' ? 'platform-wins' : entry.decision,
      }))
      : divergence.entries,
  } : divergence;
  return { handoff: normalizedHandoff, divergence: normalizedDivergence };
}

function normalizeLegacyV0Sections(sections) {
  if (!sections || !Array.isArray(sections.sections)) return sections;
  return {
    ...sections,
    sections: sections.sections.map((section) => ({
      ...section,
      classification: section.classification === 'live-spark-component'
        ? 'live-commerce-component'
        : section.classification,
    })),
  };
}

function validateLegacyV0Identity(handoff, strict, errors, warnings) {
  const family = handoff?.target?.theme_family;
  const runtime = handoff?.target?.runtime_contract;
  const familyIsSpark = typeof family === 'string' && family.trim().toLowerCase() === 'spark';
  const runtimeIsWebComponents = typeof runtime === 'string'
    && runtime.trim().toLowerCase() === 'web-components';

  if (!isMissingOrEmpty(family) && !familyIsSpark) {
    issue(
      strict,
      errors,
      warnings,
      `figma-handoff.json: legacy v0 target.theme_family ${JSON.stringify(family)} is invalid; `
      + `v0 packages are Spark-only; migrate to ${SCHEMA.handoff} to declare another family`,
    );
  }
  if (!isMissingOrEmpty(runtime) && !runtimeIsWebComponents) {
    issue(
      strict,
      errors,
      warnings,
      `figma-handoff.json: legacy v0 target.runtime_contract ${JSON.stringify(runtime)} is invalid; `
      + `v0 packages are Spark-only and require "web-components"; migrate to ${SCHEMA.handoff} `
      + 'to declare another runtime contract',
    );
  }
}

function isMissingOrEmpty(value) {
  return value == null || (typeof value === 'string' && value.trim() === '');
}

function validateThemeIdentity(handoff, errors) {
  const family = handoff.target?.theme_family;
  const runtime = handoff.target?.runtime_contract;
  errors.push(...themeIdentityErrors(
    family,
    runtime,
    'figma-handoff.json: target.theme_family',
    'target.runtime_contract',
  ));
}

function themeIdentityErrors(family, runtime, familyLabel, runtimeLabel) {
  const errors = [];
  if (!THEME_FAMILIES.has(family)) {
    errors.push(`${familyLabel} must be one of ${Array.from(THEME_FAMILIES).join(', ')}`);
  }
  if (!RUNTIME_CONTRACTS.has(runtime)) {
    errors.push(`${runtimeLabel} must be one of ${Array.from(RUNTIME_CONTRACTS).join(', ')}`);
  }
  if (THEME_FAMILIES.has(family) && !THEME_FAMILY_RUNTIME_CONTRACTS.has(family)) {
    errors.push(`${familyLabel} "${family}" has no runtime contract policy`);
    return errors;
  }
  const expectedRuntime = THEME_FAMILY_RUNTIME_CONTRACTS.get(family);
  if (expectedRuntime && runtime && runtime !== expectedRuntime) {
    errors.push(
      `${familyLabel} "${family}" contradicts `
      + `${runtimeLabel} "${runtime}"; expected "${expectedRuntime}"`,
    );
  }
  return errors;
}

function issue(strict, errors, warnings, message) {
  (strict ? errors : warnings).push(message);
}

function checkPackageFile(dir, value, label, strict, errors, warnings) {
  if (typeof value !== 'string' || !value) return;
  if (path.isAbsolute(value) || /^(?:[A-Za-z]:[\\/]|\\\\)/.test(value)) {
    errors.push(`${label}: must be a relative path inside the package`);
    return;
  }
  // Containment is checked on the resolved path, not the literal string, so
  // drive-letter/UNC prefixes and symlinked segments cannot escape the package.
  const packageRoot = path.resolve(dir);
  const target = path.resolve(dir, value);
  if (target !== packageRoot && !target.startsWith(packageRoot + path.sep)) {
    errors.push(`${label}: must be a relative path inside the package`);
    return;
  }
  let real;
  try {
    real = fs.realpathSync(target);
  } catch {
    issue(strict, errors, warnings, `${label}: file not found: ${value}`);
    return;
  }
  const realRoot = fs.realpathSync(packageRoot);
  if (real !== realRoot && !real.startsWith(realRoot + path.sep)) {
    errors.push(`${label}: resolves outside the package (symlink escape): ${value}`);
    return;
  }
  if (!fs.statSync(real).isFile()) {
    issue(strict, errors, warnings, `${label}: not a file: ${value}`);
  }
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
- [ ] Platform divergence ledger covers PDP/cart/header/app surfaces.
- [ ] Visual mismatches are marked fix-now, platform-divergence, designer-input-needed, or accepted-gap.
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

module.exports = {
  THEME_FAMILIES,
  THEME_FAMILY_RUNTIME_CONTRACTS,
  validateThemeIdentity,
};
