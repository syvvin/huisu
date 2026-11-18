(() => {
  "use strict";

  const state = {
    tree: null,
    local: { parts: {}, chapters: {}, categories: {}, total_complete: 0, total_partial: 0 },
    chapters: new Map(),     // path -> chapter summary
    loading: new Set(),      // paths currently loading
    loadErrors: new Map(),   // path -> message
    selected: new Map(),     // "path#number" -> {path, number, title_en, title_zh}
    expandedCats: new Set(),
    expandedChapters: new Set(),
    filter: "",
    job: null,
    pollTimer: null,
    jobLogSeq: -1,
    selectingCategory: false,
    lastSnapshot: null,
  };

  const $ = (id) => document.getElementById(id);
  const treeEl = $("tree");
  const toastEl = $("toast");
  let toastTimer = null;

  const esc = (value) => String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");

  const keyOf = (path, number) => `${path}#${number}`;

  function toast(message, isError = false) {
    toastEl.textContent = message;
    toastEl.classList.toggle("error", isError);
    toastEl.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toastEl.classList.remove("show"), 3800);
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
    let data = null;
    try { data = await response.json(); } catch (_) { /* ignore */ }
    if (!response.ok) {
      throw new Error((data && data.error) || `${response.status} ${response.statusText}`);
    }
    return data;
  }

  // Selection is intentionally session-only. It must not survive closing the
  // app or reopening the resource page; otherwise restored checks point at
  // chapters that are not loaded yet and look empty until each row is expanded.
  function saveSelection() { /* session-only by design */ }

  function restoreSelection() { /* no persistence by design */ }

  function chapterOf(path) {
    return state.chapters.get(path);
  }

  function allParts(path) {
    const chapter = chapterOf(path);
    return chapter ? chapter.parts || [] : [];
  }

  function isSelected(path, number) {
    return state.selected.has(keyOf(path, number));
  }

  function localOf(path, number) {
    return (state.local.parts || {})[keyOf(path, number)] || null;
  }

  function localChapter(path) {
    return (state.local.chapters || {})[path] || null;
  }

  function localCategory(slug) {
    return (state.local.categories || {})[slug] || null;
  }

  function categorySelectionStats(category) {
    const paths = new Set((category.chapters || []).map((chapter) => chapter.path));
    let selected = 0;
    for (const item of state.selected.values()) {
      if (paths.has(item.path)) selected += 1;
    }
    let loadedParts = 0;
    let loadedSelected = 0;
    for (const chapter of category.chapters || []) {
      const data = chapterOf(chapter.path);
      if (!data) continue;
      for (const part of data.parts || []) {
        loadedParts += 1;
        if (isSelected(chapter.path, part.number)) loadedSelected += 1;
      }
    }
    return {
      selected,
      loadedParts,
      loadedSelected,
      anySelected: selected > 0,
      allLoadedSelected: loadedParts > 0 && loadedSelected === loadedParts,
    };
  }

  function localPartBadge(local) {
    if (!local) return `<span class="badge ghost">未提取</span>`;
    if (local.status === "complete") {
      const title = `${local.height ? local.height + "p · " : ""}${local.cues} 条字幕 · ${Number(local.video_duration || local.duration || 0).toFixed(1)}s · ${local.dir}`;
      const video = local.files && local.files["video.mp4"];
      const label = `已提取${local.height ? " " + local.height + "p" : ""}`;
      if (video) {
        return `<a class="badge local" href="/output/${encodePath(video)}" target="_blank" rel="noreferrer" title="${esc(title)}">${esc(label)}</a>`;
      }
      return `<span class="badge local" title="${esc(title)}">${esc(label)}</span>`;
    }
    return `<span class="badge partial" title="${esc((local.missing || []).join(", "))}">不完整</span>`;
  }

  function localChapterBadge(path) {
    const local = localChapter(path);
    if (!local) return "";
    if (local.complete === 0 && local.partial > 0) {
      return `<span class="badge partial" title="本地有输出，但文件不完整">不完整</span>`;
    }
    const data = chapterOf(path);
    const total = data ? (data.parts || []).length : null;
    if (total) {
      if (local.complete >= total && local.partial === 0) {
        return `<span class="badge local" title="本 Part 已完整输出">已提取 ${total}/${total}</span>`;
      }
      return `<span class="badge partial" title="本地已有，但未覆盖全部 Part">已有 ${local.complete}/${total}</span>`;
    }
    return `<span class="badge local" title="本地已有输出">本地 ${local.complete}</span>`;
  }

  // ---------------------------------------------------------------- tree

  function renderTree() {
    if (!state.tree) return;
    const scroll = treeEl.scrollTop;
    const query = state.filter.trim().toLowerCase();
    const categories = state.tree.categories || [];
    let html = "";
    let shownCats = 0;

    for (const category of categories) {
      const catMatch = !query || (category.label + " " + category.slug).toLowerCase().includes(query);
      const visibleChapters = (category.chapters || []).filter((chapter) => {
        if (!query) return true;
        if (catMatch) return true;
        if ((chapter.title_en + " " + chapter.title_zh + " " + chapter.slug).toLowerCase().includes(query)) {
          return true;
        }
        const data = chapterOf(chapter.path);
        return (data && data.parts || []).some((part) =>
          (part.title_en + " " + part.title_zh).toLowerCase().includes(query)
        );
      });
      if (query && !catMatch && !visibleChapters.length) continue;
      shownCats += 1;

      const catOpen = state.expandedCats.has(category.slug) || (query && visibleChapters.length > 0);
      const chapters = category.chapters || [];
      const selectedInCat = chapters.reduce((count, chapter) => {
        const chapterData = chapterOf(chapter.path);
        if (!chapterData) return count;
        return count + (chapterData.parts || []).filter((part) => isSelected(chapter.path, part.number)).length;
      }, 0);
      const localCat = localCategory(category.slug);
      const localCatBadge = localCat && (localCat.complete || localCat.partial)
        ? `<span class="badge local" title="本地已提取 ${localCat.complete} 个 Part${localCat.partial ? `，${localCat.partial} 个不完整` : ""}">本地 ${localCat.complete}${localCat.partial ? "+" + localCat.partial : ""}</span>`
        : "";

      html += `<div class="cat" data-cat="${esc(category.slug)}">
        <div class="row" data-role="cat-row" data-slug="${esc(category.slug)}">
          <span class="chev ${catOpen ? "open" : ""}" data-role="cat-toggle" data-slug="${esc(category.slug)}">▶</span>
          <input type="checkbox" data-role="cat-check" data-slug="${esc(category.slug)}" ${selectedInCat ? "" : ""}>
          <span class="node-title">
            <span class="primary">${esc(category.label)}</span>
            <span class="secondary">${esc(category.slug)} · ${chapters.length} 章</span>
          </span>
          ${localCatBadge}
          <span class="badge">${selectedInCat ? selectedInCat + " 已选" : chapters.length + " 章"}</span>
        </div>
        <div class="children ${catOpen ? "" : "hidden"}">`;

      if (visibleChapters.length === 0) {
        html += `<div class="leaf-loading">目录中没有章节</div>`;
      }
      for (const chapter of visibleChapters) {
        html += renderChapter(chapter, query);
      }
      html += `</div></div>`;
    }

    if (shownCats === 0) {
      html = `<div class="tree-loading">没有匹配的章节。</div>`;
    }
    treeEl.innerHTML = html;
    treeEl.scrollTop = scroll;
    updateSelectionUI();
  }

  function renderChapter(chapter, query) {
    const path = chapter.path;
    const data = chapterOf(path);
    const loading = state.loading.has(path);
    const error = state.loadErrors.get(path);
    const open = state.expandedChapters.has(path) || (query && !loading && data);
    const parts = data ? data.parts || [] : [];
    const selectedCount = parts.filter((part) => isSelected(path, part.number)).length;
    const titleZh = (data && data.title_zh) || chapter.title_zh || "";
    const label = (data && data.label) || "";
    const partsLabel = data ? `${parts.length} Part` : (loading ? "读取中" : "未读取");
    const errorBadge = error ? `<span class="badge warn">错误</span>` : "";
    const localBadge = localChapterBadge(path) || (data ? `<span class="badge ghost">未提取</span>` : "");

    let partsHtml = "";
    if (open) {
      if (loading) {
        partsHtml = `<div class="leaf-loading"><span class="spinner"></span>正在读取该章的 Part…</div>`;
      } else if (error) {
        partsHtml = `<div class="leaf-error">读取失败：${esc(error)}</div>`;
      } else if (data) {
        for (const part of parts) {
          const checked = isSelected(path, part.number) ? "checked" : "";
          const kind = part.gameplay_only ? `<span class="kind">GAMEPLAY</span>` : "";
          const localBadge = localPartBadge(localOf(path, part.number));
          partsHtml += `<div class="row part-row" data-role="part-row" data-path="${esc(path)}" data-number="${part.number}">
            <input type="checkbox" data-role="part-check" data-path="${esc(path)}" data-number="${part.number}" ${checked}>
            <span class="num">${String(part.number).padStart(2, "0")}</span>
            <span class="node-title">
              <span class="primary">${esc(part.title_en)}</span>
              <span class="secondary">${esc(part.title_zh)}</span>
            </span>
            ${kind}
            ${localBadge}
            <span class="badge">${part.word_count || 0} words</span>
          </div>`;
        }
        if (!parts.length) partsHtml = `<div class="leaf-loading">该章没有可导出的 Part。</div>`;
      }
    }

    return `<div class="chapter" data-chapter="${esc(path)}">
      <div class="row" data-role="chapter-row" data-path="${esc(path)}">
        <span class="chev ${open ? "open" : ""}" data-role="chapter-toggle" data-path="${esc(path)}">▶</span>
        <input type="checkbox" data-role="chapter-check" data-path="${esc(path)}">
        <span class="node-title">
          <span class="primary">${esc(chapter.title_en || path)}</span>
          <span class="secondary">${esc(titleZh)}${label ? " · " + esc(label) : ""} · ${esc(path)}</span>
        </span>
        ${errorBadge}
        ${localBadge}
        <span class="badge">${partsLabel}</span>
      </div>
      <div class="children ${open ? "" : "hidden"}">${partsHtml}</div>
    </div>`;
  }

  async function loadChapter(path) {
    if (state.chapters.has(path) || state.loading.has(path)) return state.chapters.get(path);
    state.loading.add(path);
    state.loadErrors.delete(path);
    renderTree();
    try {
      const data = await api(`/api/chapter?path=${encodeURIComponent(path)}`);
      state.chapters.set(path, data);
      // Refresh stored titles and prune selections for parts that vanished.
      const valid = new Set();
      for (const part of data.parts || []) {
        const key = keyOf(path, part.number);
        valid.add(key);
        if (state.selected.has(key)) {
          const saved = state.selected.get(key);
          saved.title_en = part.title_en;
          saved.title_zh = part.title_zh;
        }
      }
      for (const key of [...state.selected.keys()]) {
        if (key.startsWith(path + "#") && !valid.has(key)) state.selected.delete(key);
      }
      saveSelection();
      return data;
    } catch (error) {
      state.loadErrors.set(path, error.message);
      toast(`读取章节失败：${error.message}`, true);
      return null;
    } finally {
      state.loading.delete(path);
      renderTree();
    }
  }

  async function loadCategoryChapters(category) {
    const paths = (category.chapters || []).map((chapter) => chapter.path);
    const queue = paths.filter((path) => !state.chapters.has(path));
    if (!queue.length) return;
    let done = 0;
    const total = queue.length;
    $("treeMeta").textContent = `正在读取 ${category.label} 的章节详情… 0/${total}`;
    const workers = Array.from({ length: Math.min(4, queue.length) }, async () => {
      while (queue.length) {
        const path = queue.shift();
        await loadChapter(path);
        done += 1;
        $("treeMeta").textContent = `正在读取 ${category.label} 的章节详情… ${done}/${total}`;
      }
    });
    await Promise.all(workers);
    $("treeMeta").textContent = `已读取 ${category.label} 的 ${total} 个章节。`;
  }

  async function loadOutputs(refresh = false, silent = false) {
    try {
      const local = await api(`/api/outputs${refresh ? "?refresh=1" : ""}`);
      state.local = local || state.local;
      if (state.tree) renderTree();
      return state.local;
    } catch (error) {
      if (!silent) toast(`读取本地输出失败：${error.message}`, true);
      return null;
    }
  }

  function selectedPartsInChapter(path) {
    return allParts(path).filter((part) => isSelected(path, part.number));
  }

  async function setChapterChecked(path, checked) {
    let data = chapterOf(path);
    if (!data) data = await loadChapter(path);
    if (!data) return;
    for (const part of data.parts || []) {
      const key = keyOf(path, part.number);
      if (checked) {
        state.selected.set(key, {
          path,
          number: part.number,
          title_en: part.title_en,
          title_zh: part.title_zh,
        });
      } else {
        state.selected.delete(key);
      }
    }
    saveSelection();
    renderTree();
  }

  async function setCategoryChecked(slug, checked) {
    const category = (state.tree.categories || []).find((item) => item.slug === slug);
    if (!category) return;
    if (state.selectingCategory) {
      toast("正在读取其他分类，请稍候。", true);
      return;
    }
    state.selectingCategory = true;
    const previous = $("treeMeta").textContent;
    $("treeMeta").textContent = `正在读取 ${category.label} …`;
    try {
      if (checked) await loadCategoryChapters(category);
      const chapterPaths = new Set((category.chapters || []).map((chapter) => chapter.path));
      if (!checked) {
        for (const [key, item] of [...state.selected.entries()]) {
          if (chapterPaths.has(item.path)) state.selected.delete(key);
        }
      }
      for (const chapter of category.chapters || []) {
        const data = chapterOf(chapter.path);
        if (!data) continue;
        for (const part of data.parts || []) {
          const key = keyOf(chapter.path, part.number);
          if (checked) {
            state.selected.set(key, {
              path: chapter.path,
              number: part.number,
              title_en: part.title_en,
              title_zh: part.title_zh,
            });
          }
        }
      }
      saveSelection();
      renderTree();
      toast(`${category.label}: ${checked ? "已全选可读取的 Part" : "已取消选择"}`);
    } finally {
      state.selectingCategory = false;
      $("treeMeta").textContent = previous || "";
    }
  }

  // ------------------------------------------------------------- selection

  function updateSelectionUI() {
    const values = [...state.selected.values()];
    $("selectedParts").textContent = String(values.length);
    const chapters = new Set(values.map((item) => item.path));
    const targetHeight = Number($("resolution").value) || 1080;
    const mismatchedLocal = values.filter((item) => {
      const local = localOf(item.path, item.number);
      return local && local.height && Number(local.height) !== targetHeight;
    }).length;
    $("selectionMeta").textContent = values.length
      ? `来自 ${chapters.size} 个章节；输出为每个 Part 独立的视频、英文 SRT、中文 SRT 和双语 SRT。`
        + (mismatchedLocal ? ` 其中 ${mismatchedLocal} 个本地输出与所选 ${targetHeight}p 不同，提取时会重新处理。` : "")
      : "还没有勾选任何 Part。展开章节后勾选，或直接勾选章节 / 分类。";

    const chips = $("selectionChips");
    const shown = values.slice(0, 10);
    chips.innerHTML = shown.map((item) =>
      `<span class="chip" title="${esc(item.title_en)}"><b>${esc(item.path.split("/")[2] || "")}</b> ${esc(item.title_en)}<span class="x" data-remove="${esc(keyOf(item.path, item.number))}">×</span></span>`
    ).join("") + (values.length > shown.length ? `<span class="chip">+${values.length - shown.length} more</span>` : "");

    $("extractBtn").disabled = values.length === 0 || (state.job && ["queued", "running"].includes(state.job.status));

    // Chapter / category tri-state boxes.
    for (const chapterRow of treeEl.querySelectorAll('[data-role="chapter-row"]')) {
      const path = chapterRow.dataset.path;
      const box = chapterRow.querySelector('[data-role="chapter-check"]');
      if (!box) continue;
      const data = chapterOf(path);
      if (!data) {
        box.checked = false;
        box.indeterminate = false;
        continue;
      }
      const parts = data.parts || [];
      const selected = parts.filter((part) => isSelected(path, part.number)).length;
      box.checked = parts.length > 0 && selected === parts.length;
      box.indeterminate = selected > 0 && selected < parts.length;
    }
    for (const catRow of treeEl.querySelectorAll('[data-role="cat-row"]')) {
      const slug = catRow.dataset.slug;
      const category = (state.tree.categories || []).find((item) => item.slug === slug);
      const box = catRow.querySelector('[data-role="cat-check"]');
      if (!category || !box) continue;
      let total = 0;
      let selected = 0;
      let loaded = 0;
      for (const chapter of category.chapters || []) {
        const data = chapterOf(chapter.path);
        if (!data) continue;
        loaded += 1;
        for (const part of data.parts || []) {
          total += 1;
          if (isSelected(chapter.path, part.number)) selected += 1;
        }
      }
      const allChaptersLoaded = loaded === (category.chapters || []).length;
      box.checked = allChaptersLoaded && total > 0 && selected === total;
      box.indeterminate = selected > 0 && !box.checked;
    }
  }

  // ------------------------------------------------------------------ job

  function renderJob(snapshot) {
    state.lastSnapshot = snapshot;
    if (!snapshot) return;
    $("jobPanel").classList.remove("hidden");
    $("jobTitle").textContent = `任务 ${snapshot.id}`;
    $("jobStage").textContent = snapshot.stage || "";
    const status = snapshot.status || "queued";
    const pill = $("jobStatus");
    pill.textContent = status;
    pill.className = `status-pill ${status}`;
    const progress = Math.max(0, Math.min(100, Number(snapshot.progress) || 0));
    $("progressBar").style.width = `${progress}%`;
    $("progressText").textContent = `${progress.toFixed(0)}%`;
    $("jobCounts").textContent = `${snapshot.outputs.length} published · ${snapshot.errors.length} errors`;
    $("cancelBtn").classList.toggle("hidden", !["queued", "running"].includes(status));
    const cancelPending = Boolean(snapshot.cancel_requested) && ["queued", "running"].includes(status);
    $("cancelBtn").disabled = cancelPending;
    $("cancelBtn").textContent = cancelPending ? "取消中…" : "取消";

    const errorsBox = $("errorsBox");
    if (snapshot.errors && snapshot.errors.length) {
      errorsBox.classList.remove("hidden");
      errorsBox.innerHTML = `<h3>错误汇总</h3><ul>${snapshot.errors.map((error) =>
        `<li>${esc(error.stage)} · Part ${esc(error.part)} · ${esc(error.message)}</li>`
      ).join("")}</ul>`;
    } else {
      errorsBox.classList.add("hidden");
      errorsBox.innerHTML = "";
    }

    const outputs = $("outputs");
    if (!snapshot.outputs.length) {
      outputs.innerHTML = `<p class="muted">任务进行中，完成后这里会列出每个 Part 的视频和字幕。</p>`;
    } else {
      outputs.innerHTML = snapshot.outputs.map((item) => {
        const files = item.files || {};
        const links = [
          ["video", "视频", "primary"],
          ["en_srt", "EN SRT", ""],
          ["zh_srt", "中文 SRT", ""],
          ["bilingual_srt", "双语 SRT", ""],
          ["metadata", "JSON", ""],
        ].filter(([key]) => files[key]).map(([key, label, cls]) =>
          `<a class="link-btn ${cls}" href="/output/${encodePath(files[key])}" target="_blank" rel="noreferrer">${label}</a>`
        ).join("");
        return `<div class="output-item">
          <div class="output-head">
            <div class="output-title"><span>P${String(item.number).padStart(2, "0")}</span>${esc(item.title_en)}</div>
            <div class="output-stats">${item.height ? esc(item.height) + "p · " : ""}${item.cues} cues · ${(Number(item.match_ratio || 0) * 100).toFixed(0)}%</div>
          </div>
          <div class="output-links">${links}</div>
        </div>`;
      }).join("");
    }

    const logEl = $("jobLog");
    const atBottom = logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight < 40;
    const logs = snapshot.logs || [];
    const seq = Number(snapshot.log_seq != null ? snapshot.log_seq : logs.length);
    if (seq !== state.jobLogSeq) {
      logEl.innerHTML = logs.map((entry) =>
        `<div class="${esc(entry.level || "info")}"><span class="t">${esc(entry.time)}</span>${esc(entry.message)}</div>`
      ).join("");
      state.jobLogSeq = seq;
      const meta = $("logMeta");
      if (meta) meta.textContent = `${logs.length} 条${snapshot.log_seq > logs.length ? " · 已截断" : ""}`;
      if (atBottom) logEl.scrollTop = logEl.scrollHeight;
    }
  }

  function encodePath(path) {
    return String(path).split("/").map((part) => encodeURIComponent(part)).join("/");
  }

  async function pollJob(jobId) {
    try {
      const snapshot = await api(`/api/jobs/${encodeURIComponent(jobId)}`);
      state.job = snapshot;
      renderJob(snapshot);
      if (["done", "partial", "failed", "cancelled"].includes(snapshot.status)) {
        clearInterval(state.pollTimer);
        state.pollTimer = null;
        $("extractBtn").disabled = state.selected.size === 0;
        await loadOutputs(true, true);
        if (snapshot.status === "done") toast(`提取完成：${snapshot.outputs.length} 个 Part。`);
        else if (snapshot.status === "partial") toast(`部分完成：${snapshot.outputs.length} 成功，${snapshot.errors.length} 失败。`, true);
        else if (snapshot.status === "cancelled") toast("任务已取消。");
        else toast("任务失败，请查看错误汇总。", true);
      }
    } catch (error) {
      clearInterval(state.pollTimer);
      state.pollTimer = null;
      toast(`读取任务状态失败：${error.message}`, true);
    }
  }

  async function startExtraction() {
    const parts = [...state.selected.values()].map((item) => ({ path: item.path, number: item.number }));
    if (!parts.length) { toast("请先勾选至少一个 Part。", true); return; }
    const force = $("forceOverwrite").checked;
    $("extractBtn").disabled = true;
    try {
      const created = await api("/api/jobs", {
        method: "POST",
        body: JSON.stringify({
          parts,
          force,
          max_height: Number($("resolution").value) || 1080,
        }),
      });
      state.jobLogSeq = -1;
      $("jobLog").classList.remove("expanded");
      $("logToggle").textContent = "展开";
      state.job = { id: created.id, status: "queued", progress: 0, stage: "queued", outputs: [], errors: [], logs: [] };
      renderJob(state.job);
      $("jobPanel").scrollIntoView({ behavior: "smooth", block: "start" });
      clearInterval(state.pollTimer);
      state.pollTimer = setInterval(() => pollJob(created.id), 1000);
      pollJob(created.id);
    } catch (error) {
      $("extractBtn").disabled = false;
      toast(`创建任务失败：${error.message}`, true);
    }
  }

  // ---------------------------------------------------------------- events

  treeEl.addEventListener("click", (event) => {
    const catCheck = event.target.closest('[data-role="cat-check"]');
    if (catCheck) {
      event.preventDefault();
      event.stopPropagation();
      const slug = catCheck.dataset.slug;
      const category = (state.tree.categories || []).find((item) => item.slug === slug);
      if (!category) return;
      if (state.selectingCategory) {
        toast("正在读取该分类，请稍候。", true);
        updateSelectionUI();
        return;
      }
      const stats = categorySelectionStats(category);
      const shouldSelect = !(stats.anySelected && stats.allLoadedSelected);
      catCheck.checked = shouldSelect;
      catCheck.indeterminate = false;
      setCategoryChecked(slug, shouldSelect);
      return;
    }
    const toggle = event.target.closest("[data-role$='-toggle']");
    if (toggle) {
      event.stopPropagation();
      if (toggle.dataset.role === "cat-toggle") {
        const slug = toggle.dataset.slug;
        if (state.expandedCats.has(slug)) state.expandedCats.delete(slug);
        else state.expandedCats.add(slug);
        renderTree();
      } else if (toggle.dataset.role === "chapter-toggle") {
        const path = toggle.dataset.path;
        if (state.expandedChapters.has(path)) {
          state.expandedChapters.delete(path);
        } else {
          state.expandedChapters.add(path);
          if (!chapterOf(path) && !state.loading.has(path)) loadChapter(path);
        }
        renderTree();
      }
      return;
    }
    if (event.target.matches("input[type='checkbox']")) return;
    const row = event.target.closest("[data-role$='-row']");
    if (row && row.dataset.role === "cat-row") {
      const slug = row.dataset.slug;
      if (state.expandedCats.has(slug)) state.expandedCats.delete(slug);
      else state.expandedCats.add(slug);
      renderTree();
    } else if (row && row.dataset.role === "chapter-row") {
      const path = row.dataset.path;
      if (state.expandedChapters.has(path)) state.expandedChapters.delete(path);
      else {
        state.expandedChapters.add(path);
        if (!chapterOf(path) && !state.loading.has(path)) loadChapter(path);
      }
      renderTree();
    }
  });

  treeEl.addEventListener("change", (event) => {
    const box = event.target;
    if (!box.matches("input[type='checkbox']")) return;
    const role = box.dataset.role;
    if (role === "cat-check") {
      setCategoryChecked(box.dataset.slug, box.checked);
    } else if (role === "chapter-check") {
      setChapterChecked(box.dataset.path, box.checked);
    } else if (role === "part-check") {
      const path = box.dataset.path;
      const number = Number(box.dataset.number);
      const key = keyOf(path, number);
      if (box.checked) {
        const part = allParts(path).find((item) => item.number === number);
        state.selected.set(key, {
          path,
          number,
          title_en: part ? part.title_en : "",
          title_zh: part ? part.title_zh : "",
        });
      } else {
        state.selected.delete(key);
      }
      saveSelection();
      updateSelectionUI();
      // Keep DOM badges in sync without a full re-render.
      renderTree();
    }
  });

  $("selectionChips").addEventListener("click", (event) => {
    const x = event.target.closest("[data-remove]");
    if (!x) return;
    state.selected.delete(x.dataset.remove);
    saveSelection();
    renderTree();
  });

  $("extractBtn").addEventListener("click", startExtraction);
  $("logToggle").addEventListener("click", () => {
    const log = $("jobLog");
    log.classList.toggle("expanded");
    $("logToggle").textContent = log.classList.contains("expanded") ? "收起" : "展开";
    if (log.classList.contains("expanded")) log.scrollTop = log.scrollHeight;
  });
  $("cancelBtn").addEventListener("click", async () => {
    if (!state.job) return;
    try {
      await api(`/api/jobs/${encodeURIComponent(state.job.id)}/cancel`, { method: "POST", body: "{}" });
      $("cancelBtn").disabled = true;
      $("cancelBtn").textContent = "取消中…";
      toast("正在取消，等待当前下载/编码步骤停止。");
    } catch (error) {
      toast(`取消失败：${error.message}`, true);
    }
  });

  $("refreshSite").addEventListener("click", async () => {
    const button = $("refreshSite");
    button.disabled = true;
    button.textContent = "扫描中…";
    try {
      const tree = await api("/api/tree?refresh=1");
      state.tree = tree;
      state.chapters.clear();
      state.loadErrors.clear();
      state.expandedChapters.clear();
      await loadOutputs(true, true);
      renderTree();
      toast("站点目录已刷新。");
    } catch (error) {
      toast(`刷新失败：${error.message}`, true);
    } finally {
      button.disabled = false;
      button.textContent = "重新扫描站点";
    }
  });

  $("expandAll").addEventListener("click", () => {
    for (const category of state.tree ? state.tree.categories || [] : []) {
      state.expandedCats.add(category.slug);
    }
    for (const path of state.chapters.keys()) {
      state.expandedChapters.add(path);
    }
    renderTree();
  });

  $("collapseAll").addEventListener("click", () => {
    state.expandedCats.clear();
    state.expandedChapters.clear();
    renderTree();
  });

  $("filter").addEventListener("input", (event) => {
    state.filter = event.target.value || "";
    renderTree();
  });

  $("resolution").addEventListener("change", (event) => {
    try { localStorage.setItem("uttu.resolution", event.target.value); } catch (_) { /* ignore */ }
    updateSelectionUI();
  });

  async function init() {
    try { localStorage.removeItem("uttu.selection"); } catch (_) { /* storage disabled */ }
    try {
      const savedResolution = localStorage.getItem("uttu.resolution");
      if (["720", "1080", "1440", "2160"].includes(savedResolution)) {
        $("resolution").value = savedResolution;
      }
    } catch (_) { /* ignore */ }
    try {
      const [tree, local] = await Promise.all([
        api("/api/tree"),
        api("/api/outputs").catch(() => null),
      ]);
      state.tree = tree;
      if (local) state.local = local;
      const totalChapters = (state.tree.categories || []).reduce((count, category) => count + (category.chapters || []).length, 0);
      $("treeMeta").textContent = `${state.tree.categories.length} 个分类 · ${totalChapters} 个章节 · 本地已有 ${state.local.total_complete || 0} 个 Part`;
      if (state.tree.errors && state.tree.errors.length) {
        toast(`部分分类读取失败：${state.tree.errors[0].category}`, true);
      }
      // Open the first category so the tree does not look empty.
      if (state.tree.categories && state.tree.categories.length) {
        state.expandedCats.add(state.tree.categories[0].slug);
      }
      renderTree();
    } catch (error) {
      treeEl.innerHTML = `<div class="leaf-error">无法读取站点目录：${esc(error.message)}</div>`;
      $("treeMeta").textContent = "读取失败";
      toast(`无法读取站点目录：${error.message}`, true);
    }
  }

  init();
})();
