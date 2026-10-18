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
  append(child) { this.children.push(child); }
}

const ids = [
  'status', 'workspace', 'admin', 'loginPanel', 'organization', 'identity',
  'connections', 'authorization', 'studies', 'evidence', 'findings',
  'apiKey', 'apiSecret', 'loginForm', 'switchForm', 'inviteForm',
  'removeForm', 'enrollForm', 'scopeForm', 'logout'
];
const elements = Object.fromEntries(ids.map(id => [id, new Element()]));
elements.apiKey.value = 'synthetic-key';
elements.apiSecret.value = 'synthetic-secret';
let denied = false;
let interrupted = false;
const document = {
  cookie: '__Host-orion_csrf=synthetic-csrf',
  getElementById: id => elements[id] || (elements[id] = new Element()),
  createElement: () => new Element(),
  createTextNode: value => ({textContent: value})
};
const context = vm.createContext({
  Date, document, location: {assign() {}},
  fetch: async () => {
    if (interrupted) throw new Error('network interruption');
    return denied ? {status: 401, ok: false} : {
    status: 200, ok: true, json: async () => ({
      tenant_id: 'tenant-alpha', role: 'administrator', subject: 'admin',
      session_expires_at: 2000000000, organization_name: 'Synthetic Alpha',
      connections: [], authorization_status: 'no_approved_grant_bound',
      autonomous_study_proposals: [], evidence_coverage: null, findings: []
    })
    };
  }
});
vm.runInContext(script, context, {filename: 'portal.js'});

(async () => {
  await vm.runInContext('refresh()', context);
  assert.equal(elements.workspace.hidden, false);
  assert.equal(elements.organization.textContent, 'Synthetic Alpha');
  denied = true;
  await assert.rejects(vm.runInContext('request("/customer/api/overview")', context));
  assert.equal(elements.workspace.hidden, true);
  assert.equal(elements.loginPanel.hidden, false);
  assert.equal(elements.organization.textContent, 'Organization');
  assert.equal(elements.connections.children.length, 0);
  assert.equal(elements.apiKey.value, '');
  assert.equal(elements.apiSecret.value, '');
  denied = false;
  await vm.runInContext('refresh()', context);
  interrupted = true;
  await assert.rejects(vm.runInContext('request("/customer/api/overview")', context));
  assert.equal(elements.workspace.hidden, true);
  console.log('customer portal browser denial clears scoped data');
})().catch(error => { console.error(error); process.exitCode = 1; });
