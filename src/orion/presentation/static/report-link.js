'use strict';

(() => {
  const button = document.getElementById('connectReport');
  const status = document.getElementById('connectionStatus');
  const source = document.getElementById('source');
  const tokenInput = document.getElementById('reportToken');
  let reportConnected = false;
  let accessToken = '';

  async function checkBackend() {
    try {
      const response = await fetch('/api/v1/status', {
        cache: 'no-store',
        credentials: 'same-origin',
        headers: { Accept: 'application/json' }
      });
      if (!response.ok) throw Error('status service unavailable');
      const state = await response.json();
      if (
        !state || state.interface_version !== 'orion.deployment-status.v1' ||
        state.http_api !== 'healthy' || state.execution_allowed !== false ||
        state.live_pilot_ready !== false ||
        !['not_connected', 'requires_authentication'].includes(state.report_service) ||
        state.governed_worker !== 'not_connected' ||
        state.persistent_storage !== 'not_connected'
      ) {
        throw Error('status service returned an unsafe state');
      }
      if (!reportConnected) {
        source.textContent = state.report_service === 'requires_authentication'
          ? 'Interface API available · report requires authentication'
          : 'Interface API connected · governed services disconnected';
      }
    } catch {
      if (!reportConnected) {
        source.textContent = 'Interface API unavailable · no operational result inferred';
      }
    }
  }

  async function connect() {
    if (tokenInput.value) {
      accessToken = tokenInput.value.trim();
      tokenInput.value = '';
    }
    if (!accessToken) {
      status.textContent = 'Enter a short-lived report access token to authenticate.';
      return;
    }
    button.disabled = true;
    status.textContent = 'Checking access and loading the bound aggregate report…';
    try {
      const response = await fetch('/api/v1/report', {
        cache: 'no-store',
        credentials: 'same-origin',
        headers: { Accept: 'application/json', Authorization: `Bearer ${accessToken}` }
      });
      if (response.status === 401 || response.status === 403) {
        accessToken = '';
        throw Error('access_denied');
      }
      if (response.status === 503) throw Error('disconnected');
      if (!response.ok) throw Error(`report service returned ${response.status}`);
      const view = await response.json();
      window.ORIONPreview.applyReportView(view);
      reportConnected = true;
      const procurement = view.report_kind === 'procurement_price_leakage';
      source.textContent = procurement
        ? 'Source: authenticated, digest-bound procurement finding · read-only'
        : 'Source: authenticated, digest-bound aggregate report · read-only';
      status.textContent = procurement
        ? 'Connected. The persisted procurement finding is loaded read-only.'
        : 'Connected. The completed aggregate is loaded read-only.';
      button.textContent = 'Refresh report';
    } catch (error) {
      reportConnected = false;
      window.ORIONPreview.clearReportView();
      source.textContent = 'Source: no authorized report loaded';
      status.textContent = error.message === 'access_denied'
        ? 'Access denied or expired. Enter a new report access token.'
        : error.message === 'disconnected'
          ? 'Report service disconnected. No report data is shown.'
          : 'Report unavailable or invalid. No report data is shown.';
      button.textContent = 'Connect report';
    } finally {
      button.disabled = false;
    }
  }

  button.addEventListener('click', connect);
  window.ORIONReportLink = Object.freeze({ connect, checkBackend });
  checkBackend();
})();
