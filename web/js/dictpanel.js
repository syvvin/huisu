// Right drawer: the part word deck, or one dictionary entry. Hidden until asked for.
import { API } from './api.js';
import { S } from './state.js';
import {
  $, $$, esc, toast, boxes, wordState, tagHtml, srcLabel, pronounce, emit, fmtTime,
} from './util.js';
import { captureReturnView } from './navigation.js';

let dictSeq = 0;
let wordHistory = [];

/* --------------------------------------------------------------- words */

export function setWords(words) {
  S.words = words || [];
  S.added = new Set(S.words.map((w) => w.lemma));
  if (S.part) S.part.words = S.words;
  const count = S.words.filter((w) => w.status !== 'ignored').length;
  const deckBtn = document.querySelector('#btn-deck');
  if (deckBtn) deckBtn.textContent = `本集词条 ${count}`;
  emit('words-changed', S.words);
}

function counts() {
  const active = (S.words || []).filter((w) => w.status !== 'ignored');
  return {
    total: active.length,
    mastered: active.filter((w) => w.box >= 4).length,
    due: active.filter((w) => w.due).length,
  };
}

/* --------------------------------------------------------------- panel */

function panelEl() { return $('#panel'); }

function stopInlineVideos() {
  panelEl().querySelectorAll('video.src-player').forEach((video) => {
    try {
      video.pause();
      video.removeAttribute('src');
      video.load();
    } catch { /* ignore */ }
  });
}

function showPanel() {
  panelEl().classList.add('open');
  document.getElementById('app').classList.add('panel-open');
  $('#scrim').classList.add('show');
}

export function closePanel() {
  wordHistory = [];
  if (!S.panel) return;
  S.panel = null;
  S.dict = null;
  S.dictQuery = null;
  S.dictMore = false;
  const panel = panelEl();
  stopInlineVideos();
  panel.classList.remove('open');
  panel.innerHTML = '';
  panel.setAttribute('aria-hidden', 'true');
  document.getElementById('app').classList.remove('panel-open');
  $('#scrim').classList.remove('show');
}

function shell(title, body, foot, backAction) {
panelEl().innerHTML = `<div class="panel-inner">
    <div class="panel-head">
      ${backAction ? '<button id="pp-back" title="返回词条">←</button>' : ''}
      <span class="pt">${esc(title)}</span>
      <span class="spacer"></span>
      <button class="close" id="pp-close" title="关闭">✕</button>
    </div>
    <div class="panel-body">${body}</div>
    ${foot || ''}
  </div>`;
  $('#pp-close').addEventListener('click', closePanel);
  if (backAction) $('#pp-back').addEventListener('click', backAction);
  showPanel();
  panelEl().setAttribute('aria-hidden', 'false');
}

/* ---------------------------------------------------------------- deck */

export function openDeck() {
  if (!S.part) return;
  wordHistory = [];
  S.panel = 'deck';
  S.dict = null;
  S.dictQuery = null;
  renderDeck();
}

function renderDeck() {
  const c = counts();
  const rows = (S.words || []).map((word) => {
    const state = wordState(word);
    return `<button class="deck-row" data-id="${word.id}" data-lemma="${esc(word.lemma)}" data-cue="${word.cue == null ? '' : word.cue}">
      <div class="top"><span class="wd">${esc(word.display || word.lemma)}</span>
        <span class="ph">${esc(word.phonetic || '')}</span></div>
      <div class="zh">${esc(word.brief_zh || '暂无释义')}</div>
      <div class="foot">${boxes(word.box)}<span class="state ${state.cls}">${state.label}</span>
        ${word.note ? '<span class="state" title="有备注">✎</span>' : ''}</div>
    </button>`;
  }).join('');

  const body = c.total
    ? `<div class="deck-sum"><span>掌握 ${c.mastered} · 待温习 ${c.due}</span><span>共 ${c.total}</span></div>${rows}`
    : `<div class="panel-empty">本集还没有词条。<br>播放时点字幕里的单词，或先去预习挑几个。</div>`;
  const foot = S.part ? `<div class="panel-foot">
      <a class="btn ${c.total ? 'primary' : ''} block" href="#/p/${S.part.id}/review" id="pp-review" ${c.total ? '' : 'style="opacity:.5"'}>温习本集</a>
    </div>` : '';
  shell('本集词条', body, foot);

  $$('.deck-row', panelEl()).forEach((row) => {
    row.addEventListener('click', () => {
      const cue = row.dataset.cue === '' ? null : Number(row.dataset.cue);
      if (cue !== null) emit('seek-cue', { cue });
      openDict(row.dataset.lemma, { cue, partId: S.part?.id });
    });
  });
}

/* ---------------------------------------------------------------- dict */

export async function openDict(lemma, opts = {}) {
  if (!lemma) return;
  if (!opts.fromChip && !opts.preserveHistory) wordHistory = [];
  if (S.settings.pronounce_auto && opts.auto !== false) {
    pronounce(lemma, S.settings.pronounce_accent || 'us');
  }
  const seq = ++dictSeq;
  S.panel = 'dict';
  S.dictMore = false;
  S._dictRendered = null;
  S.dictLoading = true;
  S.dictError = '';
  S.dict = null;
  S.dictQuery = { lemma, cue: opts.cue ?? null, start: opts.start, end: opts.end, partId: opts.partId ?? S.part?.id ?? 0 };
  renderDict();
  try {
    const data = await API.dict(lemma, S.dictQuery.partId, S.dictQuery.cue);
    if (seq !== dictSeq) return;
    S.dict = data;
  } catch (err) {
    if (seq !== dictSeq) return;
    S.dictError = err.message;
  }
  S.dictLoading = false;
  if (seq === dictSeq) renderDict();
}

function renderDict() {
  const q = S.dictQuery;
  if (!q) return;
  const canAdd = Boolean(q.partId) && S.part?.id === q.partId;
  const word = canAdd ? (S.words || []).find((w) => w.lemma === q.lemma) : null;
  const hasHistory = wordHistory.length > 0;
  const back = hasHistory ? backWord : (canAdd ? openDeck : null);
  const same = S._dictRendered === q.lemma;
  const prevTop = same ? (panelEl().querySelector('.panel-body')?.scrollTop || 0) : 0;
  stopInlineVideos();
  shell('词典', dictBody(q.lemma, word), '', back);
  S._dictRendered = q.lemma;
  const body = panelEl().querySelector('.panel-body');
  if (body && prevTop) body.scrollTop = prevTop;
  panelEl().querySelector('#pp-back')?.setAttribute('title', hasHistory ? '返回上一个词' : '返回本集词条');

  panelEl().querySelectorAll('.pron-btn').forEach((btn) => btn.addEventListener('click', () => pronounce(q.lemma, btn.dataset.accent)));
  $('#dict-add')?.addEventListener('click', addCurrent);
  $('#dict-add-cue')?.addEventListener('click', addCurrent);
  $('#dict-remove')?.addEventListener('click', async () => {
    if (!word) return;
    try {
      const res = await API.removeWord(word.id, q.partId);
      setWords(res.words || []);
      emit('library-stale');
      if (res.result?.deleted) toast('已从本 Part 移除；该词没有其他来源，词条已删除');
      else toast(`已从本 Part 移除；仍有 ${res.result?.remaining ?? 0} 个来源`);
      await openDict(q.lemma, { partId: q.partId, cue: q.cue, auto: false, preserveHistory: true });
    } catch (err) { toast(err.message, 'err'); }
  });
  $('#dict-jump')?.addEventListener('click', (event) => {
    event.preventDefault();
    jumpTo(q.partId, q.cue, q.start);
  });
  panelEl().querySelectorAll('.src-play').forEach((btn) => btn.addEventListener('click', (event) => {
    event.stopPropagation();
    toggleSourceVideo(btn);
  }));
  panelEl().querySelectorAll('.src-jump').forEach((btn) => btn.addEventListener('click', (event) => {
    event.stopPropagation();
    const row = btn.closest('.src-row');
    jumpTo(Number(row?.dataset.part), Number(row?.dataset.cue), Number(row?.dataset.start));
  }));
  const note = $('#dict-note');
  const noteWord = word || S.dict?.status;
  if (note && noteWord) {
    note.addEventListener('blur', async () => {
      const value = note.value.trim().slice(0, 500);
      if (value === (noteWord.note || '')) return;
      try {
        await API.updateWord(noteWord.id, { note: value });
        noteWord.note = value;
        if (word) word.note = value;
        toast('备注已保存');
      } catch (err) { toast(err.message, 'err'); }
    });
  }
}

function dictBody(lemma, word) {
  if (S.dictLoading) {
    return `<div class="dict-word">${esc(lemma)}</div><div class="dict-sec">${'<div class="sk"></div>'.repeat(4)}</div>`;
  }
  if (S.dictError && !S.dict) {
    return `<div class="dict-word">${esc(lemma)}</div>
      <div class="dict-sec"><div class="panel-empty">查询失败：${esc(S.dictError)}</div></div>`;
  }
  const d = S.dict || {};
  const providers = (d.provider_results || []).filter((p) => p && p.id);
  const lookupOrder = S.settings?.lookup_order || ['youdao', 'ecdict', 'freedict', 'cefr', 'note', 'sources'];
  providers.sort((a, b) => {
    const ai = lookupOrder.indexOf(a.id);
    const bi = lookupOrder.indexOf(b.id);
    return (ai < 0 ? 99 : ai) - (bi < 0 ? 99 : bi);
  });
  const configured = S.settings?.dict_sections || {};

  const sectionsFor = (provider) => {
    const chosen = configured[provider.id];
    const all = (provider.sections || []).map((s) => s.key);
    return new Set(Array.isArray(chosen) ? chosen.filter((key) => all.includes(key)) : all);
  };
  const providerOn = (provider, key) => sectionsFor(provider).has(key);
  const sourceStatus = (source) => {
    if (!source) return '';
    if (source.ok === false) return '失败';
    if (source.stale || source.cached) return '缓存';
    return source.ms != null ? `${source.ms}ms` : '';
  };

  const cefrProvider = providers.find((p) => p.id === 'cefr');
  const cefr = cefrProvider && providerOn(cefrProvider, 'level') ? (cefrProvider.data || {}).cefr : null;
  const cefrBadge = cefr?.level ? `<span class="cefr-badge level-${esc(cefr.level)}">${esc(cefr.level)}</span>` : '';
  const wordSources = d.word_sources || [];
  const focusPart = Number(d.focus?.part_id) || 0;
  const focusCue = d.focus?.cue;
  const currentSource = wordSources.find((s) => s.part_id === focusPart && s.cue === focusCue);
  const known = word || (!S.dictQuery.partId ? d.status : null);
  const noteWord = word || d.status;
  const actions = known
    ? `<span class="tag gold">已收录 · 掌握 ${known.box}/5</span>
       ${word && !currentSource ? '<button class="btn sm" id="dict-add-cue">＋ 收录这句话</button>' : ''}
       ${word ? '<button class="btn sm danger" id="dict-remove" style="margin-left:auto" title="只移除本 Part 中包含这个词的句子，不影响其他 Part">在本 Part 中移除</button>' : ''}`
    : S.dictQuery.partId
      ? `<button class="btn primary sm" id="dict-add">＋ 收进本集</button>`
      : '<span class="dim">在某一集里打开时才能收录</span>';

  const accent = S.settings.pronounce_accent || 'us';
  let html = `<div class="dict-top"><span class="dict-word">${esc(lemma)}</span>
      ${cefrBadge}
      <span class="pron-btns">
        <button class="pron-btn ${accent === 'uk' ? 'default' : ''}" data-accent="uk" title="英式发音">英 ♪</button>
        <button class="pron-btn ${accent === 'us' ? 'default' : ''}" data-accent="us" title="美式发音">美 ♪</button>
      </span></div>
    <div class="dict-actions">${actions}</div>`;

  const blocks = new Map();
  for (const p of providers) {
    const keys = sectionsFor(p);
    const sections = (p.sections || []).filter((s) => keys.has(s.key));
    if (!sections.length) continue;
    const data = p.data || {};
    const body = sections.map((sec) => {
      const content = providerSectionHtml(p.id, sec.key, data, lemma);
      if (!content) return '';
      return `<div class="dp-sec"><div class="dp-label">${esc(sec.label)}</div>${content}</div>`;
    }).join('');
    const failed = p.source && p.source.ok === false;
    if (!body && !failed) continue;
    const status = sourceStatus(p.source);
    blocks.set(p.id, `<article class="dp-card">
      <div class="dp-head">
        <span class="dp-name">${esc(p.name)}</span>
        <span class="dp-kind">${p.kind === 'local' ? '离线' : p.kind === 'analysis' ? '分析' : '在线'}</span>
        ${status ? `<span class="dp-status">${esc(status)}</span>` : ''}
      </div>
      ${failed ? `<div class="dp-sec"><div class="dp-error">${esc(p.source.error || '查询失败')}</div></div>` : body}
    </article>`);
  }

  if (noteWord && S.settings?.note_visible !== false) {
    blocks.set('note', `<article class="dp-card lookup-note">
      <div class="dp-head"><span class="dp-name">我的备注</span></div>
      <div class="dp-sec"><textarea class="note" id="dict-note" maxlength="500" placeholder="一句记忆线索、同义词或剧情关联…">${esc(noteWord.note || '')}</textarea></div>
    </article>`);
  }

  const sourceCard = sourcesHtml(d, lemma, currentSource);
  if (sourceCard) blocks.set('sources', sourceCard);

  let rendered = 0;
  for (const key of lookupOrder) {
    const block = blocks.get(key);
    if (!block) continue;
    html += block;
    rendered += 1;
    blocks.delete(key);
  }
  for (const block of blocks.values()) {
    html += block;
    rendered += 1;
  }
  if (!rendered) {
    html += `<div class="dict-sec"><div class="panel-empty">没有可显示的内容。到设置里检查词典和内容勾选。</div></div>`;
  }

  const sources = (d.sources || []).map((s) => {
    const state = s.ok ? (s.stale ? '缓存' : s.cached ? '缓存' : `${s.ms}ms`) : '失败';
    return `${s.name} ${state}`;
  }).join(' · ');
  if (sources) html += `<div class="dict-sources">${esc(sources)}</div>`;
  html += `<div class="dict-links">
      <a href="${esc(d.links?.bing || '#')}" target="_blank" rel="noreferrer">必应</a>
      <a href="${esc(d.links?.eudic || '#')}" target="_blank" rel="noreferrer">欧陆</a>
      <a href="${esc(d.links?.youdao || '#')}" target="_blank" rel="noreferrer">有道</a>
    </div>`;
  return html;
}

function providerSectionHtml(providerId, key, data, lemma) {
  if (key === 'phonetic') {
    const ph = data.phonetic || {};
    const parts = [];
    if (ph.uk) parts.push(`<span><span class="k">英</span> ${esc(ph.uk)}</span>`);
    if (ph.us) parts.push(`<span><span class="k">美</span> ${esc(ph.us)}</span>`);
    if (!ph.uk && !ph.us && ph.default) parts.push(`<span>${esc(ph.default)}</span>`);
    return parts.length ? `<div class="dp-ph">${parts.join('')}</div>` : '';
  }
  if (key === 'pos_groups') {
    const groups = (data.pos_groups || []).slice(0, 4);
    if (!groups.length) return '';
    return groups.map((g) => `<div class="pos-group">
      <div class="pos">${esc(g.pos || '—')}</div>
      <div class="means">${(g.means || []).slice(0, 4).map((m) => `<span>${esc(m)}</span>`).join('')}</div>
    </div>`).join('');
  }
  if (key === 'definitions_en') {
    const defs = (data.definitions_en || []).slice(0, 6);
    if (!defs.length) return '';
    return defs.map((x) => `<div class="dp-def"><b>${esc(x.pos || '•')}</b>${esc(x.text)}</div>`).join('');
  }
  if (key === 'forms') {
    const forms = (data.forms || []).slice(0, 10);
    return forms.length ? `<div class="chips">${forms.map((f) => `<span class="tag">${esc(f.name || '')} ${esc(f.value || '')}</span>`).join('')}</div>` : '';
  }
  if (key === 'tags') {
    const tags = [];
    for (const tag of (data.tags || []).slice(0, 8)) {
      if (!tags.includes(tag)) tags.push(tag);
    }
    const bits = tags.map((tag) => `<span class="tag">${esc(tag)}</span>`);
    if (data.collins) bits.push(`<span class="stars">${'★'.repeat(data.collins)}${'☆'.repeat(Math.max(0, 5 - data.collins))}</span>`);
    if (data.oxford) bits.push('<span class="tag gold">牛津核心</span>');
    return bits.length ? `<div class="chips">${bits.join('')}</div>` : '';
  }
  if (key === 'rank') {
    const rank = data.rank || {};
    const bits = [];
    if (rank.coca) bits.push(`<span class="tag">COCA ${Number(rank.coca).toLocaleString()}</span>`);
    if (rank.bnc) bits.push(`<span class="tag">BNC ${Number(rank.bnc).toLocaleString()}</span>`);
    return bits.length ? `<div class="chips">${bits.join('')}</div>` : '';
  }
  if (key === 'etymology') {
    return data.etymology ? `<div class="etym">${esc(data.etymology)}</div>` : '';
  }
  if (key === 'roots') {
    const roots = (data.roots || []).filter((r) => r.word && r.word !== lemma).slice(0, 12);
    return roots.length ? `<div class="chips">${roots.map((r) => `<button class="chip" data-lemma="${esc(r.word)}">${esc(r.word)}</button>`).join('')}</div>` : '';
  }
  if (key === 'synonyms') {
    const syn = (data.synonyms || []).filter((s) => s.word).slice(0, 12);
    return syn.length ? `<div class="chips">${syn.map((s) => `<button class="chip" data-lemma="${esc(s.word)}">${esc(s.word)}</button>`).join('')}</div>` : '';
  }
  if (key === 'examples') {
    const examples = (data.examples || []).slice(0, 4);
    return examples.length ? examples.map((x) => `<div class="ex">
      <div class="en">${highlight(x.en, lemma)}</div>
      ${x.zh ? `<div class="zh">${esc(x.zh)}</div>` : ''}
      ${x.source ? `<div class="src">${esc(x.source)}</div>` : ''}
    </div>`).join('') : '';
  }
  if (key === 'exam') {
    const exam = (data.exam || []).slice(0, 3);
    return exam.length ? exam.map((x) => `<div class="ex">
      <div class="en">${highlight(x.en, lemma)}</div>
      ${x.zh ? `<div class="zh">${esc(x.zh)}</div>` : ''}
      ${x.source ? `<div class="src">${esc(x.source)}</div>` : ''}
    </div>`).join('') : '';
  }
  if (key === 'level') {
    const level = data.cefr || {};
    if (!level.level) return '';
    return `<div class="cefr-line"><span class="cefr-badge level-${esc(level.level)}">${esc(level.level)}</span><span class="dp-muted">整体级别</span></div>`;
  }
  if (key === 'pos_levels') {
    const levels = (data.cefr || {}).pos_levels || [];
    if (!levels.length) return '';
    return `<div class="chips">${levels.map((x) => `<span class="tag">${esc(x.pos || '?')} ${esc(x.level || '')}</span>`).join('')}</div>`;
  }
  if (key === 'frequency') {
    const frequency = (data.cefr || {}).frequency;
    return frequency ? `<div class="dp-muted">语料词频 ${Number(frequency).toLocaleString()}</div>` : '';
  }
  return '';
}

function highlight(text, lemma) {
  if (!text) return '';
  const escaped = esc(text);
  const target = esc(lemma);
  const idx = escaped.toLowerCase().indexOf(target.toLowerCase());
  if (idx < 0) return escaped;
  return `${escaped.slice(0, idx)}<b>${escaped.slice(idx, idx + target.length)}</b>${escaped.slice(idx + target.length)}`;
}

function sourcesHtml(d, lemma, currentSource) {
  const list = d.word_sources || [];
  const focusPart = Number(d.focus?.part_id) || 0;
  const focusCue = d.focus?.cue;
  const hasContext = Boolean(d.context && !currentSource);
  if (!list.length && !hasContext) return '';
  const sourceOrder = S.settings?.source_order || 'current';
  const baseOrder = (a, b) => (a.story_order ?? 0) - (b.story_order ?? 0)
    || (a.chapter_idx ?? 0) - (b.chapter_idx ?? 0)
    || (a.part_number ?? 0) - (b.part_number ?? 0)
    || (a.cue ?? 0) - (b.cue ?? 0);
  const ordered = [...list];
  if (sourceOrder === 'recent') {
    ordered.sort((a, b) => String(b.added_at || '').localeCompare(String(a.added_at || '')) || baseOrder(a, b));
  } else if (sourceOrder === 'story') {
    ordered.sort(baseOrder);
  } else {
    ordered.sort((a, b) => (a.part_id === focusPart ? 0 : 1) - (b.part_id === focusPart ? 0 : 1) || baseOrder(a, b));
  }
  const stories = [];
  const storyIndex = new Map();
  const chapterIndex = new Map();
  const partIndex = new Map();
  for (const s of ordered) {
    const storyKey = s.story_key || 'main';
    let story = storyIndex.get(storyKey);
    if (!story) {
      story = {
        key: storyKey, title_en: s.story_title_en || 'Main Story',
        title_zh: s.story_title_zh || '', order: s.story_order ?? 0, chapters: [],
      };
      storyIndex.set(storyKey, story);
      stories.push(story);
    }
    const chapterKey = `${storyKey}|${s.chapter_key || ''}`;
    let chapter = chapterIndex.get(chapterKey);
    if (!chapter) {
      chapter = {
        key: s.chapter_key || '', label: s.chapter_label || '',
        title_en: s.chapter_title_en || '', title_zh: s.chapter_title_zh || '', parts: [],
      };
      chapterIndex.set(chapterKey, chapter);
      story.chapters.push(chapter);
    }
    let part = partIndex.get(s.part_id);
    if (!part) {
      part = {
        part_id: s.part_id, number: s.part_number,
        title_en: s.part_title_en || '', title_zh: s.part_title_zh || '', rows: [],
      };
      partIndex.set(s.part_id, part);
      chapter.parts.push(part);
    }
    part.rows.push(s);
  }
  const focusGroup = focusPart ? [...partIndex.values()].find((p) => p.part_id === focusPart) : null;
  const rowHtml = (s) => {
    const isCurrent = s.part_id === focusPart && s.cue === focusCue;
    return `<div class="src-row${isCurrent ? ' current' : ''}" data-part="${s.part_id}" data-cue="${s.cue}" data-start="${s.start ?? ''}" data-end="${s.end ?? ''}">
      <div class="src-head">
        <span class="src-time">${s.start != null ? fmtTime(s.start) : ''}</span>
        ${s.speaker ? `<span class="src-spk">${esc(s.speaker)}</span>` : ''}
        ${isCurrent ? '<span class="src-now">本次</span>' : ''}
        <span class="src-actions">
          <button class="src-play" type="button">▶ 播放</button>
          <button class="src-jump" type="button">跳转视频</button>
        </span>
      </div>
      <div class="src-en">${highlight(s.en, lemma)}</div>
      ${s.zh ? `<div class="src-zh">${esc(s.zh)}</div>` : ''}
      <div class="src-video"></div>
    </div>`;
  };
  const partTitle = (p) => `PART ${String(p.number || 0).padStart(2, '0')} · ${esc(p.title_en)}${p.title_zh ? ` <span class="zh">${esc(p.title_zh)}</span>` : ''}`;
  const partHtml = (p) => {
    const isFocus = p.part_id === focusPart;
    return `<div class="src-part${isFocus ? ' focus' : ''}">
      <div class="src-part-head">${partTitle(p)}<span class="src-part-count">${p.rows.length} 句</span></div>
      ${p.rows.map(rowHtml).join('')}
    </div>`;
  };
  const chapterHtml = (c) => `<div class="src-chapter">
    <div class="src-chapter-head">${c.label ? `${esc(c.label)} · ` : ''}${esc(c.title_en)}${c.title_zh ? ` <span class="zh">${esc(c.title_zh)}</span>` : ''}</div>
    ${c.parts.map(partHtml).join('')}
  </div>`;
  const storyHtml = (s) => `<div class="src-story">
    <div class="src-story-head">${esc(s.title_en)}${s.title_zh ? ` <span class="zh">${esc(s.title_zh)}</span>` : ''}</div>
    ${s.chapters.map(chapterHtml).join('')}
  </div>`;
  let inner = '';
  if (hasContext) {
    inner += `<div class="src-current">
      <div class="src-part-head">当前句 <span class="src-time">${esc(fmtTime(d.context.start))}</span></div>
      <div class="src-en">${highlight(d.context.en, lemma)}</div>
      ${d.context.zh ? `<div class="src-zh">${esc(d.context.zh)}</div>` : ''}
      <a class="dict-link" href="#" id="dict-jump">跳到这一句 →</a>
    </div>`;
  }
  if (focusPart && !focusGroup) inner += '<div class="src-note">本集还没有收录这个句子</div>';
  inner += stories.map(storyHtml).join('');
  const status = list.length
    ? `${list.length} 句 · ${stories.length} 主题 · ${chapterIndex.size} 章 · ${partIndex.size} 集`
    : '本集未收录';
  return `<article class="dp-card lookup-sources">
    <div class="dp-head"><span class="dp-name">来源句子</span><span class="dp-status">${status}</span></div>
    <div class="src-body">${inner}</div>
  </article>`;
}

function toggleSourceVideo(btn) {
  const row = btn.closest('.src-row');
  const wrap = row?.querySelector('.src-video');
  if (!row || !wrap) return;
  let video = wrap.querySelector('video');
  if (!video) {
    const start = Number.isFinite(Number(row.dataset.start)) ? Math.max(0, Number(row.dataset.start)) : 0;
    const end = Number(row.dataset.end);
    const head = document.createElement('div');
    head.className = 'src-video-head';
    const range = document.createElement('span');
    range.className = 'src-video-range';
    range.textContent = `本句 ${fmtTime(start)}${Number.isFinite(end) && end > start ? ` – ${fmtTime(end)}` : ''}`;
    const again = document.createElement('button');
    again.type = 'button';
    again.className = 'src-again';
    again.textContent = '↺ 回到本句';
    head.append(range, again);
    wrap.appendChild(head);
    video = document.createElement('video');
    video.className = 'src-player';
    video.controls = true;
    video.playsInline = true;
    video.preload = 'metadata';
    video.src = `/media/${row.dataset.part}`;
    wrap.appendChild(video);
    wrap.classList.add('on');
    let armed = Number.isFinite(end) && end > start;
    let stoppedAtSentence = false;
    video.addEventListener('loadedmetadata', () => {
      video.currentTime = start;
      if (S.player?.video && !S.player.video.paused) S.player.video.pause();
      video.play().catch(() => {});
    });
    video.addEventListener('timeupdate', () => {
      if (!armed || !Number.isFinite(end) || end <= start) return;
      if (video.currentTime < end) return;
      armed = false;
      stoppedAtSentence = true;
      video.currentTime = end;
      video.pause();
      btn.textContent = '▶ 继续';
    });
    video.addEventListener('seeking', () => {
      // Dragging past the sentence end is an explicit request to keep watching.
      if (armed && Number.isFinite(end) && video.currentTime >= end) armed = false;
    });
    video.addEventListener('play', () => {
      stoppedAtSentence = false;
      btn.textContent = '暂停';
    });
    video.addEventListener('pause', () => {
      btn.textContent = stoppedAtSentence ? '▶ 继续' : '▶ 播放';
    });
    video.addEventListener('ended', () => {
      video.currentTime = start;
      armed = true;
      stoppedAtSentence = false;
      btn.textContent = '▶ 播放';
    });
    video.addEventListener('error', () => toast('片段加载失败', 'err'));
    again.addEventListener('click', (event) => {
      event.stopPropagation();
      if (S.player?.video && !S.player.video.paused) S.player.video.pause();
      armed = true;
      stoppedAtSentence = false;
      video.currentTime = start;
      video.play().catch(() => {});
    });
    return;
  }
  if (video.paused) video.play().catch(() => {});
  else video.pause();
}

async function backWord() {
  if (!wordHistory.length) return;
  const prev = wordHistory.pop();
  await openDict(prev.lemma, {
    partId: prev.partId,
    cue: prev.cue,
    start: prev.start,
    end: prev.end,
    auto: false,
    preserveHistory: true,
  });
  const body = panelEl().querySelector('.panel-body');
  if (body) body.scrollTop = prev.scroll || 0;
}

function jumpTo(partId, cue, start) {
  if (!partId) return;
  if (S.part?.id === partId && location.hash.includes('/watch')) {
    closePanel();
    emit('seek-cue', { cue });
    return;
  }
  captureReturnView();
  closePanel();
  S.pendingSeek = { partId, cue, start: Number.isFinite(start) ? start : null };
  location.hash = `#/p/${partId}/watch`;
}

async function addCurrent() {
  const q = S.dictQuery;
  if (!q?.partId) return;
  const existed = (S.words || []).some((w) => w.lemma === q.lemma);
  const payload = { part_id: q.partId, lemma: q.lemma, cue: q.cue };
  if (q.cue !== null && S.part?.subtitles) {
    const cue = S.part.subtitles.find((c) => c.i === q.cue);
    const hit = cue?.tokens?.find((t) => t[2] === q.lemma);
    if (hit) { payload.start = hit[0]; payload.end = hit[1]; }
  }
  try {
    const res = await API.addWord(payload);
    setWords(res.words || []);
    emit('library-stale');
    await openDict(q.lemma, { partId: q.partId, cue: q.cue, start: q.start, end: q.end, auto: false, preserveHistory: true });
    toast(existed ? '已加入这句话' : '已收进本集');
  } catch (err) { toast(err.message, 'err'); }
}

/* chips open another entry without leaving the panel */
document.addEventListener('click', (event) => {
  const chip = event.target.closest('.chip[data-lemma]');
  if (!chip) return;
  const q = S.dictQuery;
  if (S.panel === 'dict' && q) {
    wordHistory.push({
      lemma: q.lemma,
      partId: q.partId,
      cue: q.cue,
      start: q.start,
      end: q.end,
      scroll: panelEl().querySelector('.panel-body')?.scrollTop || 0,
    });
  }
  openDict(chip.dataset.lemma, {
    partId: S.dictQuery?.partId ?? S.part?.id,
    fromChip: true,
  });
});

document.addEventListener('click', (event) => {
  if (event.target.id === 'scrim') closePanel();
});

window.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && S.panel && !$('#modal-root').classList.contains('on')) closePanel();
});
