// Watch: video, one row of controls, the sentence list, and on-demand dictionary.
import { API } from '../api.js';
import { S } from '../state.js';
import { $, $$, esc, fmtTime, toast, renderTokens, debounce, on } from '../util.js';
import { openDict, closePanel } from '../dictpanel.js';

const LOOP_MODES = [0, 3, Infinity];

export function mountWatch(root, part) {
  const cues = part.subtitles || [];
  const saved = part.progress || {};
  const st = {
    part, cues, video: null, idx: -1, loop: 0, loopCount: 0,
    mode: S.settings.subtitle_mode || 'both',
    blur: Boolean(S.settings.blur_zh),
    rate: Number(S.settings.rate) || 1,
    filter: '', lastSave: 0, completed: false,
  };
  S.player = st;

  root.innerHTML = `
    <div class="watch">
      <div class="watch-left">
        <div class="watch-head">
          <div class="player" id="player">
            <video id="video" preload="metadata" playsinline src="${part.video_url}"></video>
            <div class="vig"></div>
            <button class="big-play" id="bigplay" aria-label="播放"><span class="tri"></span></button>
            <div class="caption" id="caption"></div>
          </div>

          <div class="controls">
            <button class="cbtn play" id="btn-play" title="播放 / 暂停 (空格)">▶</button>
            <button class="cbtn nav" id="btn-prev" title="上一句 (←)">‹</button>
            <button class="cbtn nav" id="btn-next" title="下一句 (→)">›</button>
            <button class="cbtn nav" id="btn-loop" title="单句循环 (L)">↻</button>
            <div class="track" id="track"><div class="bg"></div><div class="fg" id="fg"></div><div class="knob" id="knob"></div><div id="ticks"></div></div>
            <div class="tt" id="timecode">00:00 / ${fmtTime(part.duration)}</div>
            <div class="menu-wrap">
              <button class="cbtn" id="btn-subs">字幕</button>
              <div class="menu" id="menu-subs">
                <div class="label">字幕显示</div>
                ${[['both', '双语'], ['en', '英文'], ['zh', '中文'], ['off', '关闭']].map(([k, t]) => `<button data-mode="${k}">${t}<span class="tick"></span></button>`).join('')}
                <div class="hint">中文模式下，右侧只保留当前句的中文</div>
                <div class="rule"></div>
                <button id="opt-blur">遮住中文<span class="tick"></span></button>
                <div class="hint">点画面或右侧的中文可临时显示，再点遮住</div>
                <div class="rule"></div>
                <button id="cap-style">字幕样式…</button>
                <button id="cap-reset-pos">重置位置</button>
                <div class="hint">按住画面上的字幕即可拖动</div>
              </div>
            </div>
            <div class="menu-wrap">
              <button class="cbtn" id="btn-more" title="更多">⋯</button>
              <div class="menu" id="menu-more">
                <div class="label">语速</div>
                ${[0.75, 0.9, 1, 1.15, 1.25, 1.5].map((r) => `<button data-rate="${r}">${String(r).replace(/0$/, '')}×<span class="tick"></span></button>`).join('')}
                <div class="rule"></div>
                <button id="opt-mute">静音<span class="tick"></span></button>
              </div>
            </div>
            <button class="cbtn nav" id="btn-full" title="全屏 (F)">⛶</button>
          </div>
        </div>
      </div>

      <div class="splitter" id="splitter" title="拖动调整字幕宽度"></div>

      <div class="subs">
      <div class="subs-head">
        <b>逐句</b>
        <span class="hint" id="subs-hint">点单词查词 · 点句子跳转</span>
        <div class="tools">
          <button id="btn-search" title="在本集字幕中搜索">搜索</button>
        </div>
      </div>
      <div class="subs-search hidden" id="subs-search"><input id="subs-q" placeholder="搜索本集字幕…"></div>
      <div class="subs-list" id="subs-list"></div>
      </div>
    </div>`;

  const video = $('#video');
  const list = $('#subs-list');
  st.video = video;
  let destroyed = false;

  /* ------------------------------------------------ fullscreen dictionary */
  const fsPlayer = $('#player');
  const panelEl = document.getElementById('panel');
  const panelHome = document.getElementById('shell');
  const lockEsc = async () => {
    try {
      await navigator.keyboard?.lock?.(['Escape']);
      if (document.fullscreenElement !== fsPlayer) unlockEsc();
    } catch { /* not available / denied */ }
  };
  const unlockEsc = () => {
    try { navigator.keyboard?.unlock?.(); } catch { /* ignore */ }
  };
  const syncFsPanel = async () => {
    if (document.fullscreenElement === fsPlayer) {
      if (panelEl.parentElement !== fsPlayer) fsPlayer.appendChild(panelEl);
      panelEl.classList.add('fs');
      await lockEsc();
    } else {
      if (panelEl.parentElement !== panelHome) panelHome.appendChild(panelEl);
      panelEl.classList.remove('fs');
      unlockEsc();
    }
  };
  document.addEventListener('fullscreenchange', syncFsPanel);
  const onFsEsc = (event) => {
    if (event.key !== 'Escape') return;
    if (document.fullscreenElement !== fsPlayer) return;
    if (!panelEl.classList.contains('open')) return;
    event.preventDefault();
    event.stopPropagation();
    closePanel();
  };
  window.addEventListener('keydown', onFsEsc, true);
  const stopFsPanelClick = (event) => {
    if (panelEl.classList.contains('fs')) event.stopPropagation();
  };
  panelEl.addEventListener('click', stopFsPanelClick);
  syncFsPanel();

  /* ------------------------------------------------------- resizable split */
  const watchEl = $('.watch');
  const splitter = $('#splitter');
  try {
    const saved = Number(localStorage.getItem('vocab:split') || 0);
    if (saved >= 45 && saved <= 85) watchEl.style.setProperty('--split', `${saved}%`);
  } catch { /* ignore */ }
  let splitDragging = false;
  const setSplit = (clientX) => {
    const rect = watchEl.getBoundingClientRect();
    const pct = Math.min(85, Math.max(45, ((clientX - rect.left) / rect.width) * 100));
    watchEl.style.setProperty('--split', `${pct.toFixed(2)}%`);
  };
  const endDrag = () => {
    if (!splitDragging) return;
    splitDragging = false;
    splitter.classList.remove('dragging');
    const pct = parseFloat(watchEl.style.getPropertyValue('--split')) || 70;
    try { localStorage.setItem('vocab:split', String(pct)); } catch { /* ignore */ }
  };
  splitter.addEventListener('pointerdown', (event) => {
    splitDragging = true;
    splitter.setPointerCapture(event.pointerId);
    splitter.classList.add('dragging');
    event.preventDefault();
  });
  splitter.addEventListener('pointermove', (event) => { if (splitDragging) setSplit(event.clientX); });
  splitter.addEventListener('pointerup', endDrag);
  splitter.addEventListener('pointercancel', endDrag);

  /* --------------------------------------------------------- caption style */
  const captionEl = $('#caption');
  const CAP_KEY = 'vocab:caption';
  const CAP_PALETTE = ['#f7f0de', '#ffffff', '#e8c98a', '#bcd7f0', '#bfe3d6', '#d8c9f0'];
  const capDefaults = {
    en: { size: 16, color: '#f7f0de', bg: 0.74, weight: 400, font: 'serif' },
    zh: { size: 13, color: '#d9cfb6', bg: 0.74, weight: 400, font: 'sans' },
    shadow: true, align: 'center', tab: 'en', pos: null,
  };
  const capState = {
    ...capDefaults,
    en: { ...capDefaults.en },
    zh: { ...capDefaults.zh },
  };
  try {
    const raw = JSON.parse(localStorage.getItem(CAP_KEY) || '{}');
    if (raw && raw.en && raw.zh) {
      capState.en = { ...capDefaults.en, ...raw.en };
      capState.zh = { ...capDefaults.zh, ...raw.zh };
      capState.shadow = raw.shadow !== false;
      capState.align = raw.align || 'center';
      capState.tab = raw.tab || 'en';
      capState.pos = raw.pos || null;
    } else if (raw && (raw.scale || raw.color || raw.bg || raw.pos)) {
      const scale = Number(raw.scale) || 1;
      capState.en = {
        ...capDefaults.en,
        size: Math.round(15 * scale),
        color: raw.color || capDefaults.en.color,
        bg: raw.bg ?? 0.74,
      };
      capState.zh = {
        ...capDefaults.zh,
        size: Math.round(11 * scale),
        color: raw.color || capDefaults.zh.color,
        bg: raw.bg ?? 0.74,
      };
      capState.pos = raw.pos || null;
    }
  } catch { /* ignore */ }
  const saveCap = () => { try { localStorage.setItem(CAP_KEY, JSON.stringify(capState)); } catch { /* ignore */ } };
  const fontStack = (name) => (name === 'sans' ? 'var(--sans)' : 'var(--serif)');
  const applyCapVars = (el) => {
    el.style.setProperty('--cap-en-size', `${capState.en.size}px`);
    el.style.setProperty('--cap-en-color', capState.en.color);
    el.style.setProperty('--cap-en-bg', `rgba(6, 10, 8, ${capState.en.bg})`);
    el.style.setProperty('--cap-en-weight', String(capState.en.weight));
    el.style.setProperty('--cap-en-font', fontStack(capState.en.font));
    el.style.setProperty('--cap-zh-size', `${capState.zh.size}px`);
    el.style.setProperty('--cap-zh-color', capState.zh.color);
    el.style.setProperty('--cap-zh-bg', `rgba(6, 10, 8, ${capState.zh.bg})`);
    el.style.setProperty('--cap-zh-weight', String(capState.zh.weight));
    el.style.setProperty('--cap-zh-font', fontStack(capState.zh.font));
    el.style.setProperty('--cap-shadow', capState.shadow ? '0 1px 3px rgba(0, 0, 0, .9), 0 0 2px rgba(0, 0, 0, .55)' : 'none');
    el.style.setProperty('--cap-align', capState.align);
  };
  const applyCap = () => {
    applyCapVars(captionEl);
    const sample = $('#cap-sample');
    if (sample) applyCapVars(sample);
    if (capState.pos) {
      captionEl.classList.add('custom');
      captionEl.style.left = `${(capState.pos.x * 100).toFixed(3)}%`;
      captionEl.style.top = `${(capState.pos.y * 100).toFixed(3)}%`;
    } else {
      captionEl.classList.remove('custom');
      captionEl.style.left = '';
      captionEl.style.top = '';
    }
  };
  applyCap();

  const clampCapPos = () => {
    if (!capState.pos || !captionEl.firstElementChild) return;
    const pr = $('#player').getBoundingClientRect();
    const cr = captionEl.getBoundingClientRect();
    const halfW = cr.width / 2 + 4;
    const halfH = cr.height / 2 + 4;
    const cx = Math.min(pr.width - halfW, Math.max(halfW, capState.pos.x * pr.width));
    const cy = Math.min(pr.height - halfH, Math.max(halfH, capState.pos.y * pr.height));
    const nx = cx / pr.width;
    const ny = cy / pr.height;
    if (Math.abs(nx - capState.pos.x) > 0.001 || Math.abs(ny - capState.pos.y) > 0.001) {
      capState.pos = { x: nx, y: ny };
      applyCap();
      saveCap();
    }
  };
  const capObserver = typeof ResizeObserver === 'function'
    ? new ResizeObserver(() => clampCapPos()) : null;
  capObserver?.observe($('#player'));

  let capDrag = null;
  let capSuppress = false;
  const onCapMove = (event) => {
    if (!capDrag) return;
    const dx = event.clientX - capDrag.sx;
    const dy = event.clientY - capDrag.sy;
    if (!capDrag.moved) {
      if (Math.hypot(dx, dy) < 4) return;
      capDrag.moved = true;
      captionEl.classList.add('dragging');
    }
    event.preventDefault();
    const cx = Math.min(capDrag.pw - capDrag.w / 2 - 4, Math.max(capDrag.w / 2 + 4, capDrag.cx + dx));
    const cy = Math.min(capDrag.ph - capDrag.h / 2 - 4, Math.max(capDrag.h / 2 + 4, capDrag.cy + dy));
    capState.pos = { x: cx / capDrag.pw, y: cy / capDrag.ph };
    applyCap();
  };
  const onCapUp = () => {
    if (!capDrag) return;
    const moved = capDrag.moved;
    capDrag = null;
    window.removeEventListener('pointermove', onCapMove);
    captionEl.classList.remove('dragging');
    if (moved) {
      saveCap();
      capSuppress = true;
      setTimeout(() => { capSuppress = false; }, 0);
    }
  };
  captionEl.addEventListener('pointerdown', (event) => {
    if (event.button !== 0 || !captionEl.firstElementChild) return;
    const pr = $('#player').getBoundingClientRect();
    const cr = captionEl.getBoundingClientRect();
    capDrag = {
      sx: event.clientX, sy: event.clientY,
      cx: cr.left + cr.width / 2 - pr.left,
      cy: cr.top + cr.height / 2 - pr.top,
      w: cr.width, h: cr.height, pw: pr.width, ph: pr.height, moved: false,
    };
    window.addEventListener('pointermove', onCapMove, { passive: false });
    window.addEventListener('pointerup', onCapUp, { once: true });
    window.addEventListener('pointercancel', onCapUp, { once: true });
  });

  function closeCapStyle() {
    if (capModalClose) { capModalClose(); capModalClose = null; }
    const root = $('#modal-root');
    root.classList.remove('on');
    root.innerHTML = '';
  }
  let capModalClose = null;

  function renderCapPane(tab) {
    const pane = $('#cap-pane');
    if (!pane) return;
    capState.tab = tab;
    $$('#cap-tabs button').forEach((b) => b.classList.toggle('on', b.dataset.tab === tab));
    const update = () => { applyCap(); clampCapPos(); saveCap(); };

    if (tab === 'common') {
      pane.innerHTML = `
        <div class="cap-row"><label>阴影</label><div class="grow"><div class="seg" id="cap-shadow">${[[1, '开'], [0, '关']].map(([v, t]) => `<button data-v="${v}" class="${capState.shadow === !!v ? 'on' : ''}">${t}</button>`).join('')}</div></div></div>
        <div class="cap-row"><label>对齐</label><div class="grow"><div class="seg" id="cap-align">${[['center', '居中'], ['left', '左对齐']].map(([v, t]) => `<button data-v="${v}" class="${capState.align === v ? 'on' : ''}">${t}</button>`).join('')}</div></div></div>
        <div class="cap-row"><label>位置</label><div class="grow"><button class="btn" id="cap-pos-reset">重置到默认位置</button></div></div>
        <div class="cap-row"><label></label><div class="grow"><button class="btn" id="cap-all-reset">恢复全部默认</button></div></div>
        <div class="desc" style="margin-top:8px">按住画面上的字幕即可拖动；位置和样式会自动保存。</div>`;
      $('#cap-shadow').addEventListener('click', (event) => {
        const b = event.target.closest('[data-v]');
        if (!b) return;
        capState.shadow = b.dataset.v === '1';
        $$('#cap-shadow button').forEach((x) => x.classList.toggle('on', x === b));
        update();
      });
      $('#cap-align').addEventListener('click', (event) => {
        const b = event.target.closest('[data-v]');
        if (!b) return;
        capState.align = b.dataset.v;
        $$('#cap-align button').forEach((x) => x.classList.toggle('on', x === b));
        update();
      });
      $('#cap-pos-reset').addEventListener('click', () => {
        capState.pos = null; applyCap(); saveCap(); toast('字幕位置已重置');
      });
      $('#cap-all-reset').addEventListener('click', () => {
        const keepTab = capState.tab;
        Object.assign(capState, {
          ...capDefaults, tab: keepTab,
          en: { ...capDefaults.en },
          zh: { ...capDefaults.zh },
        });
        applyCap(); saveCap(); renderCapPane(keepTab);
      });
      return;
    }

    const lang = tab === 'zh' ? 'zh' : 'en';
    const cfg = capState[lang];
    const isEn = lang === 'en';
    const min = isEn ? 12 : 9;
    const max = isEn ? 26 : 20;
    const fonts = isEn
      ? [['serif', '衬线'], ['sans', '无衬线']]
      : [['sans', '黑体'], ['serif', '宋体']];
    pane.innerHTML = `
      <div class="cap-row"><label>字号</label><div class="grow"><input type="range" id="cap-size" min="${min}" max="${max}" step="1" value="${cfg.size}"></div><output id="cap-size-v">${cfg.size}px</output></div>
      <div class="cap-row"><label>颜色</label><div class="cap-colors" id="cap-colors">${CAP_PALETTE.map((c) => `<button class="swatch ${cfg.color.toLowerCase() === c ? 'on' : ''}" data-color="${c}" style="background:${c}"></button>`).join('')}<label class="cap-custom"><input type="color" id="cap-custom" value="${cfg.color}"><span>自定义</span></label></div></div>
      <div class="cap-row"><label>背景</label><div class="grow"><input type="range" id="cap-bg" min="0" max="80" step="5" value="${Math.round(cfg.bg * 100)}"></div><output id="cap-bg-v">${Math.round(cfg.bg * 100)}%</output></div>
      <div class="cap-row"><label>字重</label><div class="grow"><div class="seg" id="cap-weight">${[[400, '常规'], [700, '加粗']].map(([v, t]) => `<button data-v="${v}" class="${cfg.weight === v ? 'on' : ''}">${t}</button>`).join('')}</div></div></div>
      <div class="cap-row"><label>字体</label><div class="grow"><div class="seg" id="cap-font">${fonts.map(([v, t]) => `<button data-v="${v}" class="${cfg.font === v ? 'on' : ''}">${t}</button>`).join('')}</div></div></div>`;

    $('#cap-size').addEventListener('input', (event) => {
      cfg.size = Number(event.target.value);
      $('#cap-size-v').textContent = `${cfg.size}px`;
      update();
    });
    $('#cap-bg').addEventListener('input', (event) => {
      cfg.bg = Number(event.target.value) / 100;
      $('#cap-bg-v').textContent = `${event.target.value}%`;
      update();
    });
    $('#cap-colors').addEventListener('click', (event) => {
      const b = event.target.closest('[data-color]');
      if (!b) return;
      cfg.color = b.dataset.color;
      $$('#cap-colors .swatch').forEach((s) => s.classList.toggle('on', s === b));
      $('#cap-custom').value = cfg.color;
      update();
    });
    $('#cap-custom').addEventListener('input', (event) => {
      cfg.color = event.target.value;
      $$('#cap-colors .swatch').forEach((s) => s.classList.toggle('on', s.dataset.color === cfg.color));
      update();
    });
    $('#cap-weight').addEventListener('click', (event) => {
      const b = event.target.closest('[data-v]');
      if (!b) return;
      cfg.weight = Number(b.dataset.v);
      $$('#cap-weight button').forEach((x) => x.classList.toggle('on', x === b));
      update();
    });
    $('#cap-font').addEventListener('click', (event) => {
      const b = event.target.closest('[data-v]');
      if (!b) return;
      cfg.font = b.dataset.v;
      $$('#cap-font button').forEach((x) => x.classList.toggle('on', x === b));
      update();
    });
  }

  function openCapStyle() {
    const root = $('#modal-root');
    root.classList.add('on');
    root.innerHTML = `<div class="modal" id="cap-modal" style="width:min(470px,92vw)">
      <div class="modal-head"><b>悬浮字幕</b><button id="cap-close" aria-label="关闭">✕</button></div>
      <div class="modal-body">
        <div class="cap-sample" id="cap-sample">
          <div class="en-cap">It's 12 at noon and the rocking APPLe is welcoming visitors …</div>
          <div class="zh-cap">现在是正午12点，APPLe号“摇滚巨轮”迎来了乘客——</div>
        </div>
        <div class="cap-tabs" id="cap-tabs">
          <button data-tab="en">英文</button>
          <button data-tab="zh">中文</button>
          <button data-tab="common">通用</button>
        </div>
        <div id="cap-pane"></div>
      </div>
      <div class="modal-foot"><button class="btn primary" id="cap-done">完成</button></div>
    </div>`;
    const onKey = (event) => { if (event.key === 'Escape') closeCapStyle(); };
    capModalClose = () => document.removeEventListener('keydown', onKey);
    document.addEventListener('keydown', onKey);
    $('#cap-close').addEventListener('click', closeCapStyle);
    $('#cap-done').addEventListener('click', closeCapStyle);
    applyCapVars($('#cap-sample'));
    $('#cap-tabs').addEventListener('click', (event) => {
      const btn = event.target.closest('[data-tab]');
      if (!btn) return;
      capState.tab = btn.dataset.tab;
      saveCap();
      renderCapPane(btn.dataset.tab);
    });
    renderCapPane(capState.tab);
  }


  /* --------------------------------------------------------------- render */
  function renderList() {
    const q = st.filter.trim().toLowerCase();
    const rows = cues.filter((cue) => !q ||
      (cue.en || '').toLowerCase().includes(q) ||
      (cue.zh || '').toLowerCase().includes(q) ||
      (cue.speaker || '').toLowerCase().includes(q));
    list.innerHTML = rows.length ? rows.map((cue) => {
      const zh = cue.zh
        ? `<div class="sub-zh${st.blur ? ' blur' : ''}" title="点击显示 / 遮住中文">${esc(cue.zh)}</div>` : '';
      return `<div class="sub${cue.zh ? '' : ' no-zh'}" data-cue="${cue.i}">
        <div class="sub-meta">
          <span class="sub-t">${fmtTime(cue.start)}</span>
          ${cue.speaker ? `<span class="sub-spk" title="${esc(cue.speaker)}">${esc(cue.speaker)}</span>` : ''}
        </div>
        <div class="sub-body">
          <div class="sub-en">${renderTokens(cue, S.added)}</div>
          ${zh}
        </div>
      </div>`;
    }).join('') : `<div class="lib-empty" style="padding:26px 8px">没有匹配「${esc(st.filter)}」的句子</div>`;
    $('#subs-hint').textContent = q ? `${rows.length} / ${cues.length} 句` : '点单词查词 · 点句子跳转';
    syncListMode();
    updateActive(true);
  }

  function syncListMode() {
    list.dataset.mode = st.mode === 'off' ? 'both' : st.mode;
  }

  function toggleZhMask() {
    st.blur = !st.blur;
    applyPrefs();
    renderCaption();
    $$('#subs-list .sub-zh').forEach((el) => el.classList.toggle('blur', st.blur));
    syncMenuTicks();
  }

  function renderCaption() {
    const cue = cues[st.idx];
    const caption = $('#caption');
    if (!cue || st.mode === 'off') { caption.innerHTML = ''; return; }
    const en = (st.mode === 'en' || st.mode === 'both')
      ? `<div class="en en-cap">${renderTokens(cue, S.added)}</div>` : '';
    const zh = (st.mode === 'zh' || st.mode === 'both') && cue.zh
      ? `<div class="zh zh-cap" title="点击显示 / 遮住中文">${esc(cue.zh)}</div>` : '';
    caption.innerHTML = en + zh;
    caption.classList.toggle('masked', st.blur);
    if (capState.pos) clampCapPos();
  }

  function cueIndexAt(time) {
    if (!cues.length) return -1;
    if (time < cues[0].start - 0.05) return -1;
    let last = 0;
    for (let i = 0; i < cues.length; i += 1) {
      if (time >= cues[i].start - 0.05 && time < cues[i].end) return i;
      if (cues[i].start <= time) last = i;
    }
    return Math.max(0, last);
  }

  function updateActive(force = false) {
    const idx = cueIndexAt(video.currentTime);
    if (idx !== st.idx || force) {
      st.idx = idx;
      $$('.sub', list).forEach((el) => el.classList.toggle('on', Number(el.dataset.cue) === idx));
      renderCaption();
      if (idx >= 0 && !st.filter) scrollToCue(idx);
    }
    updateClock();
  }

  function scrollToCue(idx) {
    const el = list.querySelector(`.sub[data-cue="${idx}"]`);
    if (!el) return;
    const top = el.offsetTop - list.clientHeight / 2 + el.offsetHeight / 2;
    list.scrollTo({ top: Math.max(0, top), behavior: 'smooth' });
  }

  function updateClock() {
    const fg = $('#fg');
    if (!fg) return;
    const duration = video.duration || st.part.duration || 0;
    const percent = duration ? Math.min(100, (video.currentTime / duration) * 100) : 0;
    fg.style.width = `${percent}%`;
    $('#knob').style.left = `${percent}%`;
    $('#timecode').textContent = `${fmtTime(video.currentTime)} / ${fmtTime(duration)}`;
  }

  /* --------------------------------------------------------------- actions */
  function togglePlay() {
    if (video.paused) video.play().catch(() => {});
    else video.pause();
  }

  function goto(idx, play = null) {
    idx = Math.max(0, Math.min(cues.length - 1, idx));
    st.loopCount = 0;
    st.idx = idx;
    video.currentTime = Math.max(0, cues[idx].start + 0.005);
    if (play === true) video.play().catch(() => {});
    updateActive(true);
  }

  function cycleLoop() {
    st.loop = LOOP_MODES[(LOOP_MODES.indexOf(st.loop) + 1) % LOOP_MODES.length];
    st.loopCount = 0;
    const btn = $('#btn-loop');
    btn.classList.toggle('on', st.loop > 0);
    btn.textContent = st.loop === Infinity ? '↻∞' : st.loop === 3 ? '↻3' : '↻';
    btn.title = st.loop ? `单句循环：${st.loop === Infinity ? '无限' : '3 遍'}` : '单句循环 (L)';
    if (st.loop) toast(st.loop === Infinity ? '单句无限循环' : '当前句重复 3 遍');
  }

  function saveProgress() {
    if (!st.part?.id) return;
    st.part.progress.position = video.currentTime || 0;
    API.progress({ part_id: st.part.id, position: st.part.progress.position }).catch(() => {});
  }

  function applyPrefs() {
    S.settings.subtitle_mode = st.mode;
    S.settings.blur_zh = st.blur;
    S.settings.rate = st.rate;
    debounce(() => API.saveSettings({
      subtitle_mode: st.mode, blur_zh: st.blur, rate: st.rate,
    }).catch(() => {}), 600)();
  }

  function syncMenuTicks() {
    $$('#menu-subs [data-mode]').forEach((btn) => {
      const tick = btn.querySelector('.tick');
      tick.textContent = btn.dataset.mode === st.mode ? '✓' : '';
    });
    $('#opt-blur .tick').textContent = st.blur ? '✓' : '';
    $$('#menu-more [data-rate]').forEach((btn) => {
      btn.querySelector('.tick').textContent = Number(btn.dataset.rate) === st.rate ? '✓' : '';
    });
    $('#opt-mute .tick').textContent = video.muted ? '✓' : '';
  }

  function markActive(lemma) {
    $$('.w.active').forEach((el) => el.classList.remove('active'));
    const selector = `.w[data-lemma="${CSS.escape(lemma)}"]`;
    $$(selector).forEach((el) => el.classList.add('active'));
  }

  /* ----------------------------------------------------------------- video */
  video.addEventListener('loadedmetadata', () => {
    if (destroyed) return;
    const duration = video.duration || st.part.duration || 1;
    $('#ticks').innerHTML = cues.map((cue) => `<div class="tick" style="left:${(cue.start / duration) * 100}%"></div>`).join('');
    const pending = S.pendingSeek;
    if (pending && pending.partId === st.part.id) {
      const cueStart = pending.cue >= 0 ? (cues[pending.cue]?.start || 0) : 0;
      video.currentTime = Number.isFinite(pending.start) ? pending.start : cueStart;
    } else if (saved.position > 3 && saved.position < duration - 4) {
      video.currentTime = saved.position;
    } else {
      video.currentTime = 0.12;
    }
    if (pending) S.pendingSeek = null;
    updateActive(true);
  });

  video.addEventListener('timeupdate', () => {
    if (destroyed) return;
    if (st.loop && st.idx >= 0) {
      const cue = cues[st.idx];
      if (video.currentTime >= cue.end - 0.05) {
        const limit = st.loop === Infinity ? Infinity : 3;
        if (st.loopCount < limit) {
          st.loopCount += 1;
          video.currentTime = cue.start + 0.005;
          return;
        }
        st.loop = 0;
        st.loopCount = 0;
        $('#btn-loop').classList.remove('on');
        $('#btn-loop').textContent = '↻';
      }
    }
    updateActive();
    if (!video.paused && Date.now() - st.lastSave > 5000) {
      st.lastSave = Date.now();
      saveProgress();
    }
  });

  video.addEventListener('play', () => {
    if (destroyed) return;
    $('#btn-play').textContent = '❚❚';
    $('#bigplay').classList.add('hide');
  });
  video.addEventListener('pause', () => {
    if (destroyed) return;
    $('#btn-play').textContent = '▶';
    $('#bigplay').classList.remove('hide');
    saveProgress();
  });
  video.addEventListener('ended', () => {
    if (destroyed) return;
    $('#bigplay').classList.remove('hide');
    if (!st.completed) {
      st.completed = true;
      API.progress({ part_id: st.part.id, event: 'complete' }).then(() => {
        st.part.progress.completed_at = new Date().toISOString();
        toast('本集已看完');
      }).catch(() => {});
    }
    saveProgress();
  });
  video.addEventListener('seeked', () => { if (!destroyed) updateActive(true); });

  /* ---------------------------------------------------------------- wiring */
  $('#btn-play').addEventListener('click', togglePlay);
  $('#bigplay').addEventListener('click', togglePlay);
  $('#player').addEventListener('click', (event) => {
    if (event.target.closest('#bigplay') || event.target.closest('.caption') || event.target.closest('#panel')) return;
    togglePlay();
  });
  $('#btn-prev').addEventListener('click', () => goto(Math.max(0, st.idx - 1), !video.paused));
  $('#btn-next').addEventListener('click', () => goto(Math.min(cues.length - 1, st.idx + 1), !video.paused));
  $('#btn-loop').addEventListener('click', cycleLoop);
  $('#btn-full').addEventListener('click', async () => {
    if (document.fullscreenElement) { document.exitFullscreen(); return; }
    try { await $('#player').requestFullscreen?.(); } catch { /* ignore */ }
    lockEsc();
  });

  function closeMenus(except) {
    $$('.menu').forEach((menu) => { if (menu !== except) menu.classList.remove('on'); });
  }
  function bindMenu(button, menu) {
    $(button).addEventListener('click', (event) => {
      event.stopPropagation();
      const willOpen = !$(menu).classList.contains('on');
      closeMenus();
      $(menu).classList.toggle('on', willOpen);
    });
  }
  bindMenu('#btn-subs', '#menu-subs');
  bindMenu('#btn-more', '#menu-more');
  document.addEventListener('click', () => closeMenus());

  $('#menu-subs').addEventListener('click', (event) => {
    const mode = event.target.closest('[data-mode]');
    if (mode) {
      st.mode = mode.dataset.mode;
      applyPrefs();
      renderCaption();
      syncListMode();
      syncMenuTicks();
      if (st.idx >= 0 && !st.filter) scrollToCue(st.idx);
      return;
    }
    if (event.target.closest('#opt-blur')) {
      toggleZhMask();
      return;
    }
    if (event.target.closest('#cap-style')) { openCapStyle(); return; }
    if (event.target.closest('#cap-reset-pos')) {
      capState.pos = null;
      applyCap();
      saveCap();
      toast('字幕位置已重置');
    }
  });
  $('#menu-more').addEventListener('click', (event) => {
    const rate = event.target.closest('[data-rate]');
    if (rate) {
      st.rate = Number(rate.dataset.rate);
      video.playbackRate = st.rate;
      applyPrefs();
      syncMenuTicks();
      closeMenus();
      return;
    }
    if (event.target.closest('#opt-mute')) {
      video.muted = !video.muted;
      syncMenuTicks();
    }
  });

  const track = $('#track');
  let dragging = false;
  const seekTo = (clientX) => {
    const rect = track.getBoundingClientRect();
    const ratio = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
    video.currentTime = ratio * (video.duration || st.part.duration || 0);
  };
  track.addEventListener('pointerdown', (event) => {
    dragging = true;
    track.setPointerCapture(event.pointerId);
    seekTo(event.clientX);
  });
  track.addEventListener('pointermove', (event) => { if (dragging) seekTo(event.clientX); });
  track.addEventListener('pointerup', () => { dragging = false; });

  $('#btn-search').addEventListener('click', () => {
    const box = $('#subs-search');
    box.classList.toggle('hidden');
    if (!box.classList.contains('hidden')) $('#subs-q').focus();
    else { st.filter = ''; $('#subs-q').value = ''; renderList(); }
  });
  $('#subs-q').addEventListener('input', debounce((event) => {
    st.filter = event.target.value;
    renderList();
  }, 160));

  list.addEventListener('click', (event) => {
    const word = event.target.closest('.w');
    if (word) {
      event.stopPropagation();
      const cue = Number(word.closest('.sub').dataset.cue);
      markActive(word.dataset.lemma);
      openDict(word.dataset.lemma, {
        cue, partId: st.part.id,
        start: Number(word.dataset.s), end: Number(word.dataset.e),
      });
      return;
    }
    if (event.target.closest('.sub-zh')) {
      event.preventDefault();
      event.stopPropagation();
      toggleZhMask();
      return;
    }
    const row = event.target.closest('.sub');
    if (row) goto(Number(row.dataset.cue), !video.paused);
  });

  $('#caption').addEventListener('click', (event) => {
    if (capSuppress) {
      capSuppress = false;
      event.preventDefault();
      event.stopPropagation();
      return;
    }
    if (event.target.closest('.zh')) {
      event.preventDefault();
      event.stopPropagation();
      toggleZhMask();
      return;
    }
    const word = event.target.closest('.w');
    if (!word || st.idx < 0) return;
    event.preventDefault();
    event.stopPropagation();
    markActive(word.dataset.lemma);
    openDict(word.dataset.lemma, {
      cue: st.idx, partId: st.part.id,
      start: Number(word.dataset.s), end: Number(word.dataset.e),
    });
  });

  /* -------------------------------------------------------------- keyboard */
  function onKey(event) {
    if (event.target.matches('input, textarea, select')) return;
    if ($('#modal-root').classList.contains('on')) return;
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    switch (event.key) {
      case ' ': event.preventDefault(); togglePlay(); break;
      case 'ArrowLeft': event.preventDefault(); goto(Math.max(0, st.idx - 1), !video.paused); break;
      case 'ArrowRight': event.preventDefault(); goto(Math.min(cues.length - 1, st.idx + 1), !video.paused); break;
      case 'l': case 'L': cycleLoop(); break;
      case 'r': case 'R': if (st.idx >= 0) goto(st.idx, true); break;
      case 'f': case 'F': $('#btn-full').click(); break;
      case 'Escape': {
        if (document.fullscreenElement) {
          event.preventDefault();
          document.exitFullscreen?.();
        } else if (S.panel) {
          event.preventDefault();
          closePanel();
        } else {
          closeMenus();
        }
        break;
      }
      default: break;
    }
  }

  /* ------------------------------------------------------------------- bus */
  const offSeek = on('seek-cue', ({ cue }) => {
    if (Number.isFinite(cue)) goto(cue, false);
  });
  const offWords = on('words-changed', () => {
    $$('.sub .w', list).forEach((el) => el.classList.toggle('added', S.added.has(el.dataset.lemma)));
    renderCaption();
  });

  /* ------------------------------------------------------------------ init */
  video.playbackRate = st.rate;
  syncMenuTicks();
  renderList();
  $('#btn-loop').title = '单句循环 (L)';
  window.addEventListener('keydown', onKey);

  S.cleanup = () => {
    destroyed = true;
    document.removeEventListener('fullscreenchange', syncFsPanel);
    window.removeEventListener('keydown', onFsEsc, true);
    unlockEsc();
    panelEl.removeEventListener('click', stopFsPanelClick);
    if (panelEl.parentElement !== panelHome) panelHome.appendChild(panelEl);
    panelEl.classList.remove('fs');
    window.removeEventListener('keydown', onKey);
    window.removeEventListener('pointermove', onCapMove);
    capObserver?.disconnect();
    if ($('#cap-modal')) closeCapStyle();
    offSeek();
    offWords();
    saveProgress();
    video.pause();
    video.removeAttribute('src');
    video.load();
    if (S.player === st) S.player = null;
  };
}
