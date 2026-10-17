const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'src', 'camero', 'web', 'static', 'app.js'), 'utf8');

function extract(start, end) {
  return source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
}

function navigationContext() {
  const context = vm.createContext({
    location: { hash: '#recordings' },
    state: {
      recordingsFilter: {}, semanticSearch: {}, search: '', offset: 0,
      selected: new Set(), scan: {},
    },
    setActiveNav() {},
    setSettingsBusy() {},
    renderRecordings() {},
    renderStreamers() {},
    renderSites() {},
    renderTags() {},
    renderSettings() {},
    renderStreamerDetail() {},
    renderSite() {},
    renderTag() {},
  });
  vm.runInContext(extract('function createDefaultRecordingsFilter()', 'function getPreferredTheme()'), context);
  vm.runInContext(extract('function route()', 'async function renderSettings()'), context);
  return context;
}

test('filters and semantic results reset on navigation, including detail views', async () => {
  const ctx = navigationContext();
  await ctx.render();
  for (const hash of ['#streamers', '#sites', '#tags', '#streamer:Alice', '#site:Example', '#tag:Example', '#recordings']) {
    ctx.state.recordingsFilter.nameContains = 'example';
    ctx.state.recordingsFilter.videoCodecs = ['h264'];
    ctx.state.semanticSearch = { query: 'example', recordingIds: [1] };
    ctx.state.search = 'example';
    ctx.state.offset = 80;
    ctx.location.hash = hash;
    await ctx.render();
    assert.equal(ctx.state.recordingsFilter.nameContains, '');
    assert.equal(ctx.state.recordingsFilter.videoCodecs.length, 0);
    assert.equal(ctx.state.semanticSearch.recordingIds, null);
    assert.equal(ctx.state.search, '');
    assert.equal(ctx.state.offset, 0);
  }
});

test('rerendering the same view preserves current filters', async () => {
  const ctx = navigationContext();
  await ctx.render();
  ctx.state.recordingsFilter.nameContains = 'example';
  await ctx.render();
  assert.equal(ctx.state.recordingsFilter.nameContains, 'example');
});

test('clicking Recordings clears semantic results even on the same route', async () => {
  const ctx = navigationContext();
  let handler;
  let renders = 0;
  ctx.document = { querySelector: () => ({ addEventListener: (_, fn) => { handler = fn; } }) };
  ctx.renderAndRestore = async () => { renders++; };
  vm.runInContext(extract('document.querySelector(\'.nav [data-route="recordings"]\')', "window.addEventListener('hashchange'"), ctx);
  ctx.state.semanticSearch = { query: 'example', recordingIds: [1] };
  let prevented = false;
  handler({ preventDefault() { prevented = true; } });
  assert.equal(ctx.state.semanticSearch.recordingIds, null);
  assert.equal(ctx.state.semanticSearch.query, '');
  assert.equal(prevented, true);
  assert.equal(renders, 1);
});

test('recording filters are never read from or written to storage', () => {
  assert.doesNotMatch(source, /(?:localStorage|sessionStorage)\.[^(]+\(['"]camero\.recordingsFilter/);
  assert.match(source, /recordingsFilter: createDefaultRecordingsFilter\(\)/);
});

test('auto-tagging settings belong exclusively to the AI tab', () => {
  const settings = extract('async function renderSettings()', '  function initSettingsTabs()');
  const catalog = settings.slice(settings.indexOf('<section id="settingsPanelCatalog"'), settings.indexOf('<section id="settingsPanelAi"'));
  const ai = settings.slice(settings.indexOf('<section id="settingsPanelAi"'), settings.indexOf('<section id="settingsPanelNetwork"'));
  for (const id of ['autoTagAfterScan', 'autoTagConfidence', 'autoTagSceneThreshold', 'autoTagFrameInterval']) {
    assert.equal(settings.split(`id="${id}"`).length - 1, 1);
    assert.ok(ai.includes(`id="${id}"`), `${id} should be in AI`);
    assert.ok(!catalog.includes(`id="${id}"`), `${id} should not be in Catalog`);
  }
  assert.ok(catalog.includes('id="periodicScanEnabled"'));
  assert.ok(ai.includes('id="aiModelsDir"'));
  assert.ok(ai.includes('id="semanticSearchMinSimilarity"'));
});

test('minimum similarity panel is explicitly labeled AI Magic Search', () => {
  const inputPosition = source.indexOf('id="semanticSearchMinSimilarity"');
  const panelStart = source.lastIndexOf('<div class="panel settings-section-panel">', inputPosition);
  const panelHeader = source.slice(panelStart, inputPosition);
  assert.ok(panelStart !== -1);
  assert.match(panelHeader, />AI Magic Search<\/div>/);
  const aiStart = source.indexOf('<section id="settingsPanelAi"');
  assert.equal(panelStart, source.indexOf('<div class="panel settings-section-panel">', aiStart));
});

test('similar search sends recording ID instead of text and preserves relevance order', async () => {
  let variables;
  let loads = 0;
  const scrolls = [];
  const ctx = vm.createContext({
    state: { semanticSearch: { query: 'old query', recordingIds: null }, offset: 80 },
    semanticSearchInput: { value: 'old query', focus() {} },
    semanticSearchSubmit: { disabled: false },
    semanticSearchPanel: { hidden: true, isConnected: true },
    semanticSearchBtn: { setAttribute() {} },
    semanticSearchClear: { hidden: true },
    config: { semanticSearchMinSimilarity: 0.42 },
    setSemanticSearchStatus() {},
    showToast(message) { assert.fail(message); },
    async load() { loads++; },
    window: {
      scrollTo(options) {
        assert.equal(loads, 1, 'Scroll only after results have loaded');
        scrolls.push(options);
      },
    },
    async gql(query, vars) {
      variables = vars;
      assert.match(query, /recordingId:\$recordingId/);
      return { semanticSearch: [{ recordingId: 9 }, { recordingId: 2 }] };
    },
  });
  vm.runInContext(extract('  let semanticSearchRequest = 0;', '  if (semanticSearchBtn && semanticSearchPanel)'), ctx);
  await ctx.runSemanticSearch(7, 'Source video');
  assert.equal(variables.query, '');
  assert.equal(variables.recordingId, 7);
  assert.equal(variables.threshold, 0.42);
  assert.deepEqual(Array.from(ctx.state.semanticSearch.recordingIds), [9, 2]);
  assert.equal(ctx.state.offset, 0);
  assert.equal(ctx.semanticSearchInput.value, '');
  assert.equal(ctx.semanticSearchPanel.hidden, false);
  assert.equal(ctx.semanticSearchClear.hidden, false);
  assert.equal(loads, 1);
  assert.equal(scrolls.length, 1);
  assert.equal(scrolls[0].top, 0);
  assert.equal(scrolls[0].behavior, 'instant');
  await ctx.runSemanticSearch();
  // Empty text does not accidentally reuse a source recording.
  assert.equal(loads, 1);
  ctx.semanticSearchInput.value = 'description';
  await ctx.runSemanticSearch();
  assert.equal(loads, 2);
  assert.equal(scrolls.length, 1, 'Text searches preserve the scroll position');
});

test('similar search card action depends on semantic index membership', () => {
  assert.match(source, /contactSheetUrl semanticIndexed streamer/);
  assert.match(source, /data-find-similar="\$\{r\.id\}"/);
  assert.ok(source.includes("${r.semanticIndexed ? '' : 'disabled'}"));
  assert.match(source, /semanticSearchSubmit\.onclick = \(\) => runSemanticSearch\(\)/);
});

test('similar action is rendered on recording cards regardless of view', () => {
  const actions = extract('              <button class="icon-btn" data-open-folder=', '    // Card click handlers');
  assert.match(actions, /data-find-similar/);
  assert.doesNotMatch(actions, /isSemanticSearchView/);
});

test('detail-view similar action opens a global search after navigation resets filters', async () => {
  for (const hash of ['#streamer:Alice', '#site:Example', '#tag:Example']) {
    const ctx = navigationContext();
    ctx.location.hash = hash;
    await ctx.render();
    let handler;
    ctx.gridEl = { querySelectorAll: () => [{
      disabled: false,
      dataset: { findSimilar: '7', sourceTitle: 'Source video' },
      addEventListener(_, fn) { handler = fn; },
    }] };
    ctx.isSemanticSearchView = false;
    ctx.runSemanticSearch = () => assert.fail('Must search from Recordings, not the detail view');
    vm.runInContext(extract("    gridEl.querySelectorAll('button[data-find-similar]')", '    // Assign tags from card'), ctx);
    await handler({ stopPropagation() {}, preventDefault() {} });
    assert.equal(ctx.location.hash, '#recordings');
    ctx.state.recordingsFilter.nameContains = 'old filter';
    ctx.state.semanticSearch.recordingIds = [99];
    let options;
    ctx.renderRecordings = async value => { options = value; };
    await ctx.render();
    assert.equal(options.similarSource.recordingId, 7);
    assert.equal(options.similarSource.title, 'Source video');
    assert.equal(ctx.state.recordingsFilter.nameContains, '');
    assert.equal(ctx.state.semanticSearch.recordingIds, null);
    await ctx.render();
    assert.equal(options.similarSource, null, 'Source should be consumed only once');
  }
});

test('Recordings executes the transferred similar search after loading', async () => {
  const calls = [];
  const ctx = vm.createContext({
    options: { similarSource: { recordingId: 7, title: 'Source video' } },
    async load() { calls.push('load'); },
    async runSemanticSearch(id, title) { calls.push([id, title]); },
    showToast(message) { assert.fail(message); },
  });
  const end = source.indexOf('async function openRecording(id)');
  const start = source.lastIndexOf('  await load().catch(e => showToast(e.message));', end);
  const ending = source.slice(start, end);
  await vm.runInContext(`(async () => {${ending.slice(0, ending.lastIndexOf('}'))}})()`, ctx);
  assert.deepEqual(calls, ['load', [7, 'Source video']]);
});
