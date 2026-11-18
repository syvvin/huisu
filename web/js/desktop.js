// Native pywebview window chrome: custom title bar, window buttons, fullscreen link.
import { $ } from './util.js';

let bridge = null;
let maximized = false;

function setMaximized(value) {
  maximized = !!value;
  document.body.classList.toggle('win-maximized', maximized);
  const button = $('#win-max');
  if (!button) return;
  button.classList.toggle('maxed', maximized);
  button.title = maximized ? '还原' : '最大化';
  button.setAttribute('aria-label', button.title);
}

async function toggleMaximized() {
  if (!bridge) return;
  try {
    setMaximized(await bridge.toggle_maximize());
  } catch { /* keep the native window responsive */ }
}

function reveal() {
  bridge = window.pywebview?.api || null;
  if (!bridge) return;
  const bar = $('#titlebar');
  if (!bar) return;
  document.body.classList.add('desktop-shell');
  bar.hidden = false;
  installResizers();
}

function installResizers() {
  if (document.getElementById('resize-layer')) return;
  const layer = document.createElement('div');
  layer.id = 'resize-layer';
  layer.className = 'resize-layer';
  for (const dir of ['n', 's', 'e', 'w', 'ne', 'nw', 'se', 'sw']) {
    const handle = document.createElement('div');
    handle.className = `resize-handle ${dir}`;
    handle.dataset.dir = dir;
    handle.addEventListener('pointerdown', startResize);
    layer.appendChild(handle);
  }
  document.body.appendChild(layer);
}

function startResize(event) {
  if (!bridge || maximized || event.button !== 0) return;
  if (document.fullscreenElement || document.body.classList.contains('video-fullscreen')) return;
  const handle = event.currentTarget;
  const dir = handle.dataset.dir;
  const pointerId = event.pointerId;
  const startX = event.screenX;
  const startY = event.screenY;
  let startW = 0;
  let startH = 0;
  let ready = false;
  let active = true;
  let last = null;
  let frame = 0;
  let pending = null;

  event.preventDefault();
  event.stopPropagation();
  try { handle.setPointerCapture(pointerId); } catch { /* ignore */ }

  const flush = () => {
    frame = 0;
    if (!pending || !bridge) return;
    const { w, h } = pending;
    pending = null;
    bridge.resize_window(w, h, dir);
  };

  const apply = (ev) => {
    if (!ready || !active) return;
    let w = startW;
    let h = startH;
    if (dir.includes('e')) w = startW + (ev.screenX - startX);
    if (dir.includes('w')) w = startW - (ev.screenX - startX);
    if (dir.includes('s')) h = startH + (ev.screenY - startY);
    if (dir.includes('n')) h = startH - (ev.screenY - startY);
    pending = { w: Math.round(Math.max(1040, w)), h: Math.round(Math.max(680, h)) };
    if (!frame) frame = requestAnimationFrame(flush);
  };

  const move = (ev) => {
    if (ev.pointerId !== pointerId) return;
    last = ev;
    apply(ev);
  };

  const up = (ev) => {
    if (ev.pointerId !== pointerId || !active) return;
    active = false;
    if (last) apply(last);
    if (pending) flush();
    handle.removeEventListener('pointermove', move);
    handle.removeEventListener('pointerup', up);
    handle.removeEventListener('pointercancel', up);
    try { handle.releasePointerCapture(pointerId); } catch { /* ignore */ }
  };

  handle.addEventListener('pointermove', move);
  handle.addEventListener('pointerup', up);
  handle.addEventListener('pointercancel', up);
  bridge.window_bounds().then((bounds) => {
    if (!active || !bounds || !bounds.width || !bounds.height) return;
    startW = bounds.width;
    startH = bounds.height;
    ready = true;
    if (last) apply(last);
  }).catch(() => { /* ignore */ });
}

export function mountDesktopChrome() {
  const bar = $('#titlebar');
  if (!bar) return;

  if (window.pywebview?.api) reveal();
  else window.addEventListener('pywebviewready', reveal, { once: true });

  $('#win-min')?.addEventListener('click', () => bridge?.minimize());
  $('#win-max')?.addEventListener('click', () => toggleMaximized());
  $('#win-close')?.addEventListener('click', () => bridge?.close_window());
  $('#titlebar-drag')?.addEventListener('dblclick', (event) => {
    event.preventDefault();
    toggleMaximized();
  });

  document.addEventListener('fullscreenchange', () => {
    const active = Boolean(document.fullscreenElement);
    document.body.classList.toggle('video-fullscreen', active);
    bridge?.set_fullscreen?.(active);
  });
}
