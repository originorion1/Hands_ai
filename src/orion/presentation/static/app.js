'use strict';

const $ = id => document.getElementById(id);
const rank = { routine: 0, important: 1, urgent: 2 };

let report = null;
let source = 'no bound report';
let unseen = 0;
let timer = null;
let demoStep = 0;
let recognition = null;
let listening = false;
let toastTimer;
let speaking = false;
let speechGeneration = 0;
let speechStartTimer;

function message(text, user = false) {
  const node = document.createElement('div');
  node.className = `message${user ? ' user' : ''}`;
  const who = document.createElement('small');
  who.textContent = user ? 'YOU' : 'ORION';
  node.append(who, document.createTextNode(text));
  $('messages').append(node);
  while ($('messages').children.length > 40) $('messages').firstChild.remove();
  $('messages').scrollTop = $('messages').scrollHeight;
}

function core() {
  const state = listening ? 'listening' : speaking ? 'speaking' : timer ? 'demo' : 'idle';
  $('orb').dataset.state = state;
  $('orb').classList.toggle('active', state !== 'idle');
  $('mode').textContent = {
    listening: 'Listening · microphone active',
    speaking: 'Speaking · voice playback started',
    demo: 'Demonstration · simulated study',
    idle: 'Standing by · runner disconnected'
  }[state];
}

function voiceSupported() {
  return 'speechSynthesis' in window && 'SpeechSynthesisUtterance' in window;
}

function voiceStatus(text) {
  $('voiceStatus').textContent = text;
}

function cancelVoice(statusText = '') {
  speechGeneration += 1;
  clearTimeout(speechStartTimer);
  speaking = false;
  if (voiceSupported()) window.speechSynthesis.cancel();
  if (statusText) voiceStatus(statusText);
  core();
}

function speechErrorText(error) {
  if (error === 'not-allowed') {
    return 'The browser blocked voice playback. Click Test voice and allow site audio if prompted.';
  }
  if (error === 'voice-unavailable' || error === 'language-unavailable') {
    return 'The selected device voice is unavailable. Choose System default and test again.';
  }
  if (error === 'audio-busy' || error === 'audio-hardware') {
    return 'The browser could not access audio output. Check the active output device and try again.';
  }
  return 'Voice playback failed. Text remains available; check browser audio permissions and output volume.';
}

function speak(text, { test = false } = {}) {
  if (!voiceSupported()) {
    voiceStatus('Spoken output is unavailable in this browser. Text remains available.');
    return false;
  }
  cancelVoice();
  const generation = speechGeneration;
  try {
    const utterance = new window.SpeechSynthesisUtterance(text);
    const selected = window.speechSynthesis
      .getVoices()
      .find(voice => voice.voiceURI === $('voices').value);
    if (selected) utterance.voice = selected;
    utterance.rate = 0.96;
    utterance.onstart = () => {
      if (generation !== speechGeneration) return;
      clearTimeout(speechStartTimer);
      speaking = true;
      voiceStatus(test ? 'Test voice playback started.' : 'Voice playback started.');
      core();
    };
    utterance.onend = () => {
      if (generation !== speechGeneration) return;
      clearTimeout(speechStartTimer);
      speaking = false;
      voiceStatus(test ? 'Test voice playback finished.' : 'Voice playback finished.');
      core();
    };
    utterance.onerror = eventObject => {
      if (generation !== speechGeneration) return;
      clearTimeout(speechStartTimer);
      speaking = false;
      voiceStatus(speechErrorText(eventObject && eventObject.error));
      core();
    };
    voiceStatus('Voice request queued; waiting for playback to begin…');
    if (window.speechSynthesis.paused) window.speechSynthesis.resume();
    window.speechSynthesis.speak(utterance);
    speechStartTimer = setTimeout(() => {
      if (generation === speechGeneration && !speaking) {
        voiceStatus('Voice was queued but playback did not begin. Check site audio permission, output volume, and the selected device voice.');
      }
    }, 2000);
    return true;
  } catch {
    voiceStatus('Voice could not be queued. Text remains available; try System default or another browser.');
    return false;
  }
}

function refreshVoices() {
  const select = $('voices');
  if (!voiceSupported()) {
    select.disabled = true;
    $('testVoice').disabled = true;
    voiceStatus('Speech synthesis is unavailable in this browser.');
    return;
  }
  const old = select.value;
  const available = window.speechSynthesis.getVoices();
  select.replaceChildren(new Option('System default', ''));
  available.forEach(voice => {
    select.append(new Option(`${voice.name} · ${voice.lang}`, voice.voiceURI));
  });
  if ([...select.options].some(option => option.value === old)) select.value = old;
  select.disabled = false;
  $('testVoice').disabled = false;
  voiceStatus(
    available.length
      ? `${available.length} device voice${available.length === 1 ? '' : 's'} available. Use Test voice to confirm audible playback.`
      : 'Speech synthesis is available; the device voice list is still loading. Test voice will use the system default.'
  );
}

function event(title, detail, severity = 'routine') {
  const feed = $('feed');
  if (!feed.querySelector('.event')) feed.replaceChildren();
  const node = document.createElement('div');
  node.className = 'event';
  const time = document.createElement('time');
  time.textContent = `${new Date().toLocaleTimeString()} · ${severity.toUpperCase()}`;
  const name = document.createElement('strong');
  name.textContent = title;
  const note = document.createElement('small');
  note.textContent = detail;
  node.append(time, document.createElement('br'), name, note);
  feed.prepend(node);
  while (feed.children.length > 30) feed.lastChild.remove();
  unseen += 1;
  $('unread').textContent = `${unseen} new`;
  if (rank[severity] >= rank[$('severity').value] && !$('quiet').checked) {
    $('toast').textContent = `${title} — ${detail}`;
    $('toast').hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { $('toast').hidden = true; }, 6000);
    if ($('alertVoice').checked) speak(`${title}. ${detail}`);
  }
}

function formatStopReason(value) {
  return value.replaceAll('_', ' ');
}

function brief() {
  if (!report) {
    return 'No report is connected. This interface cannot infer or invent a result.';
  }
  return `From the ${source}: the completed session used ${report.metadataUsed} of ${report.metadataBudget} metadata requests and ${report.studyUsed} of ${report.studyBudget} study requests, persisting ${report.observations} observations. It stopped at ${formatStopReason(report.stopReason)} after ${report.cyclesCompleted} completed cycles. These counters establish collection, not business insight or execution authority.`;
}

function ask(query) {
  const q = query.trim();
  if (!q) return;
  message(q, true);
  let reply;
  const lower = q.toLowerCase();
  if (/pause|stop learning/.test(lower)) {
    stopDemo();
    reply = 'The local demonstration is paused. No command was sent to an ERP runner; this page is not connected.';
  } else if (/evidence|source/.test(lower)) {
    reply = `${brief()} Only sanitized aggregate counters are loaded; records, customer values, and selected field identities are absent.`;
  } else if (/studying|current study/.test(lower)) {
    reply = 'No live study is running. A connected report is a completed aggregate and does not retain selected record values.';
  } else if (/attention|next/.test(lower)) {
    reply = report
      ? 'The useful next step is human review of the persisted evidence. This interface has no execution endpoint.'
      : 'Connect the locally bound report. No action can be inferred without evidence.';
  } else if (/brief|status|update/.test(lower)) {
    reply = brief();
  } else {
    reply = 'This local preview supports briefings, evidence, current-study status, attention, and pause-demo commands. General reasoning needs an ORION backend; I will not invent a finding.';
  }
  message(reply);
  if ($('speak').checked) speak(reply);
}

function stopDemo() {
  clearInterval(timer);
  timer = null;
  $('demo').textContent = 'Run event demo';
  core();
}

function renderReport() {
  if (!report) return;
  $('metadata').textContent = `${report.metadataUsed} / ${report.metadataBudget}`;
  $('metadataDetail').textContent = 'Completed aggregate';
  $('studies').textContent = `${report.studyUsed} / ${report.studyBudget}`;
  $('studyDetail').textContent = 'Completed aggregate';
  $('observations').textContent = String(report.observations);
  $('reportStatus').textContent = `${report.cyclesCompleted} cycles · ${formatStopReason(report.stopReason)}`;
  $('historySummary').textContent = `Completed aggregate: ${report.cyclesCompleted} cycles and ${report.observations} persisted observations. No record values or live events loaded.`;
}

function applyReportView(value) {
  const counter = item => Number.isSafeInteger(item) && item >= 0 && item <= 1000000;
  if (
    !value || value.interface_version !== 'orion.readonly-report.v1' ||
    value.report_kind !== 'completed_aggregate' ||
    !value.metadata || !counter(value.metadata.used) || !counter(value.metadata.budget) ||
    !value.studies || !counter(value.studies.used) || !counter(value.studies.budget) ||
    !value.cycles || !counter(value.cycles.completed) ||
    !counter(value.observations_persisted) || typeof value.stop_reason !== 'string' ||
    !value.authority || value.authority.erp_writes !== 0 ||
    value.authority.execution_allowed !== false ||
    value.authority.promotion_allowed !== false ||
    value.authority.recommendation_allowed !== false
  ) {
    throw Error('The report service returned an invalid safe view.');
  }
  report = {
    metadataUsed: value.metadata.used,
    metadataBudget: value.metadata.budget,
    studyUsed: value.studies.used,
    studyBudget: value.studies.budget,
    observations: value.observations_persisted,
    cyclesCompleted: value.cycles.completed,
    stopReason: value.stop_reason
  };
  source = 'locally bound aggregate report';
  renderReport();
  $('summary').textContent = 'A completed aggregate report is connected read-only. Ask for a briefing to review its counters.';
}

$('form').addEventListener('submit', eventObject => {
  eventObject.preventDefault();
  ask($('query').value);
  $('query').value = '';
});
document.querySelectorAll('[data-query]').forEach(button => {
  button.addEventListener('click', () => ask(button.dataset.query));
});
$('brief').onclick = () => ask('Brief me');
$('demo').onclick = () => {
  if (timer) {
    stopDemo();
    event('DEMO · Paused', 'Local animation and simulated events stopped.');
    return;
  }
  demoStep = 0;
  event('DEMO · Session started', 'Simulated activity only. No ERP request.', 'routine');
  $('demo').textContent = 'Pause demo';
  timer = setInterval(() => {
    const steps = [
      ['DEMO · Scope checked', 'Simulated company-boundary check.', 'routine'],
      ['DEMO · Study selected', 'Simulated selection; historical counters remain unchanged.', 'routine'],
      ['DEMO · Review needed', 'Example alert: a finding would need evidence and human review.', 'important'],
      ['DEMO · Session complete', 'Simulation finished. No records were read.', 'important']
    ];
    event(...steps[demoStep++]);
    if (demoStep === steps.length) stopDemo();
  }, 2500);
  core();
};
$('testVoice').onclick = () => speak('ORION voice test. Device playback is available.', { test: true });
$('testAlert').onclick = () => event('TEST · Attention requested', 'This is a notification test, not a business finding.', 'important');
$('ack').onclick = () => {
  unseen = 0;
  $('unread').textContent = '0 new';
  $('toast').hidden = true;
};
$('quiet').onchange = () => {
  if ($('quiet').checked) {
    $('toast').hidden = true;
    cancelVoice('Quiet mode enabled. Automatic alert voice and popups are suppressed.');
  }
};
$('speak').onchange = () => {
  if (!$('speak').checked && speaking) cancelVoice('Response voice stopped.');
};
$('stop').onclick = () => {
  cancelVoice('Voice playback stopped.');
  if (recognition) recognition.abort();
};
$('voices').onchange = () => {
  voiceStatus('Voice selected. Use Test voice to confirm audible playback.');
};

refreshVoices();
if (voiceSupported()) {
  if (typeof window.speechSynthesis.addEventListener === 'function') {
    window.speechSynthesis.addEventListener('voiceschanged', refreshVoices);
  } else {
    window.speechSynthesis.onvoiceschanged = refreshVoices;
  }
}

const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
if (!SpeechRecognition) {
  $('mic').disabled = true;
  $('micStatus').textContent = 'Microphone recognition is unavailable in this browser. Use the text box.';
} else {
  $('mic').onclick = () => {
    if (listening) {
      recognition.stop();
      return;
    }
    cancelVoice();
    recognition = new SpeechRecognition();
    recognition.lang = 'en-US';
    recognition.continuous = false;
    recognition.interimResults = false;
    recognition.onstart = () => {
      listening = true;
      $('mic').textContent = 'Stop listening';
      $('micStatus').textContent = 'Listening for one command…';
      core();
    };
    recognition.onresult = eventObject => {
      $('query').value = eventObject.results[0][0].transcript;
      $('micStatus').textContent = 'Transcript ready. Review it and press Send.';
    };
    recognition.onerror = eventObject => {
      $('micStatus').textContent = eventObject.error === 'not-allowed'
        ? 'Microphone access was not granted. Use typing.'
        : 'Speech recognition could not complete. Try typing.';
    };
    recognition.onend = () => {
      listening = false;
      $('mic').textContent = 'Microphone';
      core();
    };
    try {
      recognition.start();
    } catch {
      $('micStatus').textContent = 'Microphone could not start. Use typing.';
    }
  };
}

$('motion').onchange = () => {
  document.body.dataset.motion = $('motion').checked ? 'on' : 'off';
};

message('Standing by. Connect the server-bound aggregate report or run a labelled interface demonstration. No ERP execution is available.');
window.addEventListener('pagehide', () => {
  stopDemo();
  if (recognition) recognition.abort();
  cancelVoice();
});
window.ORIONPreview = Object.freeze({ applyReportView });
core();
