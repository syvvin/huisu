// Part shell: title, stages and the single stage body.
import { API } from '../api.js';
import { S } from '../state.js';
import { $, esc } from '../util.js';
import { setWords, openDeck } from '../dictpanel.js';
import { mountWatch } from './watch.js';
import { mountPreview } from './preview.js';
import { mountPartReview } from './review.js';
import { goBack, goHome } from '../navigation.js';

export async function mountPart(id, stage, seq) {
  const main = $('#main');
  if (!S.part || S.part.id !== id) {
    main.classList.add('lock');
    main.innerHTML = `<div class="page"><div class="lib-empty">载入 Part…</div></div>`;
    try {
      const part = await API.part(id);
      if (seq !== S.routeSeq) return;
      S.part = part;
      setWords(part.words || []);
      S.dict = null;
      S.dictQuery = null;
      try { localStorage.setItem('vocab:lastPart', String(part.id)); } catch { /* ignore */ }
      API.progress({ part_id: part.id, event: 'open' }).catch(() => {});
    } catch (err) {
      if (seq !== S.routeSeq) return;
      main.innerHTML = `<div class="page"><div class="lib-empty">打开失败：${esc(err.message)}</div></div>`;
      return;
    }
  }
  if (seq !== S.routeSeq) return;

  const lock = stage === 'watch' || stage === 'review';
  main.classList.toggle('lock', lock);
  main.innerHTML = headerHtml(S.part, stage) + `<div class="stage" id="stage"></div>`;
  const stageEl = $('#stage');
  $('#btn-deck')?.addEventListener('click', () => openDeck());
  $('#part-back')?.addEventListener('click', goBack);
  $('#part-home')?.addEventListener('click', (event) => {
    event.preventDefault();
    goHome();
  });

  if (stage === 'preview') {
    stageEl.classList.add('scroll');
    await mountPreview(stageEl, S.part);
  } else if (stage === 'review') {
    stageEl.classList.add('review');
    await mountPartReview(stageEl, S.part);
  } else {
    mountWatch(stageEl, S.part);
  }
}

function headerHtml(part, stage) {
  const words = S.words.filter((w) => w.status !== 'ignored');
  const flat = (S.lib?.chapters || []).flatMap((ch) => ch.parts);
  const idx = flat.findIndex((p) => p.id === part.id);
  const prev = idx > 0 ? flat[idx - 1] : null;
  const next = idx >= 0 && idx < flat.length - 1 ? flat[idx + 1] : null;
  const done = {
    preview: Boolean(part.progress?.previewed_at),
    watch: isWatched(part),
    review: Boolean(part.progress?.reviewed_at),
  };
  const stageLink = (key, label) => `<a class="${stage === key ? 'on' : ''}" href="#/p/${part.id}/${key}">${label}${done[key] && stage !== key ? '<span class="tick">✓</span>' : ''}</a>`;
  const navLink = (target, arrow, dir) => target
    ? `<a href="#/p/${target.id}/${stage}" title="${dir}：${esc(target.title_en)}">${arrow}</a>`
    : `<a class="off" aria-disabled="true">${arrow}</a>`;
  return `<header class="part-head">
    <div class="crumb">
      <button class="back" id="part-back" type="button" title="返回跳转前的界面">← 返回</button>
      <a class="home" id="part-home" href="#/" title="返回片库主页">⌂ 主页</a>
      <span class="sep">/</span>
      <span>${esc(part.story_title_en || part.story_title_zh || '')}</span>
      <span class="sep">/</span>
      <span>${esc(part.chapter_title_zh || part.chapter_title_en)}</span>
      <span class="sep">/</span>
      <span>PART ${String(part.number).padStart(2, '0')}</span>
    </div>
    <div class="part-nav">
      ${navLink(prev, '‹', '上一集')}
      ${navLink(next, '›', '下一集')}
    </div>
    <div class="part-title-row">
      <h1>${esc(part.title_en)}</h1>
      <span class="zh">${esc(part.title_zh || '')}</span>
    </div>
    <nav class="stages">
      ${stageLink('preview', '预习')}
      ${stageLink('watch', '观看')}
      ${stageLink('review', '复习')}
    </nav>
    <button class="deck-btn" id="btn-deck">本集词条 ${words.length}</button>
  </header>`;
}

export function isWatched(part) {
  if (!part) return false;
  if (part.progress?.completed_at) return true;
  const duration = part.duration || 0;
  return duration > 0 && (part.progress?.position || 0) / duration >= 0.95;
}
