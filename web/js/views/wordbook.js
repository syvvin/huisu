// Wordbook: one filterable table, all maintenance behind hover.
import { API } from '../api.js';
import { S } from '../state.js';
import { $, $$, esc, fmtDate, toast, boxes, wordState, confirmDialog, debounce } from '../util.js';
import { openDict } from '../dictpanel.js';

const STATUS = [['all', '全部'], ['due', '待温习'], ['new', '新词'],
  ['learning', '学习中'], ['mastered', '已掌握'], ['ignored', '已忽略']];

export async function mountWordbook() {
  const main = $('#main');
  S.part = null;
  S.words = [];
  S.added = new Set();
  if (!S.ui.wb) S.ui.wb = { q: '', status: 'all', chapter: '', offset: 0, limit: 60 };
  const selected = new Set();
  const totals = S.lib?.totals || {};

  main.innerHTML = `<div class="page">
    <div class="page-head">
      <h1>词库</h1>
      <span class="page-sub" id="wb-totals"><b>${totals.words || 0}</b> 词条 · 待温习 <b>${totals.due || 0}</b> · 已掌握 <b>${totals.mastered || 0}</b></span>
    </div>
    <div class="wb-tools">
      <div class="wb-search"><input id="wb-q" placeholder="搜索单词或释义" value="${esc(S.ui.wb.q)}"></div>
      <select class="select" id="wb-status">
        ${STATUS.map(([k, t]) => `<option value="${k}" ${S.ui.wb.status === k ? 'selected' : ''}>${t}</option>`).join('')}
      </select>
      <select class="select" id="wb-chapter">
        <option value="">全部章节</option>
        ${(S.lib?.chapters || []).map((ch) => `<option value="${esc(ch.key)}" ${S.ui.wb.chapter === ch.key ? 'selected' : ''}>${esc(ch.title_zh || ch.title_en)}</option>`).join('')}
      </select>
      <span style="flex:1"></span>
      <a class="btn sm" href="/api/export.csv" download="vocab-export.csv">导出 CSV</a>
      <a class="btn sm" href="#/review">温习到期</a>
    </div>
    <div class="wb-hint">忽略：不参与复习和「待温习」统计；词条、来源和记忆进度都保留，可随时恢复。删除才是永久移除。</div>
    <div class="wb-batch" id="wb-batch" hidden>
      <span class="wb-batch-count">已选 <b id="wb-batch-n">0</b> 个</span>
      <button class="btn sm" data-batch="ignore">忽略</button>
      <button class="btn sm" data-batch="restore">恢复</button>
      <button class="btn sm danger" data-batch="delete">删除</button>
      <button class="btn sm" data-batch="clear">取消选择</button>
    </div>
    <div id="wb-body"><div class="wb-empty">载入中…</div></div>
  </div>`;

  $('#wb-q').addEventListener('input', debounce((event) => {
    S.ui.wb.q = event.target.value; S.ui.wb.offset = 0; load();
  }, 240));
  $('#wb-status').addEventListener('change', (event) => {
    S.ui.wb.status = event.target.value; S.ui.wb.offset = 0; load();
  });
  $('#wb-chapter').addEventListener('change', (event) => {
    S.ui.wb.chapter = event.target.value; S.ui.wb.offset = 0; load();
  });
  $('#wb-batch').addEventListener('click', (event) => {
    const btn = event.target.closest('[data-batch]');
    if (!btn) return;
    const action = btn.dataset.batch;
    if (action === 'clear') {
      selected.clear();
      $$('.wb-select').forEach((box) => { box.checked = false; });
      updateBatchBar();
      return;
    }
    runBatch(action);
  });

  async function load() {
    const body = $('#wb-body');
    selected.clear();
    updateBatchBar();
    body.innerHTML = `<div class="wb-empty">载入中…</div>`;
    try {
      render(await API.wordbook({
        q: S.ui.wb.q, status: S.ui.wb.status, chapter: S.ui.wb.chapter,
        limit: S.ui.wb.limit, offset: S.ui.wb.offset,
      }));
      await refreshTotals();
    } catch (err) {
      body.innerHTML = `<div class="wb-empty">${esc(err.message)}</div>`;
    }
  }

  async function refreshTotals() {
    try {
      const lib = await API.library();
      S.lib = lib;
      const totals = lib.totals || {};
      const el = $('#wb-totals');
      if (el) {
        el.innerHTML = `<b>${totals.words || 0}</b> 词条 · 待温习 <b>${totals.due || 0}</b> · 已掌握 <b>${totals.mastered || 0}</b>`;
      }
    } catch { /* keep the current header */ }
  }

  function render(data) {
    const body = $('#wb-body');
    if (!data.items.length) {
      body.innerHTML = `<div class="wb-empty">${S.ui.wb.q ? '没有匹配的词条。' : '词库还是空的。去片库挑一集，点字幕里的单词就能收录。'}</div>`;
      return;
    }
    body.innerHTML = `<table class="wb-table">
      <thead><tr>
        <th class="wb-check"><input type="checkbox" id="wb-check-all" title="全选本页" aria-label="全选本页"></th>
        <th style="width:24%">单词</th><th style="width:38%">释义</th>
        <th style="width:16%">掌握</th><th style="width:18%"></th>
      </tr></thead>
      <tbody>${data.items.map((word) => rowHtml(word, selected)).join('')}</tbody>
    </table>
    <div class="wb-pager">
      <span>${data.offset + 1}–${Math.min(data.total, data.offset + data.items.length)} / ${data.total}</span>
      <span class="btns">
        <button class="btn sm" id="wb-prev" ${data.offset === 0 ? 'disabled' : ''}>上一页</button>
        <button class="btn sm" id="wb-next" ${data.offset + data.limit >= data.total ? 'disabled' : ''}>下一页</button>
      </span>
    </div>`;

    $$('.wb-word', body).forEach((el) => el.addEventListener('click', () => {
      openDict(el.dataset.lemma, { partId: 0 });
    }));

    $$('.wb-select', body).forEach((box) => box.addEventListener('change', () => {
      const id = Number(box.dataset.id);
      if (box.checked) selected.add(id);
      else selected.delete(id);
      updateBatchBar();
    }));
    $('#wb-check-all')?.addEventListener('change', (event) => {
      const checked = event.target.checked;
      $$('.wb-select', body).forEach((box) => {
        box.checked = checked;
        const id = Number(box.dataset.id);
        if (checked) selected.add(id);
        else selected.delete(id);
      });
      updateBatchBar();
    });
    updateBatchBar();

    $$('[data-act="ignore"]', body).forEach((btn) => btn.addEventListener('click', async () => {
      const ignored = btn.dataset.status === 'ignored';
      try {
        await API.updateWord(Number(btn.dataset.id), { status: ignored ? 'active' : 'ignored' });
        toast(ignored ? '已恢复' : '已忽略');
        load();
      } catch (err) { toast(err.message, 'err'); }
    }));

    $$('[data-act="delete"]', body).forEach((btn) => btn.addEventListener('click', async () => {
      const ok = await confirmDialog({
        title: '删除词条',
        message: `将完全删除「${btn.dataset.lemma}」及其记忆进度，不可撤销。`,
        okText: '删除', danger: true,
      });
      if (!ok) return;
      try {
        await API.removeWord(Number(btn.dataset.id), null, true);
        toast('已删除');
        load();
      } catch (err) { toast(err.message, 'err'); }
    }));

    $('#wb-prev')?.addEventListener('click', () => {
      S.ui.wb.offset = Math.max(0, S.ui.wb.offset - S.ui.wb.limit); load();
    });
    $('#wb-next')?.addEventListener('click', () => {
      S.ui.wb.offset += S.ui.wb.limit; load();
    });
  }

  function updateBatchBar() {
    const bar = $('#wb-batch');
    if (!bar) return;
    bar.hidden = selected.size === 0;
    const count = $('#wb-batch-n');
    if (count) count.textContent = String(selected.size);
    const visible = $$('.wb-select');
    const checked = visible.filter((box) => box.checked).length;
    const all = $('#wb-check-all');
    if (all) {
      all.checked = visible.length > 0 && checked === visible.length;
      all.indeterminate = checked > 0 && checked < visible.length;
    }
  }

  async function runBatch(action) {
    const ids = [...selected];
    if (!ids.length) return;
    if (action === 'delete') {
      const ok = await confirmDialog({
        title: '批量删除词条',
        message: `将完全删除选中的 ${ids.length} 个词条及其记忆进度，不可撤销。`,
        okText: '删除', danger: true,
      });
      if (!ok) return;
    }
    try {
      const res = await API.batchWords(ids, action);
      const n = res.updated ?? ids.length;
      const message = action === 'ignore' ? `已忽略 ${n} 个`
        : action === 'restore' ? `已恢复 ${n} 个` : `已删除 ${n} 个`;
      toast(message);
      selected.clear();
      await load();
    } catch (err) { toast(err.message, 'err'); }
  }

  S.cleanup = () => {};
  await load();
}

function rowHtml(word, selected) {
  const state = wordState(word);
  return `<tr data-id="${word.id}">
    <td class="wb-check"><input type="checkbox" class="wb-select" data-id="${word.id}" aria-label="选择 ${esc(word.display || word.lemma)}"${selected.has(word.id) ? ' checked' : ''}></td>
    <td>
      <div class="wb-word" data-lemma="${esc(word.lemma)}">${esc(word.display || word.lemma)}</div>
      <div class="wb-ph">${esc(word.phonetic || '')}</div>
    </td>
    <td><div class="wb-zh">${esc(word.brief_zh || '—')}</div></td>
    <td>
      ${boxes(word.box)}
      <div class="wb-due ${word.due ? 'hot' : ''}">${word.status === 'ignored' ? '已忽略' : word.due ? '待温习' : `${state.label} · ${fmtDate(word.due_at) || '—'}`}</div>
    </td>
    <td>
      <div class="wb-act">
        <button class="btn sm" data-act="ignore" data-id="${word.id}" data-status="${word.status}">${word.status === 'ignored' ? '恢复' : '忽略'}</button>
        <button class="btn sm danger" data-act="delete" data-id="${word.id}" data-lemma="${esc(word.lemma)}">删除</button>
      </div>
    </td>
  </tr>`;
}
