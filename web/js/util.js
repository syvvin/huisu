// Small DOM / formatting helpers shared by every view.

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

export function esc(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

export function fmtTime(seconds, withHours = false) {
  const s = Math.max(0, Math.floor(Number(seconds) || 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (withHours && h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`;
  return `${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`;
}

export function fmtDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const today = new Date();
  const diff = Math.round((startOfDay(d) - startOfDay(today)) / 86400000);
  if (diff === 0) return '今天';
  if (diff === 1) return '明天';
  if (diff === -1) return '昨天';
  if (diff > 1 && diff < 30) return `${diff} 天后`;
  if (diff < -1 && diff > -30) return `${-diff} 天前`;
  return `${d.getMonth() + 1}.${String(d.getDate()).padStart(2, '0')}`;
}

function startOfDay(d) {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

export function debounce(fn, wait = 300) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

export function toast(message, type = 'ok') {
  const wrap = $('#toast-wrap');
  const el = document.createElement('div');
  el.className = `toast${type === 'err' ? ' err' : ''}`;
  el.innerHTML = type === 'err'
    ? `<span>!</span><span>${esc(message)}</span>`
    : `<span class="tick">✓</span><span>${esc(message)}</span>`;
  wrap.appendChild(el);
  setTimeout(() => {
    el.style.transition = 'opacity .25s';
    el.style.opacity = '0';
    setTimeout(() => el.remove(), 260);
  }, type === 'err' ? 4200 : 1800);
}

export const store = {
  get(key, fallback) {
    try {
      const raw = localStorage.getItem(`vocab:${key}`);
      return raw === null ? fallback : JSON.parse(raw);
    } catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem(`vocab:${key}`, JSON.stringify(value)); } catch { /* ignore */ }
  },
};

// Minimal event bus: views subscribe to cross-cutting changes (word list updates).
export const bus = new EventTarget();
export const emit = (name, detail) => bus.dispatchEvent(new CustomEvent(name, { detail }));
export const on = (name, fn) => {
  const handler = (event) => fn(event.detail);
  bus.addEventListener(name, handler);
  return () => bus.removeEventListener(name, handler);
};

export function renderTokens(cue, addedSet, activeLemma) {
  let html = '';
  let last = 0;
  const text = cue.en || '';
  for (const [s, e, lemma] of cue.tokens || []) {
    if (s > last) html += esc(text.slice(last, s));
    const surface = text.slice(s, e);
    const classes = ['w'];
    if (addedSet && addedSet.has(lemma)) classes.push('added');
    if (activeLemma && activeLemma === lemma) classes.push('active');
    html += `<span class="${classes.join(' ')}" data-s="${s}" data-e="${e}" data-lemma="${esc(lemma)}">${esc(surface)}</span>`;
    last = e;
  }
  if (last < text.length) html += esc(text.slice(last));
  return html;
}

export function boxes(box) {
  return `<span class="boxes">${[0, 1, 2, 3, 4].map((i) => `<i class="${i < (box || 0) ? 'on' : ''}"></i>`).join('')}</span>`;
}

export function wordState(word) {
  if (word.status === 'ignored') return { label: '已忽略', cls: '' };
  if (word.due) return { label: '待温习', cls: 'due' };
  if (word.box >= 4) return { label: '已掌握', cls: 'new' };
  if (word.box === 0) return { label: '新词', cls: 'new' };
  return { label: `${word.box} / 5`, cls: '' };
}

export function tagHtml(tags = []) {
  return tags.map((t) => `<span class="tag">${esc(t)}</span>`).join('');
}

export function srcLabel(source) {
  return { ecdict: '本地词库', youdao: '有道', freedict: 'Free Dict', cefr: 'CEFR' }[source] || source;
}

const segmentState = new WeakMap();

export function playSegment(video, start, end, onDone) {
  if (!video) return;
  const prev = segmentState.get(video);
  if (prev) {
    if (prev.timer) clearTimeout(prev.timer);
    video.removeEventListener('timeupdate', prev.check);
    segmentState.delete(video);
  }
  const begin = Math.max(0, start - 0.08);
  const finishAt = Number.isFinite(end) && end > begin ? end : begin + 15;
  const token = {};
  const state = { check: null, timer: null, token };
  let done = false;
  const stop = () => {
    if (done) return;
    done = true;
    if (state.timer) clearTimeout(state.timer);
    video.removeEventListener('timeupdate', check);
    if (segmentState.get(video) === state) segmentState.delete(video);
    video.pause();
    if (onDone) onDone();
  };
  const check = () => {
    if (video.currentTime >= finishAt - 0.02) stop();
  };
  state.check = check;
  segmentState.set(video, state);
  video.pause();
  video.currentTime = begin;
  video.play().catch(() => {});
  video.addEventListener('timeupdate', check);
  state.timer = setTimeout(() => {
    if (segmentState.get(video)?.token === token) stop();
  }, Math.min(30000, ((finishAt - begin) + 2) * 1000));
}

let pronAudio = null;

export function speak(text, lang = 'en-US') {
  if (!window.speechSynthesis || !text) return;
  window.speechSynthesis.cancel();
  const utter = new SpeechSynthesisUtterance(text);
  utter.lang = lang;
  utter.rate = 0.92;
  const voices = window.speechSynthesis.getVoices().filter((v) => /^en[-_]/i.test(v.lang));
  const wanted = lang === 'en-GB' ? /en[-_]GB/i : /en[-_]US/i;
  voices.sort((a, b) => Number(wanted.test(b.lang)) - Number(wanted.test(a.lang)));
  if (voices[0]) utter.voice = voices[0];
  window.speechSynthesis.speak(utter);
}

export function pronounce(text, accent = 'us') {
  if (!text) return;
  const uk = accent === 'uk';
  const lang = uk ? 'en-GB' : 'en-US';
  const type = uk ? 1 : 2;
  if (pronAudio) {
    try { pronAudio.pause(); } catch { /* ignore */ }
    pronAudio = null;
  }
  let fellBack = false;
  const fallback = () => {
    if (fellBack) return;
    fellBack = true;
    if (pronAudio) {
      try { pronAudio.pause(); } catch { /* ignore */ }
    }
    speak(text, lang);
  };
  try {
    const audio = new Audio(`https://dict.youdao.com/dictvoice?audio=${encodeURIComponent(text)}&type=${type}`);
    pronAudio = audio;
    audio.addEventListener('error', fallback, { once: true });
    audio.play().catch(fallback);
  } catch {
    fallback();
  }
}

export function confirmDialog({ title = '确认', message = '', okText = '确定', danger = false }) {
  return new Promise((resolve) => {
    const root = $('#modal-root');
    root.classList.add('on');
    root.innerHTML = `<div class="modal" style="width:min(420px,90vw)">
      <div class="modal-head"><b>${esc(title)}</b><button data-close>✕</button></div>
      <div class="modal-body" style="font-size:11.5px;line-height:1.8;color:var(--dim)">${esc(message)}</div>
      <div class="modal-foot">
        <button class="btn" data-close>取消</button>
        <button class="btn ${danger ? 'danger' : 'primary'}" data-ok>${esc(okText)}</button>
      </div>
    </div>`;
    const finish = (value) => {
      root.classList.remove('on');
      root.innerHTML = '';
      resolve(value);
    };
    root.querySelectorAll('[data-close]').forEach((el) => el.addEventListener('click', () => finish(false)));
    root.querySelector('[data-ok]').addEventListener('click', () => finish(true));
    root.addEventListener('click', (event) => { if (event.target === root) finish(false); }, { once: true });
    document.addEventListener('keydown', function onKey(event) {
      if (event.key === 'Escape') { document.removeEventListener('keydown', onKey); finish(false); }
    });
  });
}
