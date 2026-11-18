// Remember the interface a dictionary jump came from, and restore it on back.
import { S } from './state.js';
import { $ } from './util.js';

const hashNow = () => location.hash || '#/';

function currentView() {
  const parts = location.hash.replace(/^#\/?/, '').split('/').filter(Boolean);
  if (parts[0] === 'wordbook') {
    return { type: 'wordbook', wb: { ...(S.ui.wb || {}) } };
  }
  if (parts[0] === 'p' && parts[1]) {
    const stage = parts[2] || 'watch';
    const view = { type: stage, partId: Number(parts[1]) };
    if (stage === 'watch') {
      view.time = Number.isFinite(S.player?.video?.currentTime) ? S.player.video.currentTime : null;
      view.paused = S.player?.video ? S.player.video.paused : true;
      view.subsScroll = $('#subs-list')?.scrollTop || 0;
    }
    return view;
  }
  if (parts[0] === 'review') return { type: 'review' };
  return { type: 'library' };
}

export function captureReturnView() {
  const body = document.querySelector('.panel-body');
  const snapshot = {
    hash: hashNow(),
    scrollY: window.scrollY || 0,
    mainScroll: $('#main')?.scrollTop || 0,
    stageScroll: $('#stage')?.scrollTop || 0,
    view: currentView(),
    dict: S.panel === 'dict' && S.dictQuery
      ? { ...S.dictQuery, scroll: body?.scrollTop || 0 }
      : null,
  };
  S.returnView = snapshot;
  return snapshot;
}

export function pendingReturnForRoute() {
  if (!S.returnView || S.returnView.hash !== hashNow()) return null;
  const snapshot = S.returnView;
  S.returnView = null;
  return snapshot;
}

export function goBack() {
  if (S.returnView) {
    const target = S.returnView.hash || '#/';
    if (hashNow() === target) return;
    location.hash = target;
    return;
  }
  if (window.history.length > 1) window.history.back();
  else location.hash = '#/';
}

export function goHome() {
  S.returnView = null;
  location.hash = '#/';
}

export async function restoreReturnView(snapshot) {
  if (!snapshot) return;
  const view = snapshot.view || {};

  if (view.type === 'watch' && S.player?.video && S.part?.id === view.partId) {
    const video = S.player.video;
    const applyVideo = () => {
      if (Number.isFinite(view.time)) {
        try { video.currentTime = view.time; } catch { /* metadata not ready */ }
      }
      if (view.paused) video.pause();
      else video.play().catch(() => {});
    };
    if (video.readyState >= 1) applyVideo();
    else video.addEventListener('loadedmetadata', applyVideo, { once: true });
  }

  if (snapshot.dict?.lemma) {
    const { openDict } = await import('./dictpanel.js');
    await openDict(snapshot.dict.lemma, {
      partId: snapshot.dict.partId,
      cue: snapshot.dict.cue,
      start: snapshot.dict.start,
      end: snapshot.dict.end,
      auto: false,
    });
    const body = document.querySelector('.panel-body');
    if (body) body.scrollTop = snapshot.dict.scroll || 0;
  }

  requestAnimationFrame(() => {
    window.scrollTo(0, snapshot.scrollY || 0);
    const main = $('#main');
    if (main) main.scrollTop = snapshot.mainScroll || 0;
    const stage = $('#stage');
    if (stage) stage.scrollTop = snapshot.stageScroll || 0;
    if (view.type === 'watch') {
      const list = $('#subs-list');
      if (list) list.scrollTop = view.subsScroll || 0;
    }
  });
}
