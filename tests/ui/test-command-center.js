'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const ASSET_ROOT = path.join(__dirname, '../../src/orion/presentation/static');

class ClassList {
  constructor(owner) { this.owner = owner; }
  toggle(name, enabled) {
    const names = new Set(this.owner.className.split(/\s+/).filter(Boolean));
    if (enabled) names.add(name); else names.delete(name);
    this.owner.className = [...names].join(' ');
  }
}

class Element {
  constructor(tag = 'div') {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.className = '';
    this.classList = new ClassList(this);
    this.dataset = {};
    this.textContent = '';
    this.value = '';
    this.checked = false;
    this.disabled = false;
    this.hidden = false;
    this.files = [];
    this.listeners = {};
  }
  append(...items) {
    for (const item of items) {
      if (item && typeof item === 'object') item.parentNode = this;
      this.children.push(item);
    }
  }
  prepend(item) {
    if (item && typeof item === 'object') item.parentNode = this;
    this.children.unshift(item);
  }
  replaceChildren(...items) {
    this.children = [];
    this.append(...items);
  }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  querySelector(selector) {
    if (selector === '.event') return this.children.find(item => item.className === 'event') || null;
    return null;
  }
  remove() {
    if (!this.parentNode) return;
    const index = this.parentNode.children.indexOf(this);
    if (index >= 0) this.parentNode.children.splice(index, 1);
  }
  get firstChild() { return this.children[0]; }
  get lastChild() { return this.children[this.children.length - 1]; }
  get options() { return this.children; }
  get scrollHeight() { return this.children.length; }
  set scrollTop(_value) {}
}

function buildHarness({ speech = true, response, statusResponse } = {}) {
  const ids = [
    'ack', 'alertVoice', 'brief', 'demo', 'feed', 'form', 'historySummary',
    'procurementPanel', 'procurementSummary', 'procurementEvidence', 'procurementLimits',
    'connectReport', 'connectionStatus', 'reportToken', 'metadata', 'metadataDetail', 'mic', 'micStatus', 'mode',
    'motion', 'observations', 'orb', 'query', 'quiet', 'reportStatus',
    'severity', 'source', 'speak', 'stop', 'studies', 'studyDetail', 'summary',
    'testAlert', 'testVoice', 'toast', 'unread', 'voices', 'voiceStatus', 'messages'
  ];
  const elements = Object.fromEntries(ids.map(id => [id, new Element()]));
  elements.severity.value = 'important';
  elements.motion.checked = true;
  elements.toast.hidden = true;
  elements.feed.append(new Element('p'));
  const chips = ['What are you studying?', 'Show me the evidence', 'What needs my attention?']
    .map(query => {
      const button = new Element('button');
      button.dataset.query = query;
      return button;
    });
  const document = {
    body: new Element('body'),
    getElementById: id => elements[id],
    createElement: tag => new Element(tag),
    createTextNode: text => ({ textContent: text }),
    querySelectorAll: selector => selector === '[data-query]' ? chips : []
  };
  const utterances = [];
  const voiceListeners = {};
  const speechSynthesis = {
    paused: false,
    cancelCount: 0,
    getVoices: () => [{ name: 'Fixture Voice', lang: 'en-US', voiceURI: 'fixture' }],
    speak: utterance => { utterances.push(utterance); },
    cancel() { this.cancelCount += 1; },
    resume() { this.paused = false; },
    addEventListener(name, callback) { voiceListeners[name] = callback; }
  };
  class SpeechSynthesisUtterance {
    constructor(text) { this.text = text; }
  }
  const pageListeners = {};
  const window = {
    addEventListener(name, callback) { pageListeners[name] = callback; }
  };
  if (speech) {
    window.speechSynthesis = speechSynthesis;
    window.SpeechSynthesisUtterance = SpeechSynthesisUtterance;
  }
  let nextTimer = 1;
  const requests = [];
  const context = vm.createContext({
    console,
    Date,
    document,
    window,
    Option: function Option(text, value) {
      const option = new Element('option');
      option.textContent = text;
      option.value = value;
      return option;
    },
    setTimeout: () => nextTimer++,
    clearTimeout: () => {},
    setInterval: () => nextTimer++,
    clearInterval: () => {},
    fetch: async (path, options) => {
      requests.push({ path, options });
      return path === '/api/v1/status'
        ? statusResponse || { ok: false, status: 503 }
        : response || { ok: true, status: 200, json: async () => validView() };
    }
  });
  vm.runInContext(fs.readFileSync(path.join(ASSET_ROOT, 'app.js'), 'utf8'), context, { filename: 'app.js' });
  vm.runInContext(fs.readFileSync(path.join(ASSET_ROOT, 'report-link.js'), 'utf8'), context, { filename: 'report-link.js' });
  return { context, elements, utterances, speechSynthesis, document, requests };
}

function validView() {
  return {
    authority: {
      erp_writes: 0,
      execution_allowed: false,
      promotion_allowed: false,
      recommendation_allowed: false
    },
    cycles: { attempted: 99, completed: 99 },
    failures: [],
    interface_version: 'orion.readonly-report.v1',
    metadata: { budget: 7, used: 7 },
    observations_persisted: 303,
    report_kind: 'completed_aggregate',
    session_ended_at: '2026-09-06T14:50:51.027940+00:00',
    session_started_at: '2026-09-06T14:49:17.854170+00:00',
    stop_reason: 'cycle_limit',
    studies: { budget: 99, used: 99 }
  };
}

(async () => {
  const harness = buildHarness();
  const { context, elements, utterances, document } = harness;
  assert.match(elements.voiceStatus.textContent, /1 device voice available/);
  assert.equal(elements.testVoice.disabled, false);

  elements.testVoice.onclick();
  assert.match(elements.voiceStatus.textContent, /queued/);
  assert.equal(elements.orb.dataset.state, 'idle', 'queued speech must not appear to be speaking');
  const firstUtterance = utterances.at(-1);
  firstUtterance.onstart();
  assert.equal(elements.orb.dataset.state, 'speaking');
  firstUtterance.onend();
  assert.equal(elements.orb.dataset.state, 'idle');
  assert.match(elements.voiceStatus.textContent, /finished/);

  elements.testVoice.onclick();
  utterances.at(-1).onerror({ error: 'not-allowed' });
  assert.match(elements.voiceStatus.textContent, /browser blocked/i);
  assert.equal(elements.orb.dataset.state, 'idle');

  elements.alertVoice.checked = true;
  elements.quiet.checked = true;
  elements.quiet.onchange();
  const spokenBeforeQuietAlert = utterances.length;
  elements.testAlert.onclick();
  assert.equal(utterances.length, spokenBeforeQuietAlert);
  assert.equal(elements.toast.hidden, true);
  assert.equal(elements.unread.textContent, '1 new');
  elements.ack.onclick();
  assert.equal(elements.unread.textContent, '0 new');

  elements.quiet.checked = false;
  elements.severity.value = 'urgent';
  elements.toast.hidden = true;
  elements.testAlert.onclick();
  assert.equal(elements.toast.hidden, true, 'important alert must stay below urgent threshold');

  const feedCount = elements.feed.children.length;
  let releaseLoading;
  const loadingResponse = new Promise(resolve => { releaseLoading = resolve; });
  const loading = buildHarness({ response: loadingResponse });
  await loading.elements.connectReport.listeners.click();
  assert.match(loading.elements.connectionStatus.textContent, /access token/);
  loading.elements.reportToken.value = 'fixture-token';
  const loadingRequest = loading.elements.connectReport.listeners.click();
  assert.equal(loading.elements.connectReport.disabled, true);
  assert.match(loading.elements.connectionStatus.textContent, /loading/);
  releaseLoading({ ok: true, status: 200, json: async () => validView() });
  await loadingRequest;
  assert.equal(loading.elements.connectReport.disabled, false);
  assert.equal(loading.elements.reportToken.value, '');
  assert.equal(loading.requests.at(-1).options.headers.Authorization, 'Bearer fixture-token');

  context.window.ORIONPreview.applyReportView({
    ...validView(),
    cycles: { attempted: 0, completed: 0 },
    metadata: { budget: 7, used: 0 },
    observations_persisted: 0,
    stop_reason: 'no_candidate',
    studies: { budget: 99, used: 0 }
  });
  assert.equal(elements.observations.textContent, '0');
  assert.match(elements.reportStatus.textContent, /no candidate/);

  elements.reportToken.value = 'fixture-token';
  await elements.connectReport.listeners.click();
  assert.equal(elements.metadata.textContent, '7 / 7');
  assert.equal(elements.studies.textContent, '99 / 99');
  assert.equal(elements.observations.textContent, '303');
  assert.match(elements.reportStatus.textContent, /99 cycles/);
  assert.match(elements.historySummary.textContent, /Completed aggregate/);
  assert.match(elements.connectionStatus.textContent, /Connected/);
  assert.equal(elements.feed.children.length, feedCount, 'historical view must not create a simulated event');

  assert.throws(
    () => context.window.ORIONPreview.applyReportView({ ...validView(), authority: { erp_writes: 1 } }),
    /invalid safe view/
  );
  assert.equal(elements.observations.textContent, '303', 'invalid view must preserve prior result');

  const failed = buildHarness({ response: { ok: false, status: 503 } });
  failed.elements.reportToken.value = 'fixture-token';
  await failed.elements.connectReport.listeners.click();
  assert.match(failed.elements.connectionStatus.textContent, /disconnected/);
  assert.equal(failed.elements.observations.textContent, '—');

  const denied = buildHarness({ response: { ok: false, status: 401 } });
  denied.elements.reportToken.value = 'fixture-token';
  await denied.elements.connectReport.listeners.click();
  assert.match(denied.elements.connectionStatus.textContent, /Access denied/);
  assert.equal(denied.elements.observations.textContent, '—');

  const lineageView = {
    ...validView(),
    interface_version: 'orion.readonly-report.v2',
    report_kind: 'synthetic_lineage',
    observations_persisted: 2,
    lineage: {
      run_id: 'bb8f1c20-2735-4e9b-a2c9-a49f980a8010',
      dataset_id: 'dataset-267-alpha',
      source_sha256: 'a'.repeat(64),
      authorization_id: 'synthetic-read-267',
      evidence_ids: [
        '6ba7b810-713f-4fc2-b38f-539b18fbdb77',
        '1ec7e4e1-aa60-4c43-8759-345b995044ed'
      ],
      investigation_sha256: 'b'.repeat(64),
      witness_sha256: 'c'.repeat(64)
    }
  };
  const lineageResponse = { ok: true, status: 200, json: async () => lineageView };
  const lineageBrowser = buildHarness({ response: lineageResponse });
  lineageBrowser.elements.reportToken.value = 'synthetic-token';
  await lineageBrowser.elements.connectReport.listeners.click();
  assert.equal(lineageBrowser.elements.observations.textContent, '2');
  assert.match(lineageBrowser.elements.historySummary.textContent, /dataset-267-alpha/);
  assert.match(lineageBrowser.elements.historySummary.textContent, /bb8f1c20/);
  lineageResponse.ok = false;
  lineageResponse.status = 401;
  lineageBrowser.elements.reportToken.value = 'forged-token';
  await lineageBrowser.elements.connectReport.listeners.click();
  assert.equal(lineageBrowser.elements.observations.textContent, '—');
  assert.equal(lineageBrowser.elements.historySummary.textContent, 'No bound historical report loaded.');

  const procurementView = {
    ...lineageView,
    interface_version: 'orion.readonly-report.v3',
    report_kind: 'procurement_price_leakage',
    procurement: {
      status: 'candidate_review', item: 'part-42', target_ref: 'holdout_a-T',
      estimated_exposure_usd: '60.00', reviewer_decision: 'pending',
      source_system: 'synthetic-records', finding_sha256: 'd'.repeat(64),
      connector_identity: 'originorion-connector-synthetic@0.2.0rc2',
      source_id: 'synthetic-source-a',
      comparison_refs: ['holdout_a-H1', 'holdout_a-H2'], target_unit_usd: '17.00',
      normalized_unit: 'each', confidence: 'limited_synthetic', missing_evidence: [],
      alternative_explanations: ['market change needs review'], next_question: 'Review market',
      independently_verified_savings_usd: null, company: 'Synthetic Procurement Co',
      evidence_ids: lineageView.lineage.evidence_ids, evidence_acquired_at: '2026-03-01T00:00:00Z',
      base_report_sha256: 'e'.repeat(64), quantity_each: '10', reference_unit_usd: '11.00',
      authorization_status: 'read_only_admitted', excluded_evidence: [], invalidation_history: []
    }
  };
  const procurementResponse = { ok: true, status: 200, json: async () => procurementView };
  const procurementBrowser = buildHarness({ response: procurementResponse });
  procurementBrowser.elements.reportToken.value = 'synthetic-token';
  await procurementBrowser.elements.connectReport.listeners.click();
  assert.equal(procurementBrowser.elements.procurementPanel.hidden, false);
  assert.match(procurementBrowser.elements.procurementSummary.textContent, /60.00/);
  assert.match(procurementBrowser.elements.procurementEvidence.textContent, /holdout_a-H1/);
  procurementResponse.ok = false;
  procurementResponse.status = 401;
  procurementBrowser.elements.reportToken.value = 'forged-token';
  await procurementBrowser.elements.connectReport.listeners.click();
  assert.equal(procurementBrowser.elements.procurementPanel.hidden, true);
  assert.equal(procurementBrowser.elements.procurementSummary.textContent, '');

  const stageA = buildHarness({
    statusResponse: {
      ok: true,
      status: 200,
      json: async () => ({
        interface_version: 'orion.deployment-status.v1',
        http_api: 'healthy',
        report_service: 'not_connected',
        governed_worker: 'not_connected',
        persistent_storage: 'not_connected',
        live_pilot_ready: false,
        execution_allowed: false
      })
    }
  });
  await stageA.context.window.ORIONReportLink.checkBackend();
  assert.match(stageA.elements.source.textContent, /Interface API connected/);
  assert.match(stageA.elements.source.textContent, /governed services disconnected/);
  const unsafeStatus = buildHarness({
    statusResponse: {
      ok: true, status: 200, json: async () => ({
        interface_version: 'orion.deployment-status.v1',
        http_api: 'healthy',
        report_service: 'not_connected',
        governed_worker: 'not_connected',
        persistent_storage: 'not_connected',
        live_pilot_ready: false,
        execution_allowed: true
      })
    }
  });
  await unsafeStatus.context.window.ORIONReportLink.checkBackend();
  assert.match(unsafeStatus.elements.source.textContent, /unavailable/);

  elements.motion.checked = false;
  elements.motion.onchange();
  assert.equal(document.body.dataset.motion, 'off');

  const unsupported = buildHarness({ speech: false });
  assert.equal(unsupported.elements.testVoice.disabled, true);
  assert.match(unsupported.elements.voiceStatus.textContent, /unavailable/);

  const html = fs.readFileSync(path.join(ASSET_ROOT, 'index.html'), 'utf8');
  const css = `${fs.readFileSync(path.join(ASSET_ROOT, '2036.css'), 'utf8')}\n${fs.readFileSync(path.join(ASSET_ROOT, 'motion.css'), 'utf8')}`;
  assert.match(html, /id="testVoice"/);
  assert.match(html, /id="connectReport"/);
  assert.match(html, /Disconnected\. No report loaded\./);
  assert.match(html, /report-link\.js/);
  assert.match(html, /id="voiceStatus"[^>]*role="status"/);
  assert.match(html, /type="button"[^>]*data-query/);
  assert.match(css, /prefers-reduced-motion:reduce/);
  assert.match(css, /body\[data-motion="off"\]/);
  assert.match(css, /@media\(max-width:560px\)/);
  assert.match(css, /@media\(max-width:1100px\)/);
  console.log('ORION command-center interaction checks passed');
})().catch(error => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
