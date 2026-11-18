// Review: one card at a time. Context, reveal, three judgements.
import { API } from '../api.js';
import { S } from '../state.js';
import { $, $$, esc, fmtTime, toast, emit, playSegment, pronounce } from '../util.js';
import { openDict } from '../dictpanel.js';

const RATING = { 1: 'again', 2: 'hard', 3: 'good' };
let audio = null;
let replayToken = 0;

function scopeSub(scope) {
  return scope === 'all' ? '词库全部词条' : '所有到期词条';
}

function updateScopeUi(scope) {
  $$('#review-scopes [data-scope]').forEach((btn) => {
    btn.classList.toggle('on', btn.dataset.scope === scope);
  });
  const sub = $('#review-sub');
  if (sub) sub.textContent = scopeSub(scope);
}

export async function mountReviewPage() {
  const main = $('#main');
  main.innerHTML = `<div class="page">
    <div class="page-head"><h1>温习</h1><span class="page-sub" id="review-sub">所有到期词条</span></div>
    <div class="review-scopes" id="review-scopes">
      <button type="button" data-scope="due" class="on">到期复习</button>
      <button type="button" data-scope="all">全部词</button>
    </div>
    <div class="review" id="review" style="padding-top:16px"></div>
  </div>`;
  const review = $('#review');

  async function start(scope) {
    if (S.cleanup) { try { S.cleanup(); } catch { /* ignore */ } S.cleanup = null; }
    updateScopeUi(scope);
    await mountPartReview(review, null, { scope });
  }

  $$('#review-scopes [data-scope]').forEach((btn) => {
    btn.addEventListener('click', () => start(btn.dataset.scope));
  });
  await start('due');
}

export async function mountPartReview(container, part, opts = {}) {
  const scope = opts.scope || (part ? 'part' : 'due');
  const session = {
    part, scope, items: [], index: 0, revealed: false,
    results: { good: 0, hard: 0, again: 0 },
    skipped: 0, skippedIds: new Set(), ratings: new Map(),
    startedAt: Date.now(), shownAt: Date.now(), done: false,
    mode: S.ui.reviewMode || 'cloze',
  };
  container.innerHTML = `<div class="review-top"><span>准备中…</span></div>`;

  if (opts.items) {
    session.items = [...opts.items];
  } else {
    let data;
    try {
      data = await API.queue({ part_id: part?.id || 0, scope, limit: 120 });
    } catch (err) {
      container.innerHTML = `<div class="review-done"><h3>读取失败</h3><p>${esc(err.message)}</p></div>`;
      return;
    }
    session.items = data.items || [];
  }

  if (!session.items.length) {
    container.innerHTML = emptyHtml(part, scope);
    $('#empty-all')?.addEventListener('click', () => {
      updateScopeUi('all');
      mountPartReview(container, null, { scope: 'all' });
    });
    return;
  }

  const scopeText = part
    ? `PART ${String(part.number).padStart(2, '0')} · 本集全部`
    : (scope === 'all' ? '全部词条' : '到期复习');
  const quitHref = part ? `#/p/${part.id}/watch` : '#/';

  container.innerHTML = `
    <div class="review-top">
      <span id="rev-pos"></span>
      <span class="scope" id="rev-scope">${esc(scopeText)}</span>
      <button class="mode" id="rev-mode" title="切换复习方式"></button>
      <a class="quit" id="rev-quit" href="${quitHref}" title="退出温习">退出</a>
    </div>
    <div class="review-progress"><span id="rev-bar"></span></div>
    <div class="review-meta"><span id="rev-counts"></span><span id="rev-acc"></span></div>
    <div id="card-host" style="width:100%;max-width:540px"></div>
    <div class="rate-row" id="rate-row"></div>
    <div class="review-hint" id="rev-hint">
      <span id="rev-keys">空格 显示释义 · 1 忘了 · 2 模糊 · 3 记得 · S 跳过 · R 重听 · P 发音</span>
      <button type="button" id="rev-skip">跳过 →</button>
    </div>`;

  const host = $('#card-host');
  const modeBtn = $('#rev-mode');
  modeBtn.addEventListener('click', () => {
    session.mode = session.mode === 'cloze' ? 'word' : 'cloze';
    S.ui.reviewMode = session.mode;
    session.revealed = false;
    render();
  });
  $('#rev-skip').addEventListener('click', skip);

  function current() { return session.items[session.index]; }

  function render() {
    if (session.done) return;
    const item = current();
    if (!item) { renderDone(); return; }
    const rated = session.results.good + session.results.hard + session.results.again;
    const total = session.items.length;
    $('#rev-pos').textContent = `第 ${session.index + 1} / ${total} 张`;
    $('#rev-scope').textContent = scopeText;
    modeBtn.textContent = session.mode === 'cloze' ? '语境填空 ⇄' : '看词回想 ⇄';
    const bar = $('#rev-bar');
    if (bar) bar.style.width = `${total ? Math.round((session.index / total) * 100) : 0}%`;
    $('#rev-counts').textContent = `✓ ${session.results.good} · ~ ${session.results.hard} · ↺ ${session.results.again}${session.skipped ? ` · 跳过 ${session.skipped}` : ''}`;
    $('#rev-acc').textContent = rated ? `正确率 ${Math.round((session.results.good / rated) * 100)}%` : '';
    session.shownAt = Date.now();
    host.innerHTML = cardHtml(item, session);
    $('.card', host)?.addEventListener('click', () => { if (!session.revealed) reveal(); });
    $('#replay')?.addEventListener('click', (event) => {
      event.stopPropagation();
      replay(item);
    });
    $('#say-word')?.addEventListener('click', (event) => {
      event.stopPropagation();
      pronounce(item.lemma, S.settings.pronounce_accent || 'us');
    });
    $('#card-detail')?.addEventListener('click', (event) => {
      event.stopPropagation();
      openDict(item.lemma, { partId: item.part_id || S.part?.id, cue: item.context?.cue });
    });
    renderRate();
  }

  function reveal() {
    session.revealed = true;
    render();
    replay(current());
  }

  function replay(item) {
    const ctx = item?.context;
    if (!ctx) return;
    const token = ++replayToken;
    if (!audio) {
      audio = document.createElement('video');
      audio.className = 'audio-video';
      audio.preload = 'metadata';
      document.body.appendChild(audio);
    }
    if (audio.dataset.part !== String(ctx.part_id)) {
      audio.src = ctx.video_url;
      audio.dataset.part = String(ctx.part_id);
      audio.load();
    }
    const go = () => {
      if (token !== replayToken) return;
      playSegment(audio, ctx.start, ctx.end);
    };
    if (audio.readyState >= 1) go();
    else audio.addEventListener('loadedmetadata', go, { once: true });
  }

  function renderRate() {
    const row = $('#rate-row');
    if (!session.revealed) {
      row.innerHTML = `<button class="rate good" id="btn-reveal" style="flex:1">显示释义<small>空格 / 点击卡片</small></button>`;
      $('#btn-reveal')?.addEventListener('click', reveal);
      return;
    }
    row.innerHTML = `
      <button class="rate" data-r="1">忘了<small>10 分钟后再来</small></button>
      <button class="rate" data-r="2">模糊<small>明天再确认</small></button>
      <button class="rate good" data-r="3">记得<small>拉长间隔</small></button>`;
    $$('.rate[data-r]', row).forEach((btn) => {
      btn.addEventListener('click', () => rate(Number(btn.dataset.r)));
    });
  }

  async function rate(rating) {
    const item = current();
    if (!item || !session.revealed || session.done) return;
    replayToken += 1;
    if (audio) audio.pause();
    let result;
    try {
      result = await API.review(item.id, {
        rating,
        part_id: item.context?.part_id || item.part_id || session.part?.id || 0,
        elapsed_ms: Date.now() - session.shownAt,
      });
    } catch (err) {
      toast(err.message, 'err');
      return;
    }
    if (result?.word) Object.assign(item, result.word);
    item.lastRating = rating;
    session.ratings.set(item.id, rating);
    session.results[RATING[rating]] += 1;
    if (session.part) {
      session.part.progress.reviewed_at = new Date().toISOString();
      const link = document.querySelector(`.stages a[href="#/p/${session.part.id}/review"]`);
      if (link && !link.querySelector('.tick')) link.insertAdjacentHTML('beforeend', '<span class="tick">✓</span>');
    }
    session.index += 1;
    session.revealed = false;
    if (session.index >= session.items.length) {
      renderDone();
      emit('library-stale');
    } else {
      render();
    }
  }

  function skip() {
    const item = current();
    if (!item || session.done) return;
    replayToken += 1;
    if (audio) audio.pause();
    session.skipped += 1;
    session.skippedIds.add(item.id);
    session.index += 1;
    session.revealed = false;
    if (session.index >= session.items.length) {
      renderDone();
      emit('library-stale');
    } else {
      render();
    }
  }

  function restart(items = null, nextScope = session.scope) {
    if (S.cleanup) { try { S.cleanup(); } catch { /* ignore */ } S.cleanup = null; }
    updateScopeUi(nextScope);
    mountPartReview(container, session.part, { scope: nextScope, items });
  }

  function renderDone() {
    session.done = true;
    const total = session.results.good + session.results.hard + session.results.again;
    const minutes = Math.max(1, Math.round((Date.now() - session.startedAt) / 60000));
    const accuracy = total ? Math.round((session.results.good / total) * 100) : 0;
    const missed = session.items.filter((item) => {
      const rating = session.ratings.get(item.id);
      return rating === 1 || rating === 2 || session.skippedIds.has(item.id);
    });
    const bar = $('#rev-bar');
    if (bar) bar.style.width = '100%';
    $('#rev-pos').textContent = '完成';
    $('#rev-scope').textContent = scopeText;
    $('#rev-counts').textContent = `✓ ${session.results.good} · ~ ${session.results.hard} · ↺ ${session.results.again}${session.skipped ? ` · 跳过 ${session.skipped}` : ''}`;
    $('#rev-acc').textContent = total ? `正确率 ${accuracy}%` : '';
    modeBtn.style.display = 'none';
    $('#rev-skip').style.display = 'none';
    $('#rev-hint').style.display = 'none';
    host.innerHTML = `<div class="review-done">
      <h3>${session.part ? '本集温习完成' : (scope === 'all' ? '全部词温习完成' : '今日温习完成')}</h3>
      <p>${total} 张已评分${session.skipped ? ` · ${session.skipped} 张跳过` : ''} · 正确率 ${accuracy}% · 用时 ${minutes} 分钟</p>
      <div class="done-stats">
        <div><b>${session.results.good}</b><span>记得</span></div>
        <div><b>${session.results.hard}</b><span>模糊</span></div>
        <div><b>${session.results.again}</b><span>忘了</span></div>
      </div>
      <div class="done-actions">
        <button class="btn primary" id="done-again">再刷本轮</button>
        ${missed.length ? `<button class="btn" id="done-missed">只练没记住的 ${missed.length}</button>` : ''}
        ${session.part
          ? `<a class="btn" href="#/p/${session.part.id}/watch">回到观看</a>
             <a class="btn" href="#/review">去温习</a>`
          : scope === 'due'
            ? `<button class="btn" id="done-all">复习全部词</button>
               <a class="btn" href="#/">回到片库</a>`
            : `<button class="btn" id="done-due">复习到期词</button>
               <a class="btn" href="#/">回到片库</a>`}
      </div>
    </div>`;
    $('#rate-row').innerHTML = '';
    $('#done-again')?.addEventListener('click', () => restart(session.items, scope));
    $('#done-missed')?.addEventListener('click', () => restart(missed, scope));
    $('#done-all')?.addEventListener('click', () => restart(null, 'all'));
    $('#done-due')?.addEventListener('click', () => restart(null, 'due'));
  }

  function onKey(event) {
    if (event.target.matches('input, textarea, select')) return;
    if ($('#modal-root').classList.contains('on')) return;
    if (session.done) return;
    const key = event.key.toLowerCase();
    if (event.key === ' ') {
      event.preventDefault();
      if (!session.revealed) reveal();
      return;
    }
    if (key === 's') { event.preventDefault(); skip(); return; }
    if (key === 'r') { event.preventDefault(); replay(current()); return; }
    if (key === 'p') { event.preventDefault(); if (current()) pronounce(current().lemma, S.settings.pronounce_accent || 'us'); return; }
    if (!session.revealed) return;
    if (event.key === '1') rate(1);
    else if (event.key === '2') rate(2);
    else if (event.key === '3') rate(3);
  }
  window.addEventListener('keydown', onKey);

  S.cleanup = () => {
    window.removeEventListener('keydown', onKey);
    replayToken += 1;
    if (audio) { audio.pause(); audio.removeAttribute('src'); audio.load(); audio.remove(); audio = null; }
  };

  render();
}

function cardHtml(item, session) {
  const ctx = item.context;
  const revealed = session.revealed;
  const target = ctx?.surface || item.display || item.lemma;
  const sentence = ctx?.en || '';
  const hasTarget = hasTargetIn(sentence, target);
  const wordOnly = session.mode === 'word' || !hasTarget;
  const showWordFront = !revealed && wordOnly;
  const gloss = (item.brief_zh || '').replace(/^[a-z]{1,5}\.\s*/i, '');
  const top = `<div class="card-top">
      <span>${session.part ? `PART ${String(session.part.number).padStart(2, '0')}` : '复习'}</span>
      <span>${ctx ? fmtTime(ctx.start) : ''}</span>
      <span class="r">BOX ${item.box} / 5</span>
    </div>`;

  const front = showWordFront
    ? `<div class="card-word" style="margin-top:32px">${esc(item.display || item.lemma)}</div>
       <div class="card-ph">${esc(item.phonetic || '')}</div>
       <div class="card-hint">点按卡片 / 空格 显示释义</div>`
    : `<div class="card-sentence">${revealed && hasTarget ? hitHtml(sentence, target) : blankHtml(sentence, target)}</div>
       ${ctx?.speaker ? `<div class="card-speaker">${esc(ctx.speaker)}</div>` : ''}
       ${revealed ? '' : '<div class="card-hint">空格 / 点击卡片 显示释义</div>'}`;

  const reveal = revealed
    ? `<div class="card-reveal">
        <div class="card-word">${esc(item.display || item.lemma)}</div>
        <div class="card-ph">${esc(item.phonetic || '')}</div>
        <div class="card-means">${item.brief_zh ? `<span class="pos">${esc(item.pos || '')}</span>${esc(gloss)}` : '暂无释义'}</div>
        <div class="card-tags">
          <span>${item.reps ? `复习 ${item.reps} 次` : '新词'}</span>
          ${ctx?.part_title_en ? `<span>${esc(ctx.part_title_en)}</span>` : ''}
        </div>
        ${item.note ? `<div class="card-note">备注：${esc(item.note)}</div>` : ''}
        <div class="card-replay">
          <button id="say-word">♪ 单词</button>
          ${ctx ? '<button id="replay">♪ 原句</button>' : ''}
          <button id="card-detail">详细信息</button>
        </div>
      </div>`
    : '';

  return `<div class="card">
    ${top}
    ${front}
    ${reveal}
    <div class="card-foot">
      <span>${session.mode === 'cloze' ? '语境填空' : '看词回想'}</span>
      <span class="r">${esc(ctx?.speaker || '')}</span>
    </div>
  </div>`;
}

function hasTargetIn(text, target) {
  return Boolean(text && target && text.toLowerCase().includes(target.toLowerCase()));
}

function blankHtml(text, target) {
  if (!text) return '';
  if (!hasTargetIn(text, target)) return esc(text);
  const idx = text.toLowerCase().indexOf(target.toLowerCase());
  const width = Math.max(44, Math.min(180, target.length * 10));
  return `${esc(text.slice(0, idx))}<span class="blank" style="min-width:${width}px">${esc(text.slice(idx, idx + target.length))}</span>${esc(text.slice(idx + target.length))}`;
}

function hitHtml(text, target) {
  if (!text) return '';
  if (!hasTargetIn(text, target)) return esc(text);
  const idx = text.toLowerCase().indexOf(target.toLowerCase());
  return `${esc(text.slice(0, idx))}<span class="hit">${esc(text.slice(idx, idx + target.length))}</span>${esc(text.slice(idx + target.length))}`;
}

function emptyHtml(part, scope) {
  if (part) {
    const total = (S.words || []).filter((w) => w.status !== 'ignored').length;
    return `<div class="review-done">
      <h3>${total ? '本集暂时没有可复习的词' : '本集还没有词条'}</h3>
      <p>${total ? '这些词可能已被忽略，或队列为空。' : '先在预习或观看时收录几个生词，温习会按记忆曲线排队。'}</p>
      <div class="done-actions" style="margin-top:18px">
        <a class="btn" href="#/p/${part.id}/preview">去预习</a>
        <a class="btn primary" href="#/p/${part.id}/watch">去观看</a>
        <a class="btn" href="#/review">去温习</a>
      </div>
    </div>`;
  }
  if (scope === 'all') {
    return `<div class="review-done">
      <h3>词库还没有可复习的词</h3>
      <p>先在预习或观看时收录一些生词。</p>
      <div class="done-actions" style="margin-top:18px"><a class="btn primary" href="#/">回到片库</a></div>
    </div>`;
  }
  return `<div class="review-done">
    <h3>今天没有到期的词</h3>
    <p>词卡会按 10 分钟 / 1 / 2 / 4 / 8 / 16 天的间隔回来。<br>也可以现在复习全部词条，或先去片库看一集新的。</p>
    <div class="done-actions" style="margin-top:18px">
      <button class="btn primary" id="empty-all">复习全部词</button>
      <a class="btn" href="#/">回到片库</a>
    </div>
  </div>`;
}
