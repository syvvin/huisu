// Bundled UTTU extractor: a local iframe served by the app's extractor bridge.
import { API } from '../api.js';
import { $, esc, toast, confirmDialog } from '../util.js';
import { refreshLibrary } from '../main.js';

export async function mountExtract() {
  const main = $('#main');
  main.innerHTML = `<div class="page extract-page">
    <div class="page-head">
      <h1>资源提取</h1>
      <span class="page-sub" id="ext-root">正在启动提取器…</span>
    </div>
    <div class="extract-tools">
      <button class="btn sm" id="ext-refresh">刷新界面</button>
      <button class="btn sm" id="ext-sync">重新扫描片库</button>
      <span class="extract-state" id="ext-state"></span>
    </div>
    <div class="extract-cache">
      <span id="ext-cache">正在检查源视频缓存…</span>
      <button class="btn sm" id="ext-cache-clear" hidden>清理应用缓存</button>
    </div>
    <div class="extract-frame-wrap">
      <iframe id="ext-frame" title="UTTU 资源提取器"></iframe>
      <div class="extract-cover" id="ext-cover">正在启动资源提取器…</div>
    </div>
  </div>`;

  const frame = $('#ext-frame');
  const cover = $('#ext-cover');
  const state = $('#ext-state');
  let url = 'http://127.0.0.1:8765/';
  let cache = null;

  const fmtBytes = (value) => {
    const n = Number(value) || 0;
    if (n >= 1e9) return `${(n / 1e9).toFixed(1)} GB`;
    if (n >= 1e6) return `${Math.round(n / 1e6)} MB`;
    if (n > 0) return `${Math.round(n / 1e3)} KB`;
    return '0 MB';
  };

  async function refreshCache() {
    try {
      cache = await API.extractorCache();
      const bits = [`源视频缓存：${fmtBytes(cache.total_bytes)}`];
      if (cache.videos) bits.push(`${cache.videos} 个完整视频`);
      if (cache.partials) bits.push(`${cache.partials} 个未完成`);
      if (cache.legacy_bytes) bits.push(`其中旧提取器缓存 ${fmtBytes(cache.legacy_bytes)}（不随应用清理）`);
      $('#ext-cache').textContent = bits.join(' · ');
      $('#ext-cache-clear').hidden = !(cache.primary_bytes > 0);
    } catch (err) {
      $('#ext-cache').textContent = `源视频缓存信息不可用：${err.message}`;
    }
  }

  async function start() {
    state.textContent = '启动中…';
    try {
      const res = await API.extractorStart();
      if (!res.ok) throw new Error(res.error || '提取器启动失败');
      url = res.url;
      $('#ext-root').textContent = `输出目录：${res.root}`;
      frame.src = url;
      state.textContent = '运行中';
      cover.style.display = 'none';
    } catch (err) {
      cover.style.display = 'flex';
      cover.innerHTML = `<div><b>提取器启动失败</b><br><span>${esc(err.message)}</span></div>`;
      state.textContent = '未启动';
    }
  }

  frame.addEventListener('load', () => { cover.style.display = 'none'; });
  $('#ext-refresh').addEventListener('click', () => { frame.src = `${url}?t=${Date.now()}`; });
  $('#ext-sync').addEventListener('click', async () => {
    try {
      const res = await API.sync();
      await refreshLibrary();
      toast(`片库已刷新：${res.sync.parts} 个 Part`);
    } catch (err) { toast(err.message, 'err'); }
  });

  $('#ext-cache-clear').addEventListener('click', async () => {
    if (!cache?.primary_bytes) return;
    const ok = await confirmDialog({
      title: '清理应用缓存的源视频',
      message: `将删除应用自己的源视频缓存（${fmtBytes(cache.primary_bytes)}），以释放磁盘空间。旧提取器的缓存不会删除；最近 5 分钟内仍在写入的文件会跳过。下次提取同一录像时会重新下载完整录像。`,
      okText: '清理',
      danger: true,
    });
    if (!ok) return;
    try {
      const res = await API.extractorCacheClear();
      let msg = `已移除 ${res.removed} 个应用缓存文件`;
      if (res.freed) msg += `，释放 ${fmtBytes(res.freed)}`;
      if (res.hardlinked_bytes) msg += `；另有 ${fmtBytes(res.hardlinked_bytes)} 是旧缓存硬链接，硬盘空间仍由旧目录保留`;
      if (res.skipped) msg += `，跳过 ${res.skipped} 个正在写入的文件`;
      toast(msg);
      await refreshCache();
    } catch (err) {
      toast(err.message, 'err');
    }
  });

  await refreshCache();
  await start();
}
