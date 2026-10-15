'use strict';

(() => {
  const button = document.getElementById('connectReport');
  const status = document.getElementById('connectionStatus');
  const source = document.getElementById('source');

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
  window.ORIONReportLink = Object.freeze({ connect });
})();
