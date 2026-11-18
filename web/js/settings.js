// Settings: dictionary engines/content, CEFR preview filter, library location, data.
import { API } from './api.js';
import { S, applySettings } from './state.js';
import { $, $$, esc, toast, confirmDialog } from './util.js';
import { refreshLibrary } from './main.js';

const CEFR_LEVELS = ['A1', 'A2', 'B1', 'B2', 'C1', 'C2'];

export async function openSettings() {
  const root = $('#modal-root');
  root.classList.add('on');
  root.innerHTML = `<div class="modal"><div class="modal-head"><b>设置</b><button id="set-close">✕</button></div>
    <div class="modal-body"><div class="wb-empty">载入中…</div></div></div>`;

  let settings;
  let stateInfo = null;
  try {
    settings = await API.settings();
    try { stateInfo = await API.stateInfo(); } catch { /* older server */ }
  } catch (err) {
    root.querySelector('.modal-body').innerHTML = `<div class="wb-empty">${esc(err.message)}</div>`;
    return;
  }
  const providerMeta = settings.providers_meta || [];
  const providerById = new Map(providerMeta.map((p) => [p.id, p]));
  const sections = {};
  providerMeta.forEach((p) => {
    const chosen = settings.dict_sections?.[p.id];
    sections[p.id] = new Set(Array.isArray(chosen) ? chosen : (p.sections || []).map((s) => s.key));
  });
  const lookupKeys = [...providerById.keys(), 'note', 'sources'];
  const lookupOrder = (settings.lookup_order || []).filter((key) => lookupKeys.includes(key));
  lookupKeys.forEach((key) => { if (!lookupOrder.includes(key)) lookupOrder.push(key); });
  const sourceOrder = settings.source_order || 'current';
  const noteVisible = settings.note_visible !== false;
  const kindLabel = (p) => p.kind === 'local' ? '离线' : p.kind === 'analysis' ? '分析' : '在线';
  const blockArrows = (key, index) => `<span class="pb-actions">
    <button type="button" data-block-move="up" data-key="${key}" title="上移" ${index === 0 ? 'disabled' : ''}>↑</button>
    <button type="button" data-block-move="down" data-key="${key}" title="下移" ${index === lookupOrder.length - 1 ? 'disabled' : ''}>↓</button>
  </span>`;
  const blockHtml = (key, index) => {
    if (key === 'note') {
      return `<div class="panel-block ${noteVisible ? '' : 'inactive'}" data-key="note">
        <div class="pb-head"><span class="pb-name">我的备注</span><span class="pb-kind">板块</span><span class="pb-status" id="note-visibility">${noteVisible ? '显示' : '不显示'}</span>${blockArrows(key, index)}</div>
        <div class="pb-body">
          <label class="setting-check"><input type="checkbox" id="set-note-visible" ${noteVisible ? 'checked' : ''}> 在查词面板显示我的备注</label>
          <div class="desc" style="margin-top:6px">关闭只隐藏这个板块；备注内容仍保存在 state.json。</div>
        </div>
      </div>`;
    }
    if (key === 'sources') {
      return `<div class="panel-block" data-key="sources">
        <div class="pb-head"><span class="pb-name">来源句子</span><span class="pb-kind">板块</span><span class="pb-status"></span>${blockArrows(key, index)}</div>
        <div class="pb-body">
          <div class="desc">所有 Part 默认展开，当前句始终高亮；这里只调整各 Part 的排列方式。</div>
          <select class="select" id="set-source-order">
            <option value="current" ${sourceOrder === 'current' ? 'selected' : ''}>当前 Part 优先</option>
            <option value="story" ${sourceOrder === 'story' ? 'selected' : ''}>按主题 / 章节 / Part</option>
            <option value="recent" ${sourceOrder === 'recent' ? 'selected' : ''}>最近收藏优先</option>
          </select>
        </div>
      </div>`;
    }
    const p = providerById.get(key);
    if (!p) return '';
    const chosen = sections[p.id];
    return `<div class="panel-block ${chosen.size ? '' : 'inactive'}" data-key="${p.id}">
      <div class="pb-head">
        <span class="pb-name">${esc(p.name)}</span>
        <span class="pb-kind">${kindLabel(p)}</span>
        <span class="pb-status">${chosen.size ? `${chosen.size} 项` : '不显示'}</span>
        ${blockArrows(key, index)}
      </div>
      <div class="pb-note">${esc(p.note)}</div>
      <div class="pb-sections">
        ${(p.sections || []).map((sec) => `<label class="prov-sec"><input type="checkbox" data-provider-section="${p.id}" data-section="${sec.key}" ${chosen.has(sec.key) ? 'checked' : ''}> ${esc(sec.label)}</label>`).join('')}
      </div>
    </div>`;
  };
  const cefrLevels = new Set(settings.preview_cefr_levels || ['B1', 'B2']);
  const dict = settings.dict || {};
  const cefrInfo = settings.cefr || {};
  const written = stateInfo?.updated_at ? stateInfo.updated_at.replace('T', ' ').slice(0, 19) : '';
  const stateSize = stateInfo ? (stateInfo.size < 1024 ? `${stateInfo.size} B` : `${(stateInfo.size / 1024).toFixed(1)} KB`) : '';

  root.innerHTML = `<div class="modal">
    <div class="modal-head"><b>设置</b><button id="set-close">✕</button></div>
    <div class="modal-body">

      <div class="field">
        <label>学习记录</label>
        <div class="setting-row">
          <div class="grow"><div class="name">数据文件</div>
            <div class="desc mono" id="set-state-path" title="${esc(stateInfo?.path || '')}">${esc(stateInfo?.path || 'data/state.json')}</div></div>
          <span class="tag" id="set-state-count">${stateInfo ? `${stateInfo.words} 词 · ${stateSize}` : ''}</span>
        </div>
        <div class="desc" id="set-state-updated" style="margin-top:6px">
          ${written ? `最近写入：${esc(written)}。` : ''}每次进度、词条、设置变化后自动写回；视频删除后自动清理对应记录。
        </div>
      </div>

      <div class="field">
        <label>外观</label>
        <div class="setting-row">
          <div class="grow"><div class="name">主题</div><div class="desc">暗色档案室 / 亮色羊皮纸；亮色用墨绿、黄铜与珊瑚色。</div></div>
          <div class="seg" id="set-theme">
            <button type="button" data-theme="dark" class="${(settings.theme || 'dark') === 'dark' ? 'on' : ''}">暗色</button>
            <button type="button" data-theme="light" class="${settings.theme === 'light' ? 'on' : ''}">亮色</button>
          </div>
        </div>
      </div>

      <div class="field">
        <label>预习筛选（CEFR）</label>
        <div class="desc">只把所选 CEFR 级别的词放进预习列表。这里是全局设置，所有 Part 共用；预习页也可以快速切换。</div>
        <div class="cefr-options" id="cefr-options">
          ${CEFR_LEVELS.map((level) => `<label class="cefr-opt"><input type="checkbox" data-cefr="${level}" ${cefrLevels.has(level) ? 'checked' : ''}><span>${level}</span></label>`).join('')}
        </div>
        <div class="desc" style="margin-top:6px">默认 B1 + B2；至少保留一个级别。</div>
      </div>

      <div class="field">
        <label>发音</label>
        <div class="desc">词典顶部始终提供英式和美式两个播放按钮；默认口音决定自动播放使用哪一个。在线音频失败时使用浏览器语音合成。</div>
        <div class="setting-row">
          <div class="grow"><div class="name">默认口音</div><div class="desc">自动播放使用这个口音</div></div>
          <select class="select" id="set-pron-accent">
            <option value="uk" ${(settings.pronounce_accent || 'us') === 'uk' ? 'selected' : ''}>英式</option>
            <option value="us" ${(settings.pronounce_accent || 'us') !== 'uk' ? 'selected' : ''}>美式</option>
          </select>
        </div>
        <label class="setting-check"><input type="checkbox" id="set-pron-auto" ${settings.pronounce_auto ? 'checked' : ''}> 查词时自动播放默认口音</label>
      </div>

      <div class="field">
        <label>查词面板</label>
        <div class="desc">按从上到下的顺序显示；勾选词典里的内容就会启用该词典，不需要再单独勾选词典。来源句子和我的备注也是同级板块。</div>
        <div class="panel-blocks" id="panel-blocks">
          ${lookupOrder.map((key, index) => blockHtml(key, index)).join('')}
        </div>
      </div>

      <div class="field">
        <label>片库目录</label>
        <div class="desc">uttu-extractor 的 output 目录。修改后会重新扫描 <span class="mono">part.json</span>。</div>
        <div style="display:flex;gap:6px">
          <input class="input" id="set-root" value="${esc(settings.library_root || S.lib?.root || '')}">
          <button class="btn" id="set-sync" style="flex:0 0 auto">保存并扫描</button>
        </div>
      </div>

      <div class="field">
        <label>词典数据</label>
        <div class="setting-row">
          <div class="grow"><div class="name">ECDICT 离线词库</div>
            <div class="desc">${dict.available ? `${(dict.entries || 0).toLocaleString()} 词条 · ${(dict.size / 1048576).toFixed(0)} MB` : '未构建，请运行 tools/build_ecdict.py'}</div></div>
          <span class="tag ${dict.available ? 'gold' : ''}">${dict.available ? '就绪' : '缺失'}</span>
        </div>
        <div class="setting-row">
          <div class="grow"><div class="name">CEFR 词表</div>
            <div class="desc">${cefrInfo.available ? `${(cefrInfo.words || 0).toLocaleString()} 词 · ${((cefrInfo.size || 0) / 1048576).toFixed(1)} MB · A1–C2` : 'CEFR 数据缺失'}</div></div>
          <span class="tag ${cefrInfo.available ? 'gold' : ''}">${cefrInfo.available ? '就绪' : '缺失'}</span>
        </div>
        <div class="setting-row">
          <div class="grow"><div class="name">词典缓存</div><div class="desc">清空后下次查词会重新请求在线引擎</div></div>
          <button class="btn sm" id="set-clear">清空</button>
        </div>
      </div>

      <div class="field">
        <label>关于</label>
        <div class="desc" style="margin:0">
          回溯 ${esc(settings.version || '0.0.1')} · 资源提取器已内置。<br>
          字幕与视频来自本地 uttu-extractor 输出；中文释义与音标来自 ECDICT（MIT）；
          CEFR 级别来自 Words CEFR Dataset / cefrpy（MIT）；英英释义来自 Wiktionary / Free Dictionary；
          词源、同根词、真题来自有道词典公开接口。<br>
          所有学习记录保存在本机 <span class="mono">data/state.json</span>（一个 JSON 文件），进度、词条、设置变化后自动写回；视频删除后对应记录会自动清理。
        </div>
      </div>
    </div>
    <div class="modal-foot"><button class="btn primary" id="set-done">完成</button></div>
  </div>`;

  const close = () => { root.classList.remove('on'); root.innerHTML = ''; };
  $('#set-close').addEventListener('click', close);
  $('#set-done').addEventListener('click', close);
  root.addEventListener('click', (event) => { if (event.target === root) close(); }, { once: true });
  document.addEventListener('keydown', function onEsc(event) {
    if (event.key === 'Escape') { document.removeEventListener('keydown', onEsc); close(); }
  });

  async function saveSections() {
    const map = {};
    const providers = [];
    providerMeta.forEach((p) => {
      const chosen = $$(`[data-provider-section="${p.id}"]`).filter((el) => el.checked).map((el) => el.dataset.section);
      map[p.id] = chosen;
      if (chosen.length) providers.push(p.id);
      const card = $(`.panel-block[data-key="${p.id}"]`);
      if (card) {
        card.classList.toggle('inactive', !chosen.length);
        const status = card.querySelector('.pb-status');
        if (status) status.textContent = chosen.length ? `${chosen.length} 项` : '不显示';
      }
    });
    try {
      await API.saveSettings({ dict_sections: map, providers });
      S.settings.dict_sections = map;
      S.settings.providers = providers;
    } catch (err) { toast(err.message, 'err'); }
  }

  async function saveCefrLevels() {
    const chosen = $$('[data-cefr]').filter((el) => el.checked).map((el) => el.dataset.cefr);
    if (!chosen.length) {
      toast('至少保留一个 CEFR 级别', 'err');
      const last = $$('[data-cefr]').find((el) => el.dataset.cefr === [...cefrLevels].pop());
      if (last) last.checked = true;
      return;
    }
    try {
      await API.saveSettings({ preview_cefr_levels: chosen });
      S.settings.preview_cefr_levels = chosen;
      cefrLevels.clear();
      chosen.forEach((level) => cefrLevels.add(level));
    } catch (err) { toast(err.message, 'err'); }
  }

  $$('[data-provider-section]').forEach((input) => input.addEventListener('change', saveSections));
  $$('[data-cefr]').forEach((input) => input.addEventListener('change', saveCefrLevels));

  $('#set-pron-accent').addEventListener('change', async (event) => {
    const accent = event.target.value === 'uk' ? 'uk' : 'us';
    try {
      await API.saveSettings({ pronounce_accent: accent });
      S.settings.pronounce_accent = accent;
    } catch (err) { toast(err.message, 'err'); }
  });
  $('#set-pron-auto').addEventListener('change', async (event) => {
    const auto = Boolean(event.target.checked);
    try {
      await API.saveSettings({ pronounce_auto: auto });
      S.settings.pronounce_auto = auto;
    } catch (err) { toast(err.message, 'err'); }
  });
  $$('#set-theme [data-theme]').forEach((btn) => btn.addEventListener('click', async () => {
    const theme = btn.dataset.theme === 'light' ? 'light' : 'dark';
    try {
      await API.saveSettings({ theme });
      applySettings({ theme });
      $$('#set-theme [data-theme]').forEach((item) => item.classList.toggle('on', item.dataset.theme === theme));
    } catch (err) { toast(err.message, 'err'); }
  }));

  $('#panel-blocks').addEventListener('click', async (event) => {
    const btn = event.target.closest('[data-block-move]');
    if (!btn) return;
    const key = btn.dataset.key;
    const index = lookupOrder.indexOf(key);
    const target = btn.dataset.blockMove === 'up' ? index - 1 : index + 1;
    if (target < 0 || target >= lookupOrder.length) return;
    [lookupOrder[index], lookupOrder[target]] = [lookupOrder[target], lookupOrder[index]];
    const container = $('#panel-blocks');
    const blocks = new Map([...container.querySelectorAll('.panel-block')].map((block) => [block.dataset.key, block]));
    lookupOrder.forEach((k) => { const block = blocks.get(k); if (block) container.appendChild(block); });
    refreshBlockButtons();
    try {
      await API.saveSettings({ lookup_order: lookupOrder });
      S.settings.lookup_order = [...lookupOrder];
    } catch (err) { toast(err.message, 'err'); }
  });
  function refreshBlockButtons() {
    $$('.panel-block').forEach((block) => {
      const index = lookupOrder.indexOf(block.dataset.key);
      $$('[data-block-move]', block).forEach((btn) => {
        btn.disabled = btn.dataset.blockMove === 'up' ? index <= 0 : index >= lookupOrder.length - 1;
      });
    });
  }
  refreshBlockButtons();

  $('#set-source-order').addEventListener('change', async (event) => {
    const order = event.target.value;
    try {
      await API.saveSettings({ source_order: order });
      S.settings.source_order = order;
    } catch (err) { toast(err.message, 'err'); }
  });

  $('#set-note-visible').addEventListener('change', async (event) => {
    const visible = Boolean(event.target.checked);
    const card = $('.panel-block[data-key="note"]');
    const status = $('#note-visibility');
    try {
      await API.saveSettings({ note_visible: visible });
      S.settings.note_visible = visible;
      card?.classList.toggle('inactive', !visible);
      if (status) status.textContent = visible ? '显示' : '不显示';
    } catch (err) {
      event.target.checked = !visible;
      toast(err.message, 'err');
    }
  });
  $('#set-sync').addEventListener('click', async () => {
    try {
      await API.saveSettings({ library_root: $('#set-root').value.trim() });
      const res = await API.sync();
      await refreshLibrary();
      toast(`已扫描 ${res.sync.parts} 个 Part · ${res.sync.cues} 条字幕`);
      close();
      location.hash = '#/';
      location.reload();
    } catch (err) { toast(err.message, 'err'); }
  });

  $('#set-clear').addEventListener('click', async () => {
    const ok = await confirmDialog({
      title: '清空词典缓存', message: '在线释义与词源缓存会被删除，之后查词需要重新联网。',
      okText: '清空',
    });
    if (!ok) return;
    try {
      const res = await API.clearCache();
      toast(`已清空 ${res.removed} 条缓存`);
    } catch (err) { toast(err.message, 'err'); }
  });
}
