'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

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

function buildHarness({ speech = true, response } = {}) {
  const ids = [
    'ack', 'alertVoice', 'brief', 'demo', 'feed', 'form', 'historySummary',
    'connectReport', 'connectionStatus', 'metadata', 'metadataDetail', 'mic', 'micStatus', 'mode',
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
    fetch: async () => response || { ok: true, status: 200, json: async () => validView() }
  });
  vm.runInContext(fs.readFileSync('app.js', 'utf8'), context, { filename: 'app.js' });
  vm.runInContext(fs.readFileSync('report-link.js', 'utf8'), context, { filename: 'report-link.js' });
  return { context, elements, utterances, speechSynthesis, document };
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
  const loadingRequest = loading.elements.connectReport.listeners.click();
  assert.equal(loading.elements.connectReport.disabled, true);
  assert.match(loading.elements.connectionStatus.textContent, /Loading/);
  releaseLoading({ ok: true, status: 200, json: async () => validView() });
  await loadingRequest;
  assert.equal(loading.elements.connectReport.disabled, false);

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
  await failed.elements.connectReport.listeners.click();
  assert.match(failed.elements.connectionStatus.textContent, /no fallback data/);

  elements.motion.checked = false;
  elements.motion.onchange();
  assert.equal(document.body.dataset.motion, 'off');

  const unsupported = buildHarness({ speech: false });
  assert.equal(unsupported.elements.testVoice.disabled, true);
  assert.match(unsupported.elements.voiceStatus.textContent, /unavailable/);

  const html = fs.readFileSync('index.html', 'utf8');
  const css = `${fs.readFileSync('2036.css', 'utf8')}\n${fs.readFileSync('motion.css', 'utf8')}`;
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
