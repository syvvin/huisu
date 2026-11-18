// Shell: boot, router, top bar.
import { API } from './api.js';
import { S, applySettings } from './state.js';
import { $, esc, toast, debounce, on } from './util.js';
import { mountLibrary } from './views/library.js';
import { mountPart } from './views/part.js';
import { mountWordbook } from './views/wordbook.js';
import { mountReviewPage } from './views/review.js';
import { mountExtract } from './views/extract.js';
import { openSettings } from './settings.js';
import { pendingReturnForRoute, restoreReturnView } from './navigation.js';
import { closePanel } from './dictpanel.js';
import { mountDesktopChrome } from './desktop.js';

async function boot() {
  mountDesktopChrome();
  wireNav();
  try {
    applySettings(await API.settings());
  } catch (err) {
    toast(`读取设置失败：${err.message}`, 'err');
  }
  try {
    S.lib = await API.library();
  } catch (err) {
    $('#main').innerHTML = `<div class="page"><div class="lib-empty">
      片库读取失败：${esc(err.message)}<br><br>
      <button class="btn" id="fatal-settings">打开设置</button></div></div>`;
    $('#fatal-settings')?.addEventListener('click', openSettings);
    return;
  }
  renderNav();
  on('library-stale', debounce(() => { refreshLibrary(); }, 600));
  window.addEventListener('hashchange', route);
  await route();
}

function closeNav() {
  $('#sidebar').classList.remove('open');
  $('#nav-scrim').classList.remove('show');
  $('#nav-toggle').textContent = '☰';
  $('#nav-toggle').setAttribute('aria-label', '打开导航');
}

function wireNav() {
  const toggle = $('#nav-toggle');
  toggle.addEventListener('click', () => {
    const open = !$('#sidebar').classList.contains('open');
    $('#sidebar').classList.toggle('open', open);
    $('#nav-scrim').classList.toggle('show', open);
    toggle.textContent = open ? '✕' : '☰';
    toggle.setAttribute('aria-label', open ? '关闭导航' : '打开导航');
  });
  $('#nav-scrim').addEventListener('click', closeNav);
  $('#sidebar').querySelectorAll('a').forEach((a) => a.addEventListener('click', closeNav));
  $('#btn-settings').addEventListener('click', () => { closeNav(); openSettings(); });
  $('#btn-theme').addEventListener('click', async () => {
    const theme = S.settings.theme === 'light' ? 'dark' : 'light';
    try {
      await API.saveSettings({ theme });
      applySettings({ theme });
    } catch (err) { toast(err.message, 'err'); }
  });
  window.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && $('#sidebar').classList.contains('open')) closeNav();
  });
}

export async function refreshLibrary() {
  try {
    S.lib = await API.library();
    renderNav();
  } catch { /* keep the last known library */ }
}

function renderNav() {
  const due = S.lib?.totals?.due || 0;
  $('#side-due').textContent = String(due);
  $('#side-due').style.color = due ? 'var(--gold)' : 'var(--faint)';
}

function setNav(name) {
  document.querySelectorAll('#sidebar a[data-nav]').forEach((a) => {
    a.classList.toggle('on', a.dataset.nav === name);
  });
}

async function route() {
  const seq = ++S.routeSeq;
  const raw = location.hash.replace(/^#\/?/, '');
  const parts = raw.split('/').filter(Boolean);
  const returning = pendingReturnForRoute();
  if (returning?.view?.type === 'wordbook' && returning.view.wb) {
    S.ui.wb = { ...returning.view.wb };
  }
  if (S.cleanup) { try { S.cleanup(); } catch { /* ignore */ } S.cleanup = null; }
  closePanel();
  closeNav();
  const main = $('#main');
  main.classList.remove('lock');
  main.scrollTop = 0;

  if (!parts.length) {
    setNav('library');
    await mountLibrary();
  } else if (parts[0] === 'wordbook') {
    setNav('wordbook');
    await mountWordbook();
  } else if (parts[0] === 'review') {
    setNav('library');
    await mountReviewPage();
  } else if (parts[0] === 'extract') {
    setNav('extract');
    await mountExtract();
  } else if (parts[0] === 'p' && parts[1]) {
    setNav('library');
    const stage = ['preview', 'watch', 'review'].includes(parts[2]) ? parts[2] : 'watch';
    await mountPart(Number(parts[1]), stage, seq);
  } else {
    location.hash = '#/';
    return;
  }
  if (seq === S.routeSeq) {
    renderNav();
    if (returning) {
      try { await restoreReturnView(returning); } catch { /* keep the page usable */ }
    }
  }
}

window.addEventListener('DOMContentLoaded', boot);
window.addEventListener('beforeunload', () => {
  if (S.part?.id && S.player?.video) {
    const data = JSON.stringify({ part_id: S.part.id, position: S.player.video.currentTime });
    navigator.sendBeacon('/api/progress', new Blob([data], { type: 'application/json' }));
  }
});
