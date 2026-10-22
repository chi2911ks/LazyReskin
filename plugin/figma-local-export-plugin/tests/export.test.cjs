const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const directory = path.resolve(__dirname, '..');
const pluginCode = fs.readFileSync(path.join(directory, 'code.js'), 'utf8');
let nextId = 1;

function node(name, type = 'COMPONENT', children = [], extra = {}) {
  const comp = extra.mainComponent !== undefined ? extra.mainComponent : null;
  const cleanedExtra = { ...extra };
  delete cleanedExtra.mainComponent;

  const result = {
    id: `1:${nextId++}`, name, type, children, visible: true,
    width: 100, height: 100, x: 0, y: 0,
    absoluteBoundingBox: { x: 0, y: 0, width: 100, height: 100 },
    calls: [],
    async exportAsync(settings) {
      this.calls.push(settings);
      return new TextEncoder().encode(this.id + ':' + this.name);
    },
    async getMainComponentAsync() {
      return comp;
    },
    get mainComponent() {
      throw new Error('in get_mainComponent: Cannot call with documentAccess: dynamic-page. Use node.getMainComponentAsync instead.');
    },
    ...cleanedExtra
  };
  children.forEach(child => { child.parent = result; });
  return result;
}

function setup(pages, selection = []) {
  const messages = [];
  const document = node('Test file', 'DOCUMENT', pages);
  let loaded = false;
  document.findAllWithCriteria = criteria => {
    assert.equal(loaded, true, 'all pages must load before document traversal');
    const found = [];
    function walk(current) {
      if (criteria.types.includes(current.type)) found.push(current);
      current.children.forEach(walk);
    }
    walk(document);
    return found;
  };
  pages[0].selection = selection;
  const figma = {
    root: document, currentPage: pages[0], mixed: Symbol('mixed'),
    showUI() {}, closePlugin() {},
    async loadAllPagesAsync() { loaded = true; },
    ui: { postMessage(message) { messages.push(structuredClone(message)); } }
  };
  const context = vm.createContext({ figma, __html__: '', console });
  vm.runInContext(pluginCode, context);
  return {
    context, messages, figma,
    async run(type, options = {}) {
      await figma.ui.onmessage({ type, options });
      const end = messages.findLast(message => message.type === 'result-end');
      return end && JSON.parse(end.json);
    }
  };
}

test('exports Created in this file components on other pages without name prefixes', async () => {
  const launcher = node('ic_launcher', 'FRAME');
  const names = ['nav', 'fi_17765571', '8356127 3887362 1', 'Frame 1', 'VTV1_logo_2013_final', 'Group 7613', 'Scr 2'];
  const components = names.map(name => node(name));
  const runtime = setup([node('App', 'PAGE', [launcher]), node('Library', 'PAGE', components)], [launcher]);
  const result = await runtime.run('export-local-components');
  assert.equal(result.meta.assetCount, names.length);
  assert.equal(result.screens.length, 0);
  assert.deepEqual(result.assetExports.map(asset => asset.nodeName), names);
  assert.ok(result.assetExports.every(asset => asset.page.name === 'Library' && asset.status === 'exported'));
  assert.equal(launcher.calls.length, 0);
  assert.match(result.assetExports[2].filename, /^asset_8356127/);
});

test('exports every visible component variant, including boolean false variants', async () => {
  const a = node('Selected=false', 'COMPONENT', [], { variantProperties: { Selected: 'false' } });
  const b = node('Selected=true');
  const hidden = node('Hidden', 'COMPONENT', [], { visible: false });
  const remote = node('Remote', 'COMPONENT', [], { remote: true });
  const runtime = setup([node('Library', 'PAGE', [node('Buttons', 'COMPONENT_SET', [a, b, hidden]), remote])]);
  const result = await runtime.run('export-local-components');
  assert.equal(result.meta.assetCount, 2);
  assert.equal(result.meta.hiddenComponentCount, 1);
  assert.equal(result.meta.complete, false);
  assert.equal(result.assetExports[0].variantProperties.Selected, 'false');
});

test('selected group exports intact once; leaf images and vectors can be selected', async () => {
  const label = node('Breaking news', 'TEXT');
  const group = node('Frame 1', 'GROUP', [label]);
  const image = node('Photo', 'RECTANGLE', [], { fills: [{ type: 'IMAGE', visible: true }] });
  const vector = node('Shape', 'VECTOR');
  const runtime = setup([node('Page', 'PAGE', [group, image, vector])], [group, label, image, vector]);
  const result = await runtime.run('export-selected-assets', { includeAssets: false });
  assert.equal(result.meta.assetCount, 3);
  assert.equal(label.visible, true);
  assert.equal(label.calls.length, 0);
  assert.equal(group.calls[0].format, 'PNG');
});

test('selected component sets export variants separately', async () => {
  const variants = [node('Default'), node('Pressed')];
  const set = node('Button', 'COMPONENT_SET', variants);
  const runtime = setup([node('Page', 'PAGE', [set])], [set]);
  const result = await runtime.run('export-selected-assets');
  assert.equal(result.meta.assetCount, 2);
  assert.equal(set.calls.length, 0);
});

test('same-name components preserve separate files, including a colliding suffix name', async () => {
  const first = node('ic_logo');
  const second = node('ic_logo');
  const third = node(`ic_logo__${first.id.replace(':', '_')}`);
  const runtime = setup([node('Page', 'PAGE', [first, second, third])]);
  const result = await runtime.run('export-local-components');
  const filenames = result.assetExports.map(asset => asset.filename);
  assert.equal(result.meta.assetCount, 3);
  assert.equal(new Set(filenames).size, 3);
  const sent = runtime.messages.filter(message => message.type === 'result-asset');
  assert.deepEqual(sent.map(message => message.asset.filename), filenames);
  assert.equal(new Set(sent.map(message => message.asset.base64)).size, 3);
});

test('asset errors do not mutate child visibility and do not count as successful files', async () => {
  const child = node('Headline', 'TEXT');
  const broken = node('img_banner', 'COMPONENT', [child], {
    async exportAsync() { assert.equal(child.visible, true); throw new Error('Export failed'); }
  });
  const good = node('Other');
  const runtime = setup([node('Page', 'PAGE', [broken, good])]);
  const result = await runtime.run('export-local-components');
  assert.equal(child.visible, true);
  assert.equal(result.meta.assetCount, 1);
  assert.equal(result.meta.failedCount, 1);
  assert.equal(result.meta.complete, false);
  assert.equal(result.assetExports[0].status, 'failed');
  assert.equal(result.meta.errors[0].message, 'Export failed');
});

test('limits produce skipped manifest entries, never silent truncation', async () => {
  const runtime = setup([node('Page', 'PAGE', [node('One'), node('Two'), node('Three')])]);
  const result = await runtime.run('export-local-components', { maxAssets: 1 });
  assert.equal(result.meta.candidateAssetCount, 3);
  assert.equal(result.meta.assetCount, 1);
  assert.equal(result.meta.skippedCount, 2);
  assert.equal(result.meta.complete, false);
  assert.deepEqual(result.assetExports.map(asset => asset.status), ['exported', 'skipped', 'skipped']);
});

test('PNG override and scale reach exportAsync for icons', async () => {
  const icon = node('ic_launcher');
  const runtime = setup([node('Page', 'PAGE', [icon])], [icon]);
  const result = await runtime.run('export-selected-assets', { assetFormat: 'png', pngScale: 3 });
  assert.equal(icon.calls[0].format, 'PNG');
  assert.equal(icon.calls[0].constraint.value, 3);
  assert.match(result.assetExports[0].filename, /\.png$/);
});

test('screen export still supplies layout JSON and named SVG/PNG assets', async () => {
  const icon = node('ic_back', 'INSTANCE');
  const picture = node('img_banner', 'RECTANGLE');
  const screen = node('Home', 'FRAME', [icon, picture]);
  const runtime = setup([node('Page', 'PAGE', [screen])], [screen]);
  const result = await runtime.run('export-selection'); 
  assert.equal(result.screens.length, 1);
  assert.equal(result.screens[0].flattened.length, 3);
  assert.equal(result.meta.assetCount, 2);
  assert.equal(icon.calls[0].format, 'SVG');
  assert.equal(picture.calls[0].format, 'PNG');
});

test('screen JSON without assets remains supported', async () => {
  const icon = node('ic_back', 'INSTANCE');
  const screen = node('Home', 'FRAME', [icon]);
  const runtime = setup([node('Page', 'PAGE', [screen])], [screen]);
  const result = await runtime.run('export-selection', { includeAssets: false });
  assert.equal(result.screens.length, 1);
  assert.equal(result.meta.assetCount, 0);
  assert.equal(icon.calls.length, 0);
});

test('page loading failures surface as errors and release the export lock', async () => {
  const component = node('Logo');
  const runtime = setup([node('Page', 'PAGE', [component])], [component]);
  runtime.figma.loadAllPagesAsync = async () => { throw new Error('Page unavailable'); };
  assert.equal(await runtime.run('export-local-components'), undefined);
  assert.equal(runtime.messages.at(-1).message, 'Page unavailable');
  assert.equal(runtime.context.exportInProgress, false);
  const result = await runtime.run('export-selected-assets');
  assert.equal(result.meta.assetCount, 1);
});

test('overlapping export requests do not interleave streams', async () => {
  let finish;
  const component = node('Logo', 'COMPONENT', [], {
    exportAsync() { return new Promise(resolve => { finish = resolve; }); }
  });
  const runtime = setup([node('Page', 'PAGE', [component])], [component]);
  const first = runtime.run('export-selected-assets');
  await runtime.run('export-selected-assets');
  assert.equal(runtime.messages.filter(message => message.type === 'result-start').length, 1);
  finish(new Uint8Array([1, 2, 3]));
  await first;
  assert.equal(runtime.messages.filter(message => message.type === 'result-end').length, 1);
});

function setupUI() {
  const html = fs.readFileSync(path.join(directory, 'ui.html'), 'utf8');
  const elements = Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map(match => [match[1], {
    value: '', checked: false, disabled: false, hidden: false, textContent: '', className: '',
    attributes: {}, select() {}, focus() { this.focused = true; },
    setAttribute(name, value) { this.attributes[name] = value; },
    removeAttribute(name) { delete this.attributes[name]; }
  }]));
  elements.pngScale.value = '2';
  elements.maxAssets.value = '3000';
  elements.assetFormat.value = 'auto';
  const downloads = [];
  const downloadNames = [];
  const requests = [];
  const context = vm.createContext({
    Blob, TextEncoder, Uint8Array, Uint32Array, atob,
    parent: { postMessage(message) { requests.push(message); } },
    document: {
      getElementById: id => elements[id],
      execCommand() { return true; },
      createElement: () => {
        const el = {
          click() {
            if (el.download) downloadNames.push(el.download);
          }
        };
        return el;
      }
    },
    URL: { createObjectURL(blob) { downloads.push(blob); return 'blob:test'; }, revokeObjectURL() {} }
  });
  vm.runInContext(html.match(/<script>([\s\S]*?)<\/script>/)[1], context);
  return { elements, downloads, downloadNames, requests, context, message: msg => context.onmessage({ data: { pluginMessage: msg } }) };
}

async function zipEntries(blob) {
  const buffer = Buffer.from(await blob.arrayBuffer());
  const entries = {};
  let offset = 0;
  while (buffer.readUInt32LE(offset) === 0x04034b50) {
    const length = buffer.readUInt32LE(offset + 18);
    const nameLength = buffer.readUInt16LE(offset + 26);
    const extraLength = buffer.readUInt16LE(offset + 28);
    const name = buffer.toString('utf8', offset + 30, offset + 30 + nameLength);
    const start = offset + 30 + nameLength + extraLength;
    entries[name] = buffer.subarray(start, start + length);
    offset = start + length;
  }
  return entries;
}

test('UI bundles final manifest, exact asset filenames and failures without counting failures as files', async () => {
  const runtime = setup([node('Page', 'PAGE', [node('Logo'), node('Logo')])]);
  const result = await runtime.run('export-local-components', { maxAssets: 1 });
  const ui = setupUI();
  assert.equal(ui.elements.btnAssets.disabled, true);
  ui.elements.btnSel.onclick();
  ui.elements.btnExport.onclick();
  assert.equal(ui.elements.btnSel.disabled, true);
  runtime.messages.forEach(ui.message);
  assert.equal(ui.elements.btnAssets.disabled, false);
  assert.match(ui.elements.status.textContent, /Exported: 1.*Over limit: 1/);
  ui.elements.btnAssets.onclick();
  const entries = await zipEntries(ui.downloads[0]);
  const manifest = JSON.parse(entries['assets.manifest.json']);
  assert.equal(manifest.assets.length, 2);
  assert.equal(manifest.assets[1].status, 'skipped');
  assert.ok(entries['assets/' + result.assetExports[0].filename]);
  assert.equal(entries['assets/' + result.assetExports[1].filename], undefined);
  assert.equal(JSON.parse(entries['meta.json']).assetCount, 1);
  assert.match(ui.elements.status.textContent, /partial bundle/);
});

test('UI clears old data and blocks partial/stale downloads after an export error', () => {
  const ui = setupUI();
  ui.elements.btnSel.onclick();
  ui.elements.btnExport.onclick();
  ui.message({ type: 'result-start', json: '{"screens":[]}', screenshots: [] });
  assert.equal(ui.elements.btnAssets.disabled, true);
  ui.message({ type: 'error', message: 'Cannot load file' });
  assert.equal(ui.elements.btnSel.disabled, false);
  assert.equal(ui.elements.btnAssets.disabled, true);
  ui.elements.btnAssets.onclick();
  assert.equal(ui.downloads.length, 0);
});

test('UI source selection does not export until the primary action is pressed', () => {
  const ui = setupUI();
  ui.elements.btnPage.onclick();
  assert.equal(ui.requests.length, 0);
  assert.equal(ui.elements.btnPage.attributes['aria-pressed'], 'true');
  assert.equal(ui.elements.btnSel.attributes['aria-pressed'], 'false');
  assert.equal(ui.elements.exportLabel.textContent, 'Export whole page');
  assert.equal(ui.elements.includeAssetsRow.hidden, false);
  assert.equal(ui.elements.screensRow.hidden, false);
  ui.elements.btnExport.onclick();
  assert.equal(ui.requests[0].pluginMessage.type, 'export-page');
  assert.equal(ui.elements.btnExport.disabled, true);
  assert.equal(ui.elements.assetFormat.disabled, true);
  assert.equal(ui.elements.resultBadge.textContent, 'Exporting');
});

test('UI invalid asset limit focuses the field and blocks export', () => {
  const ui = setupUI();
  ui.elements.maxAssets.value = '0';
  ui.elements.btnExport.onclick();
  assert.equal(ui.requests.length, 0);
  assert.equal(ui.elements.advancedOptions.open, true);
  assert.equal(ui.elements.maxAssetsError.hidden, false);
  assert.equal(ui.elements.maxAssets.attributes['aria-invalid'], 'true');
  assert.equal(ui.elements.maxAssets.focused, true);
});

test('UI error restores source and options for retry', () => {
  const ui = setupUI();
  ui.elements.btnExport.onclick();
  ui.message({ type: 'error', message: 'No selection.' });
  assert.equal(ui.elements.btnExport.disabled, false);
  assert.equal(ui.elements.maxAssets.disabled, false);
  assert.equal(ui.elements.exportProgress.hidden, true);
  assert.equal(ui.elements.resultBadge.textContent, 'Error');
  assert.equal(ui.elements.statusBox.attributes['data-state'], 'error');
  assert.match(ui.elements.status.textContent, /retry/);
});

test('UI whole page mode selects whole page and triggers export-page', () => {
  const ui = setupUI();
  ui.elements.btnPage.onclick();
  assert.equal(ui.requests.length, 0);
  assert.equal(ui.elements.btnPage.attributes['aria-pressed'], 'true');
  assert.equal(ui.elements.exportLabel.textContent, 'Export whole page');
  assert.equal(ui.elements.includeAssetsRow.hidden, false);
  assert.equal(ui.elements.screensRow.hidden, false);
  ui.elements.btnExport.onclick();
  assert.equal(ui.requests[0].pluginMessage.type, 'export-page');
});

test('screen export captures screenshots for every screen by default', async () => {
  const screen1 = node('Home', 'FRAME', []);
  const screen2 = node('Player', 'FRAME', []);
  const runtime = setup([node('Page', 'PAGE', [screen1, screen2])], [screen1, screen2]);
  await runtime.run('export-selection');
  const start = runtime.messages.find(m => m.type === 'result-start');
  assert.equal(start.screenshots.length, 2);
  assert.match(start.screenshots[0].filename, /home.*\.png$/i);
  assert.match(start.screenshots[1].filename, /player.*\.png$/i);
});

test('smart asset detection extracts image fills and vector icons without ic_ or img_ prefix', async () => {
  const channelLogo = node('HBO HD', 'RECTANGLE', [], {
    fills: [{ type: 'IMAGE', visible: true }]
  });
  const searchVector1 = node('Vector 1', 'VECTOR', []);
  const searchVector2 = node('Vector 2', 'VECTOR', []);
  const searchIcon = node('Search Icon', 'FRAME', [searchVector1, searchVector2]);
  searchIcon.absoluteBoundingBox = { x: 0, y: 0, width: 24, height: 24 };
  const screen = node('LiveTV', 'FRAME', [channelLogo, searchIcon]);
  const runtime = setup([node('Page', 'PAGE', [screen])], [screen]);
  const result = await runtime.run('export-selection', { assetScope: 'all_artwork' });
  assert.equal(result.assetExports.length, 2);
  const kinds = result.assetExports.map(a => a.kind);
  assert.ok(kinds.includes('image_png'));
  assert.ok(kinds.includes('vector_svg'));
});

test('library asset matching extracts assets matching library components by name across screens', async () => {
  // Library page with components
  const libPlay = node('Play', 'COMPONENT', []);
  const libSearch = node('Search', 'COMPONENT', []);
  const libUnused = node('UnusedIcon', 'COMPONENT', []);
  const assetsPage = node('Assets', 'PAGE', [libPlay, libSearch, libUnused]);

  // Screens page with 2 screens using library assets by name/instance
  const screen1Icon = node('Play', 'INSTANCE', [], { mainComponent: libPlay });
  const screen1Div = node('Divider', 'FRAME', []); // regular layout, not in library
  const screen1 = node('Home', 'FRAME', [screen1Icon, screen1Div]);

  const screen2Icon = node('Search', 'FRAME', []); // named exactly like library component
  const screen2 = node('Player', 'FRAME', [screen2Icon]);
  const screensPage = node('Screens', 'PAGE', [screen1, screen2]);

  const runtime = setup([assetsPage, screensPage], [screen1, screen2]);
  runtime.figma.currentPage = screensPage;
  screensPage.selection = [screen1, screen2];

  const result = await runtime.run('export-selection');
  // Only Play and Search should be extracted (UnusedIcon is skipped, Divider is skipped)
  assert.equal(result.assetExports.length, 2);
  const filenames = result.assetExports.map(a => a.filename);
  assert.ok(filenames.some(f => f.startsWith('play')));
  assert.ok(filenames.some(f => f.startsWith('search')));
  assert.ok(!filenames.some(f => f.startsWith('unusedicon')));
  assert.ok(!filenames.some(f => f.startsWith('divider')));
});

test('dynamic-page mode resolves instances asynchronously without calling mainComponent getter', async () => {
  const masterIcon = node('ic_tv_guide', 'COMPONENT', []);
  const assetsPage = node('Design System', 'PAGE', [masterIcon]);

  let asyncCalls = 0;
  const instance = node('TV Guide Item', 'INSTANCE', [], {
    async getMainComponentAsync() {
      asyncCalls++;
      return masterIcon;
    }
  });

  const screen = node('LiveTV', 'FRAME', [instance]);
  const screensPage = node('Screens', 'PAGE', [screen]);

  const runtime = setup([assetsPage, screensPage], [screen]);
  runtime.figma.currentPage = screensPage;
  screensPage.selection = [screen];

  const result = await runtime.run('export-selection');
  assert.equal(result.meta.failedCount, 0);
  assert.equal(result.assetExports.length, 1);
  assert.match(result.assetExports[0].filename, /^ic_tv_guide/);
  assert.ok(asyncCalls > 0, 'getMainComponentAsync should be called');
});

test('UI names downloaded bundle after document title with spaces and invalid chars sanitized', async () => {
  const icon = node('ic_logo', 'COMPONENT', []);
  const docTitle = 'INV166 LiveTV 7 - Trinity (Copy)';
  const runtime = setup([node('Page', 'PAGE', [icon])], [icon]);
  runtime.figma.root.name = docTitle;

  const result = await runtime.run('export-selected-assets');
  assert.equal(result.meta.fileName, docTitle);

  const ui = setupUI();
  ui.elements.btnSel.onclick();
  ui.elements.btnExport.onclick();
  runtime.messages.forEach(ui.message);

  assert.equal(ui.elements.btnAssets.disabled, false);
  assert.equal(ui.elements.bundleFilename.textContent, 'INV166_LiveTV_7_Trinity_Copy.zip');

  ui.elements.btnAssets.onclick();
  assert.equal(ui.downloadNames.length, 1);
  assert.equal(ui.downloadNames[0], 'INV166_LiveTV_7_Trinity_Copy.zip');
});
