'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const script = fs.readFileSync(path.join(__dirname, '../../deployment/customer/portal.js'), 'utf8');
assert(!/localStorage|sessionStorage/.test(script));

class Element {
  constructor() { this.textContent = ''; this.value = ''; this.hidden = false; this.children = []; this.listeners = {}; }
  addEventListener(name, listener) { this.listeners[name] = listener; }
  replaceChildren(...children) { this.children = children; }
  append(...children) { this.children.push(...children); }
}

const ids = [
  'status', 'workspace', 'admin', 'loginPanel', 'organization', 'identity',
  'connections', 'authorization', 'studies', 'evidence', 'findings',
  'reportPanel', 'reportDetails',
  'apiKey', 'apiSecret', 'loginForm', 'switchForm', 'inviteForm',
  'removeForm', 'enrollForm', 'scopeForm', 'logout'
];
const elements = Object.fromEntries(ids.map(id => [id, new Element()]));
elements.apiKey.value = 'synthetic-key';
elements.apiSecret.value = 'synthetic-secret';
let denied = false;
let interrupted = false;
let reportAvailable = false;
const fixtureReport = {
  authority: {execution_allowed: false, erp_writes: 0},
  lineage: {run_id: 'synthetic-run-1'},
  procurement: {
    tenant_id: 'tenant-alpha', run_id: 'synthetic-run-1',
    item: 'Synthetic item', target_ref: 'synthetic-target', company: 'Synthetic Alpha',
    comparison_refs: ['invoice-1', 'invoice-2'], target_unit_usd: '16.00',
    normalized_unit: 'each', reference_unit_usd: '10.00',
    status: 'candidate_review', confidence: 'limited_synthetic',
    estimated_exposure_usd: '60.00', alternative_explanations: ['quality check'],
    missing_evidence: ['market context'], reviewer_decision: 'pending',
    independently_verified_savings_usd: null, source_system: 'synthetic-records',
    connector_identity: 'synthetic-connector', source_id: 'synthetic-source',
    source_data_sha256: 'source-digest', finding_sha256: 'finding-digest',
    evidence_ids: ['evidence-1'], authorization_id: 'synthetic-grant',
    authorization_status: 'read_only_admitted'
  }
};
const persistedReport = process.argv[2]
  ? JSON.parse(fs.readFileSync(process.argv[2], 'utf8')) : fixtureReport;
assert.equal(persistedReport.procurement.run_id, persistedReport.lineage.run_id);
assert.equal(persistedReport.authority.execution_allowed, false);
assert.equal(persistedReport.authority.erp_writes, 0);
let servedReport = persistedReport;
const document = {
  cookie: '__Host-orion_csrf=synthetic-csrf',
  getElementById: id => elements[id] || (elements[id] = new Element()),
  createElement: () => new Element(),
  createTextNode: value => ({textContent: value})
};
const context = vm.createContext({
  Date, document, location: {assign() {}},
  fetch: async path => {
    if (interrupted) throw new Error('network interruption');
    if (path === '/customer/api/report' && !denied) {
      return {status: 200, ok: true, json: async () => servedReport};
    }
    return denied ? {status: 401, ok: false} : {
    status: 200, ok: true, json: async () => ({
      tenant_id: persistedReport.procurement.tenant_id, role: 'administrator', subject: 'admin',
      session_expires_at: 2000000000,
      organization_name: persistedReport.procurement.company,
      connections: [], authorization_status: 'no_approved_grant_bound',
      autonomous_study_proposals: [], evidence_coverage: null, findings: [],
      report_available: reportAvailable
    })
    };
  }
});
vm.runInContext(script, context, {filename: 'portal.js'});

(async () => {
  await vm.runInContext('refresh()', context);
  assert.equal(elements.workspace.hidden, false);
  assert.equal(elements.organization.textContent, persistedReport.procurement.company);
  reportAvailable = true;
  await vm.runInContext('refresh()', context);
  assert.equal(elements.reportPanel.hidden, false);
  assert(elements.reportDetails.children.some(line =>
    line.children.some(child => child.textContent ===
      `$${persistedReport.procurement.estimated_exposure_usd}`)));
  assert(elements.reportDetails.children.some(line =>
    line.children.some(child => child.textContent.includes(
      persistedReport.procurement.source_data_sha256))));
  assert(elements.reportDetails.children.some(line =>
    line.children.some(child => child.textContent.includes(
      persistedReport.procurement.finding_sha256))));
  servedReport = {
    authority: {execution_allowed: false, erp_writes: 0},
    report_kind: 'synthetic_lineage',
    lineage: {run_id: 'generic-run', evidence_ids: ['evidence-1']}
  };
  await vm.runInContext('refresh()', context);
  assert.match(elements.reportDetails.children[0].textContent, /generic-run/);
  servedReport = persistedReport;
  denied = true;
  await assert.rejects(vm.runInContext('request("/customer/api/overview")', context));
  assert.equal(elements.workspace.hidden, true);
  assert.equal(elements.loginPanel.hidden, false);
  assert.equal(elements.organization.textContent, 'Organization');
  assert.equal(elements.connections.children.length, 0);
  assert.equal(elements.reportPanel.hidden, true);
  assert.equal(elements.reportDetails.children.length, 0);
  assert.match(elements.status.textContent, /Access denied/);
  assert.equal(elements.apiKey.value, '');
  assert.equal(elements.apiSecret.value, '');
  denied = false;
  await vm.runInContext('refresh()', context);
  interrupted = true;
  await assert.rejects(vm.runInContext('request("/customer/api/overview")', context));
  assert.equal(elements.workspace.hidden, true);
  console.log('customer portal browser denial clears scoped data');
})().catch(error => { console.error(error); process.exitCode = 1; });
