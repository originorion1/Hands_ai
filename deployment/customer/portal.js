'use strict';
const byId = id => document.getElementById(id);
let overview = null;

function status(message) { byId('status').textContent = message; }
function csrf() {
  const entry = document.cookie.split('; ').find(item => item.startsWith('__Host-orion_csrf='));
  return entry ? entry.slice('__Host-orion_csrf='.length) : '';
}
function clear() {
  overview = null;
  byId('workspace').hidden = true;
  byId('admin').hidden = true;
  byId('loginPanel').hidden = false;
  byId('organization').textContent = 'Organization';
  byId('identity').textContent = '';
  byId('connections').replaceChildren();
  byId('authorization').textContent = '';
  byId('studies').textContent = 'No study proposal is available from an approved source.';
  byId('evidence').textContent = 'No evidence coverage is available.';
  byId('findings').textContent = 'No finding is available.';
  byId('reportPanel').hidden = true;
  byId('reportDetails').replaceChildren();
  byId('apiKey').value = '';
  byId('apiSecret').value = '';
}
async function request(path, body) {
  const options = {credentials: 'same-origin', cache: 'no-store'};
  if (body !== undefined) {
    options.method = 'POST';
    options.headers = {'Content-Type': 'application/json', 'X-Orion-CSRF': csrf()};
    options.body = JSON.stringify(body);
  }
  let response;
  try { response = await fetch(path, options); }
  catch {
    clear();
    throw new Error('Connection interrupted. Sign in again before viewing organization data.');
  }
  if (response.status === 401 || response.status === 403) {
    clear();
    throw new Error('Access denied. Sign in again.');
  }
  if (!response.ok) {
    clear();
    throw new Error('Service unavailable. Sign in again before viewing organization data.');
  }
  try { return await response.json(); }
  catch {
    clear();
    throw new Error('Invalid service response. Sign in again.');
  }
}
async function refresh() {
  const data = await request('/customer/api/overview');
  const report = data.report_available ? await request('/customer/api/report') : null;
  if (report && (!report.lineage?.run_id ||
      (report.procurement && (report.procurement.tenant_id !== data.tenant_id ||
       report.procurement.run_id !== report.lineage.run_id)) ||
      report.authority?.execution_allowed !== false || report.authority?.erp_writes !== 0)) {
    clear();
    throw new Error('Report lineage or authorization mismatch. Sign in again.');
  }
  overview = data;
  byId('loginPanel').hidden = true;
  byId('workspace').hidden = false;
  byId('admin').hidden = data.role !== 'administrator';
  byId('organization').textContent = data.organization_name;
  byId('identity').textContent = `${data.tenant_id} · ${data.role} · session expires ${new Date(data.session_expires_at * 1000).toLocaleString()}`;
  byId('connections').replaceChildren();
  for (const connection of data.connections) {
    const item = document.createElement('li');
    item.textContent = `${connection.label}: ${connection.connector} · ${connection.status} · ${connection.company || 'company unverified'} · read authority absent`;
    byId('connections').append(item);
  }
  if (!data.connections.length) byId('connections').append(document.createTextNode('No connector enrolled.'));
  byId('authorization').textContent = `Authorization: ${data.authorization_status}. This workspace performs no ERP writes.`;
  byId('studies').textContent = data.autonomous_study_proposals.length
    ? data.autonomous_study_proposals.join('; ') : 'No approved study proposal is available.';
  byId('evidence').textContent = data.evidence_coverage === null
    ? 'No approved evidence coverage is available.' : String(data.evidence_coverage);
  byId('findings').textContent = data.findings.length
    ? `${data.findings.length} evidence-linked finding(s) available.` : 'No finding is available.';
  if (report?.procurement) {
    const finding = report.procurement;
    const details = [
      ['OBSERVED FACT', `${finding.item} · ${finding.target_ref} · ${finding.company}`],
      ['Supporting transactions', finding.comparison_refs.join(', ')],
      ['Normalized price', `$${finding.target_unit_usd} per ${finding.normalized_unit}; reference $${finding.reference_unit_usd}`],
      ['INFERRED FINDING', `${finding.status} · ${finding.confidence}`],
      ['ESTIMATED FINANCIAL EXPOSURE', `$${finding.estimated_exposure_usd}`],
      ['Alternative explanations', finding.alternative_explanations.join('; ')],
      ['Missing evidence', finding.missing_evidence.join('; ') || 'None recorded'],
      ['Reviewer decision', finding.reviewer_decision],
      ['INDEPENDENTLY VERIFIED SAVINGS', finding.independently_verified_savings_usd ?? 'None verified'],
      ['Source provenance', `${finding.source_system} · ${finding.connector_identity} · ${finding.source_id}`],
      ['Investigation and custody', `${report.lineage.run_id} · source ${finding.source_data_sha256} · finding ${finding.finding_sha256}`],
      ['Evidence references', finding.evidence_ids.join(', ')],
      ['Authorization', `${finding.authorization_id} · read-only · ${finding.authorization_status}`]
    ];
    const container = byId('reportDetails');
    container.replaceChildren();
    for (const [label, value] of details) {
      const line = document.createElement('p');
      const strong = document.createElement('strong');
      strong.textContent = `${label}: `;
      line.append(strong, document.createTextNode(value));
      container.append(line);
    }
    byId('reportPanel').hidden = false;
  } else if (report) {
    const line = document.createElement('p');
    line.textContent = `${report.report_kind} · run ${report.lineage.run_id} · read-only · ${report.lineage.evidence_ids?.length ?? 0} evidence references`;
    byId('reportDetails').replaceChildren(line);
    byId('reportPanel').hidden = false;
  }
  status(report ? 'Authenticated. Evidence-linked read-only report loaded.' :
    'Authenticated. No approved report or acquisition grant is bound.');
}
function bind(id, handler) {
  byId(id).addEventListener('submit', async event => {
    event.preventDefault();
    try { await handler(); } catch (error) { status(error.message); }
  });
}
bind('loginForm', async () => {
  location.assign(`/customer/login?tenant=${encodeURIComponent(byId('loginTenant').value)}`);
});
bind('switchForm', async () => {
  await request('/customer/api/switch', {tenant_id: byId('switchTenant').value});
  clear();
  await refresh();
});
bind('inviteForm', async () => {
  await request('/customer/api/invite', {
    issuer: byId('inviteIssuer').value, subject: byId('inviteSubject').value,
    role: byId('inviteRole').value, expires_at: Math.floor(Date.now() / 1000) + 86400
  });
  status('Invitation pending. The exact IdP subject must sign in before expiry.');
});
bind('removeForm', async () => {
  await request('/customer/api/remove', {
    issuer: byId('removeIssuer').value, subject: byId('removeSubject').value
  });
  status('Member removed. Existing access is denied on its next check.');
});
bind('enrollForm', async () => {
  try {
    await request('/customer/api/erpnext/enroll', {
      label: byId('connectionLabel').value, endpoint: byId('erpOrigin').value,
      company: byId('erpCompany').value,
      api_key: byId('apiKey').value, api_secret: byId('apiSecret').value
    });
    await refresh();
    status('Connector enrolled in the protected vault. Read authority remains absent.');
  } finally {
    byId('apiKey').value = '';
    byId('apiSecret').value = '';
  }
});
bind('scopeForm', async () => {
  if (!overview) throw new Error('Sign in again.');
  const fields = byId('scopeFields').value.split(',').map(value => value.trim()).filter(Boolean);
  const exclusions = byId('scopeExclusions').value.split(',').map(value => value.trim()).filter(Boolean);
  const result = await request('/customer/api/authorization-proposals', {
    tenant_id: overview.tenant_id, companies: [byId('scopeCompany').value],
    fields_by_doctype: [[byId('scopeDoctype').value, fields]],
    sensitive_exclusions: exclusions, historical_start: byId('historyStart').value,
    historical_end: byId('historyEnd').value, max_requests: Number(byId('maxRequests').value),
    max_duration_seconds: Number(byId('maxDuration').value),
    max_evidence_bytes: Number(byId('maxEvidence').value),
    retention_days: Number(byId('retention').value),
    revocation_operator: byId('revocationOperator').value, read_only: true
  });
  status(`Proposal ${result.proposal_id} is pending separate governed validation. No ERP read is authorized.`);
});
byId('logout').addEventListener('click', async () => {
  try { await request('/customer/api/logout', {}); }
  catch (error) { status(error.message); }
  finally { clear(); status('Signed out.'); }
});
refresh().catch(() => { clear(); status('Sign in to view your organization.'); });
