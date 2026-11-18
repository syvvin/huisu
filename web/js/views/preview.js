// Preview: CEFR-filtered word picking before watching. The level choice is global.
import { API } from '../api.js';
import { S } from '../state.js';
import { $, $$, esc, toast, emit } from '../util.js';
import { setWords, openDict, openDeck } from '../dictpanel.js';

const CEFR_LEVELS = ['A1', 'A2', 'B1', 'B2', 'C1', 'C2'];

export async function mountPreview(root, part) {
  let items = [];
  let available = true;
  const levels = new Set(S.settings.preview_cefr_levels || ['B1', 'B2']);
  root.innerHTML = `<p class="preview-lede">正在挑选生词…</p>`;

  async function load() {
    try {
      const data = await API.candidates(part.id);
      items = data.items || [];
      available = data.available !== false;
      const next = new Set(data.levels || []);
      if (next.size) {
        levels.clear();
        next.forEach((level) => levels.add(level));
        S.settings.preview_cefr_levels = [...levels];
      }
    } catch (err) {
      root.innerHTML = `<p class="preview-lede">${esc(err.message)}</p>`;
      return;
    }
    render();
  }

  function render() {
    const words = S.words.filter((w) => w.status !== 'ignored');
    const speakers = (part.speakers || []).slice(0, 2).map((s) => s.name);
    const who = speakers.length ? `${speakers.join(' 与 ')} 出场最多` : '本集对白';
    const levelText = CEFR_LEVELS.filter((level) => levels.has(level)).join(' / ');
    const lede = !available
      ? 'CEFR 词表还没有就绪，无法按级别筛选预习词。'
      : words.length
        ? `本集 ${part.cues} 句对白，${esc(who)}。你已经收录 <em>${words.length}</em> 个词，CEFR ${levelText} 还有 ${items.length} 个候选。`
        : `本集 ${part.cues} 句对白，${esc(who)}。按 CEFR ${levelText}，筛出 <em>${items.length}</em> 个候选。`;

    root.innerHTML = `<div class="cefr-picker">
        <span class="cefr-picker-label">CEFR</span>
        ${CEFR_LEVELS.map((level) => `<button type="button" class="cefr-chip ${levels.has(level) ? 'on' : ''}" data-level="${level}">${level}</button>`).join('')}
        <span class="cefr-picker-note">选择会保存，所有 Part 共用</span>
      </div>
      <p class="preview-lede">${lede}</p>
      <div class="preview-actions">
        <button class="btn primary" id="preview-start">开始观看 →</button>
        <span class="preview-note">已收录 <b id="picked" style="color:var(--gold);font-weight:400">${words.length}</b> 个词 · 其余可以边看边点</span>
      </div>
      <div class="cands" id="cands">
        ${items.length ? items.map(rowHtml).join('') : `<div class="cand-empty">${available ? `没有 CEFR ${levelText} 的待收录词了。` : 'CEFR 数据未就绪。'}</div>`}
      </div>`;

    $$('.cefr-chip', root).forEach((btn) => btn.addEventListener('click', () => toggleLevel(btn.dataset.level)));
    $('#preview-start').addEventListener('click', startWatching);
    $('#cands').addEventListener('click', onCandidateClick);
  }

  async function toggleLevel(level) {
    if (levels.has(level)) {
      if (levels.size === 1) {
        toast('至少保留一个 CEFR 级别', 'err');
        return;
      }
      levels.delete(level);
    } else {
      levels.add(level);
    }
    const chosen = CEFR_LEVELS.filter((item) => levels.has(item));
    try {
      await API.saveSettings({ preview_cefr_levels: chosen });
      S.settings.preview_cefr_levels = chosen;
      await load();
    } catch (err) { toast(err.message, 'err'); }
  }

  function rowHtml(cand) {
    const cue = part.subtitles[cand.cue];
    return `<div class="cand" data-lemma="${esc(cand.lemma)}" data-cue="${cand.cue}">
      <div>
        <div><span class="cand-level level-${esc(cand.level || '')}">${esc(cand.level || '—')}</span><span class="word">${esc(cand.display || cand.lemma)}</span><span class="ph">${esc(cand.phonetic || '')}</span></div>
        <div class="gloss">${esc(cand.brief_zh || '暂无释义')}</div>
      </div>
      <div class="ctx">${cue ? excerpt(cue, cand.display || cand.lemma) : ''}</div>
      <button class="add" title="收进本集">＋</button>
    </div>`;
  }

  async function onCandidateClick(event) {
    const row = event.target.closest('.cand');
    if (!row) return;
    if (event.target.closest('.add')) {
      try {
        const res = await API.addWord({
          part_id: part.id, lemma: row.dataset.lemma, cue: Number(row.dataset.cue),
        });
        setWords(res.words || []);
        if (S.panel === 'deck') openDeck();
        emit('library-stale');
        row.classList.add('added');
        row.querySelector('.add').textContent = '✓';
        row.querySelector('.add').disabled = true;
        $('#picked').textContent = String(S.words.filter((w) => w.status !== 'ignored').length);
        toast(`已收录 ${row.dataset.lemma}`);
      } catch (err) { toast(err.message, 'err'); }
      return;
    }
    openDict(row.dataset.lemma, { cue: Number(row.dataset.cue), partId: part.id });
  }

  function startWatching() {
    if (!part.progress.previewed_at) {
      part.progress.previewed_at = new Date().toISOString();
      API.progress({ part_id: part.id, event: 'preview' }).catch(() => {});
    }
    location.hash = `#/p/${part.id}/watch`;
  }

  await load();
}

function excerpt(cue, display) {
  const text = cue.en || '';
  const target = display || '';
  const idx = target ? text.toLowerCase().indexOf(target.toLowerCase()) : -1;
  const spk = cue.speaker ? `<span class="spk">${esc(cue.speaker)}</span>` : '';
  if (idx < 0) return spk + esc(text);
  return `${spk}${esc(text.slice(0, idx))}<em>${esc(text.slice(idx, idx + target.length))}</em>${esc(text.slice(idx + target.length))}`;
}
