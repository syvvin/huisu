// Library: the only navigation surface. One list, one continue card.
import { API } from '../api.js';
import { S } from '../state.js';
import { $, esc, fmtTime } from '../util.js';

const LAST_KEY = 'vocab:lastPart';

export async function mountLibrary() {
  const main = $('#main');
  if (!S.lib) {
    try { S.lib = await API.library(); } catch (err) {
      main.innerHTML = `<div class="page"><div class="lib-empty">${esc(err.message)}</div></div>`;
      return;
    }
  }
  const chapters = S.lib.chapters || [];
  if (!chapters.length) {
    renderEmpty(main);
    return;
  }
  const flat = flatten();
  const focus = pickFocus(flat);
  if (focus && !S.ui.openChapters.size) S.ui.openChapters.add(focus.chapterKey);
  render(main, focus);
}

function flatten() {
  const out = [];
  (S.lib.chapters || []).forEach((ch) => {
    ch.parts.forEach((part) => out.push({ ...part, chapterKey: ch.key, chapter: ch }));
  });
  return out;
}

function pickFocus(flat) {
  const lastId = Number(localStorage.getItem(LAST_KEY) || 0);
  const last = flat.find((p) => p.id === lastId);
  if (last && !last.watched) return last;
  return flat.find((p) => !p.watched && p.position > 0)
    || flat.find((p) => !p.watched)
    || last
    || flat[0];
}

function render(main, focus) {
  const totals = S.lib.totals || {};
  const due = totals.due || 0;
  const chapters = S.lib.chapters || [];
  const stories = libraryStories(chapters);
  main.innerHTML = `<div class="page">
    <div class="page-head">
      <h1>片库</h1>
      <span class="page-sub"><b>${totals.parts || 0}</b> 个 Part · <b>${totals.words || 0}</b> 词条</span>
    </div>

    ${due ? `<div class="due-line"><span class="dot"></span>今天有 <b style="color:var(--gold)">${due}</b> 个词待温习
      <a href="#/review">开始温习 →</a></div>` : ''}

    ${continueHtml(focus)}

    <div class="lib-tools">
      <div class="lib-search">
        <input id="lib-q" placeholder="搜索主题、章节或 Part" value="${esc(S.ui.libQuery)}">
      </div>
      <span class="stat">${stories.length} 个主题 · ${chapters.length} 章 · ${chapters.reduce((n, c) => n + c.parts.length, 0)} 集</span>
    </div>

    <div class="story-nav">
      <span class="story-nav-label">主题</span>
      ${stories.map((story) => `<button class="story-link" type="button" data-story="${esc(story.key)}">${esc(story.title_en)} <span>${story.parts} 集</span></button>`).join('')}
    </div>

    <div id="chapters">${stories.map((story) => storyHtml(story, chapters, focus)).join('')}</div>
  </div>`;

  $('#lib-q').addEventListener('input', (event) => {
    S.ui.libQuery = event.target.value;
    renderChapterList(focus);
  });
  bindStoryNav(main);
  bindChapterToggles(main, focus);
  bindParts();
}

function libraryStories(chapters) {
  if (S.lib?.stories?.length) return S.lib.stories;
  const groups = new Map();
  chapters.forEach((ch) => {
    const key = ch.story_key || 'main';
    if (!groups.has(key)) {
      groups.set(key, {
        key,
        title_en: ch.story_title_en || 'Main Story',
        title_zh: ch.story_title_zh || '主线故事',
        order: ch.story_order ?? 0,
        chapters: 0, parts: 0, words: 0, due: 0,
      });
    }
    const story = groups.get(key);
    story.chapters += 1;
    story.parts += ch.parts.length;
    story.words += ch.words || 0;
    story.due += ch.due || 0;
  });
  return [...groups.values()].sort((a, b) => a.order - b.order);
}

function continueHtml(part) {
  if (!part) return '';
  const pct = Math.round((part.progress || 0) * 100);
  const started = part.position > 3 || part.added > 0;
  const label = part.watched ? '重新观看' : started ? '继续观看' : '开始观看';
  const story = part.chapter?.story_title_en || '';
  return `<div class="continue">
    <img src="${part.thumb_url}" alt="" onerror="this.style.visibility='hidden'">
    <div>
      <div class="kicker">${part.watched ? '已看完' : started ? '继续' : '从这里开始'}${story ? ` · ${esc(story)}` : ''} · ${esc(part.chapter.label || '')} · PART ${String(part.number).padStart(2, '0')}</div>
      <h2>${esc(part.title_en)}</h2>
      <div class="zh">${esc(part.title_zh || '')} · ${fmtTime(part.duration)} · ${part.cues} 句</div>
      <div class="meta">
        <span>已收录 ${part.added} 词</span>
        <span>掌握 ${pct}%</span>
        ${part.due ? `<span style="color:var(--gold)">${part.due} 待温习</span>` : ''}
      </div>
      <div class="bar"><i style="width:${pct}%"></i></div>
    </div>
    <div class="acts">
      <a class="btn primary" href="#/p/${part.id}/watch">${label}</a>
      <a class="btn" href="#/p/${part.id}/preview">先预习</a>
    </div>
  </div>`;
}

function storyHtml(story, chapters, focus) {
  const own = chapters.filter((ch) => (ch.story_key || 'main') === story.key);
  if (!own.length) return '';
  return `<section class="story-block" data-story="${esc(story.key)}" id="story-${esc(story.key)}">
    <div class="story-head">
      <div class="story-titles">
        <span class="story-en">${esc(story.title_en)}</span>
        ${story.title_zh ? `<span class="story-zh">${esc(story.title_zh)}</span>` : ''}
      </div>
      <span class="story-meta">${story.chapters} 章 · ${story.parts} 集 · ${story.words} 词</span>
    </div>
    ${own.map((ch) => chapterHtml(ch, focus)).join('')}
  </section>`;
}

function chapterHtml(ch, focus) {
  const q = S.ui.libQuery.trim().toLowerCase();
  const open = q ? true : S.ui.openChapters.has(ch.key);
  const watched = ch.parts.filter((p) => p.watched).length;
  return `<section class="chapter-block ${open ? '' : 'closed'}" data-key="${esc(ch.key)}">
    <button class="chapter-toggle" data-chapter="${esc(ch.key)}">
      <span class="ch-label">${esc(ch.label || `CH.${ch.idx}`)}</span>
      <span class="ch-names">
        <span class="ch-en">${esc(ch.title_en)}</span>
        ${ch.title_zh ? `<span class="ch-zh">${esc(ch.title_zh)}</span>` : ''}
      </span>
      <span class="ch-meta">${ch.parts.length} 集 · ${ch.words} 词</span>
      <span class="ch-chev">▾</span>
    </button>
    <div class="lib-parts">${ch.parts.map((p) => partRow(p, ch, focus)).join('')}</div>
  </section>`;
}

function partRow(part, ch, focus) {
  const q = S.ui.libQuery.trim().toLowerCase();
  if (q) {
    const hay = `${part.title_en} ${part.title_zh} ${part.number} ${ch.title_en} ${ch.title_zh} ${ch.story_title_en || ''} ${ch.story_title_zh || ''}`.toLowerCase();
    if (!hay.includes(q)) return '';
  }
  const pct = Math.round((part.progress || 0) * 100);
  let state = '未开始';
  let cls = '';
  if (part.watched) { state = `已看完${part.added ? ` · ${pct}%` : ''}`; cls = 'done'; }
  else if (part.added) { state = `${part.added} 词 · 掌握 ${pct}%`; cls = 'done'; }
  const active = S.part?.id === part.id ? ' on' : '';
  return `<button class="lib-part${active}" data-part="${part.id}">
    <span class="pt-num">${String(part.number).padStart(2, '0')}</span>
    <span class="pt-names"><span class="pt-en">${esc(part.title_en)}<span class="pt-zh">${esc(part.title_zh || '')}</span></span></span>
    <span class="pt-state ${cls}">${part.due ? '<i class="due-dot"></i>' : ''}${state}</span>
    <span class="pt-dur">${fmtTime(part.duration)}</span>
  </button>`;
}

function renderChapterList(focus) {
  const wrap = $('#chapters');
  if (!wrap) return;
  const chapters = S.lib.chapters || [];
  const stories = libraryStories(chapters);
  wrap.innerHTML = stories.map((story) => storyHtml(story, chapters, focus)).join('');
  bindChapterToggles(wrap, focus);
  bindParts();
}

function bindChapterToggles(scope, focus) {
  scope.querySelectorAll('.chapter-toggle').forEach((btn) => {
    btn.addEventListener('click', () => {
      const key = btn.dataset.chapter;
      if (S.ui.openChapters.has(key)) S.ui.openChapters.delete(key);
      else S.ui.openChapters.add(key);
      storeOpen();
      renderChapterList(focus);
    });
  });
}

function bindStoryNav(scope) {
  scope.querySelectorAll('.story-link').forEach((btn) => {
    btn.addEventListener('click', () => {
      document.getElementById(`story-${btn.dataset.story}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  });
}

function bindParts() {
  document.querySelectorAll('.lib-part').forEach((row) => {
    row.addEventListener('click', () => {
      location.hash = `#/p/${row.dataset.part}/watch`;
    });
  });
}

function storeOpen() {
  try { localStorage.setItem('vocab:openChapters', JSON.stringify(Array.from(S.ui.openChapters))); } catch { /* ignore */ }
}

function renderEmpty(main) {
  main.innerHTML = `<div class="page">
    <div class="page-head"><h1>片库</h1></div>
    <div class="lib-empty">
      还没有找到任何 Part。<br>
      这个应用读取 uttu-extractor 已经提取好的视频与双语字幕。<br><br>
      当前片库目录：<br><span class="mono" style="color:var(--gold)">${esc(S.lib?.root || '')}</span><br><br>
      如果目录不同，在设置里修改后重新扫描。
      <div style="margin-top:16px"><button class="btn" id="empty-settings">打开设置</button></div>
    </div></div>`;
  $('#empty-settings')?.addEventListener('click', async () => {
    const { openSettings } = await import('../settings.js');
    openSettings();
  });
}
