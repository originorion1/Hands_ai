'use strict';

(() => {
  const button = document.getElementById('connectReport');
  const status = document.getElementById('connectionStatus');
  const source = document.getElementById('source');
  let reportConnected = false;

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
        state.report_service !== 'not_connected' ||
        state.governed_worker !== 'not_connected' ||
        state.persistent_storage !== 'not_connected'
      ) {
        throw Error('status service returned an unsafe state');
      }
      if (!reportConnected) {
        source.textContent = 'Interface API connected · governed services disconnected';
      }
    } catch {
      if (!reportConnected) {
        source.textContent = 'Interface API unavailable · no operational result inferred';
      }
    }
  }

  async function connect() {
    button.disabled = true;
    status.textContent = 'Loading the server-bound aggregate report…';
    try {
      const response = await fetch('/api/v1/report', {
        cache: 'no-store',
        credentials: 'same-origin',
        headers: { Accept: 'application/json' }
      });
      if (!response.ok) throw Error(`report service returned ${response.status}`);
      const view = await response.json();
      window.ORIONPreview.applyReportView(view);
      reportConnected = true;
      source.textContent = 'Source: exact server-bound aggregate report · read-only';
      status.textContent = 'Connected. The completed aggregate is loaded read-only.';
      button.textContent = 'Refresh report';
    } catch {
      status.textContent = 'Report unavailable. Previous view unchanged; no fallback data was used.';
    } finally {
      button.disabled = false;
    }
  }

  button.addEventListener('click', connect);
  window.ORIONReportLink = Object.freeze({ connect, checkBackend });
  checkBackend();
})();
