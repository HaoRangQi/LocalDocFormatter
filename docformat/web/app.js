const token = window.DOCFORMAT_TOKEN;
let currentJobId = null;
let pollTimer = null;
let scannedFiles = [];
let savedApiKeyMasked = "";
let apiKeyRevealed = false;
let modelOptions = [];
let runtimeInfo = { container: false, workspaceRoots: [], pathHint: "" };
let pathBrowserState = {
  mode: "files",
  currentPath: "",
  parentPath: null,
  selectedPaths: [],
  onConfirm: null,
};

const defaultCorrectionPrompt = `你是中文语音转写文稿的保守纠错工具。
只允许修正明显错别字、同音误转、ASR 转译错误、专有名词误识别和必要标点。
禁止润色、总结、扩写、缩写、改写表达风格、改变语气、重排段落或删除信息。
保持原有段落结构、换行、编号和说话内容。
只输出修正后的全文，不要解释，不要列修改清单。`;

const targetRules = {
  writer: ["docx", "pdf", "txt"],
  calc: ["xlsx", "pdf"],
  impress: ["pptx", "pdf"],
  correctionWriter: ["docx", "pdf", "txt", "md"],
  correctionSubtitle: ["docx", "pdf", "txt", "md", "srt"],
};

const extFamily = {
  ".doc": "writer",
  ".dot": "writer",
  ".rtf": "writer",
  ".odt": "writer",
  ".ott": "writer",
  ".txt": "writer",
  ".html": "writer",
  ".htm": "writer",
  ".docx": "writer",
  ".xls": "calc",
  ".xlt": "calc",
  ".ods": "calc",
  ".ots": "calc",
  ".csv": "calc",
  ".xlsx": "calc",
  ".ppt": "impress",
  ".pps": "impress",
  ".pot": "impress",
  ".odp": "impress",
  ".otp": "impress",
  ".pptx": "impress",
  ".md": "writer",
  ".srt": "subtitle",
};

const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const headers = {
    "Content-Type": "application/json",
    "X-DocFormat-Token": token,
    ...(options.headers || {}),
  };
  let response;
  try {
    response = await fetch(path, { ...options, headers });
  } catch (error) {
    throw new Error(`本地服务不可用：${error.message}`);
  }
  let payload = {};
  try {
    payload = await response.json();
  } catch (error) {
    payload = {};
  }
  if (!response.ok) {
    throw new Error(payload.error || `HTTP ${response.status}`);
  }
  return payload;
}

function sourceList() {
  return $("sources")
    .value.split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

function appendPaths(paths) {
  if (!paths.length) return;
  const existing = sourceList();
  $("sources").value = [...existing, ...paths].join("\n");
  scanSources();
}

function lexiconFileList() {
  return $("lexiconFiles")
    .value.split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

function appendLexiconPaths(paths) {
  if (!paths.length) return;
  const existing = lexiconFileList();
  $("lexiconFiles").value = [...existing, ...paths].join("\n");
  previewLexiconFiles();
}

async function pick(kind) {
  if (runtimeInfo.container || runtimeInfo.workspaceRoots.length) {
    return openPathBrowser(kind);
  }
  const payload = await api(`/api/pick?kind=${encodeURIComponent(kind)}`);
  return payload.paths || [];
}

async function loadHealth() {
  const health = await fetch("/api/health").then((response) => response.json());
  runtimeInfo = health.runtime || runtimeInfo;
  $("runtimePathHint").textContent = runtimeInfo.pathHint || "";
  const status = $("engineStatus");
  if (health.libreOffice.found) {
    status.textContent = runtimeInfo.container ? "Docker + LibreOffice 已就绪" : "LibreOffice 已就绪";
    status.className = "status status-ok";
  } else {
    status.textContent = "未检测到 LibreOffice";
    status.className = "status status-error";
    $("jobSummary").textContent = health.libreOffice.installHint;
  }
}

function openPathBrowser(kind) {
  return new Promise((resolve) => {
    pathBrowserState = {
      mode: kind,
      currentPath: runtimeInfo.workspaceRoots[0] || "",
      parentPath: null,
      selectedPaths: [],
      onConfirm: resolve,
    };
    $("pathBrowserTitle").textContent = kind === "directory" ? "选择文件夹" : "选择文件";
    $("pathBrowser").hidden = false;
    loadBrowserPath(pathBrowserState.currentPath);
  });
}

async function loadBrowserPath(path) {
  try {
    const payload = await api(`/api/browse?path=${encodeURIComponent(path || "")}`);
    pathBrowserState.currentPath = payload.path;
    pathBrowserState.parentPath = payload.parent;
    $("browserPath").textContent = payload.path;
    $("browserParent").disabled = !payload.parent;
    $("workspaceRoots").innerHTML = (payload.roots || [])
      .map((root) => `<button type="button" class="root-chip" data-path="${escapeHtml(root)}">${escapeHtml(root)}</button>`)
      .join("");
    $("browserEntries").innerHTML = (payload.entries || []).map(renderBrowserEntry).join("") || '<div class="empty-state">当前文件夹为空。</div>';
    $("browserSelection").textContent = selectionSummary();
    for (const button of document.querySelectorAll(".root-chip")) {
      button.addEventListener("click", () => loadBrowserPath(button.dataset.path));
    }
    for (const row of document.querySelectorAll(".browser-entry")) {
      row.addEventListener("click", () => handleBrowserEntry(row));
    }
  } catch (error) {
    $("browserEntries").innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`;
  }
}

function renderBrowserEntry(entry) {
  const icon = entry.kind === "directory" ? "DIR" : "FILE";
  const meta = entry.kind === "directory" ? "文件夹" : formatSize(entry.sizeBytes);
  return `<button type="button" class="browser-entry" data-kind="${escapeHtml(entry.kind)}" data-path="${escapeHtml(entry.path)}">
    <span class="entry-icon">${icon}</span>
    <span class="entry-name">${escapeHtml(entry.name)}</span>
    <span class="entry-meta">${escapeHtml(meta)}</span>
  </button>`;
}

function handleBrowserEntry(row) {
  const path = row.dataset.path;
  const kind = row.dataset.kind;
  if (kind === "directory") {
    if (pathBrowserState.mode === "directory") {
      pathBrowserState.selectedPaths = [path];
      $("browserSelection").textContent = selectionSummary();
      document.querySelectorAll(".browser-entry").forEach((entry) => entry.classList.toggle("selected", entry === row));
      return;
    }
    loadBrowserPath(path);
    return;
  }
  if (pathBrowserState.mode === "files") {
    const next = new Set(pathBrowserState.selectedPaths);
    if (next.has(path)) {
      next.delete(path);
    } else {
      next.add(path);
    }
    pathBrowserState.selectedPaths = [...next];
    row.classList.toggle("selected");
    $("browserSelection").textContent = selectionSummary();
  }
}

function selectionSummary() {
  if (!pathBrowserState.selectedPaths.length) {
    return pathBrowserState.mode === "directory" ? "未选择文件夹" : "未选择文件";
  }
  if (pathBrowserState.mode === "directory") {
    return `已选择：${pathBrowserState.selectedPaths[0]}`;
  }
  return `已选择 ${pathBrowserState.selectedPaths.length} 个文件`;
}

function closePathBrowser(paths = []) {
  $("pathBrowser").hidden = true;
  if (pathBrowserState.onConfirm) {
    pathBrowserState.onConfirm(paths);
  }
  pathBrowserState.onConfirm = null;
}

async function scanSources() {
  const sources = sourceList();
  $("correctionPanel").classList.toggle("active", $("enableAiCorrection").checked);
  if (!sources.length) {
    scannedFiles = [];
    renderFileList();
    $("scanSummary").textContent = "请选择文件或文件夹。";
    return;
  }
  $("scanSummary").textContent = "正在读取文件列表...";
  try {
    const payload = await api("/api/scan", {
      method: "POST",
      body: JSON.stringify({
        sources,
        recursive: $("recursive").checked,
        enableAiCorrection: $("enableAiCorrection").checked,
      }),
    });
    scannedFiles = payload.files.map((file) => ({
      ...file,
      targetFormat: file.defaultTargetFormat,
    }));
    $("scanSummary").textContent = `已读取 ${payload.count} 个文件。`;
    renderFileList();
  } catch (error) {
    $("scanSummary").textContent = error.message;
  }
}

function renderFileList() {
  $("fileCount").textContent = String(scannedFiles.length);
  renderBulkTargets();
  if (!scannedFiles.length) {
    $("fileList").className = "file-list empty";
    $("fileList").innerHTML = '<div class="empty-state">选择文件或文件夹后，这里会列出待转换文件。</div>';
    $("startJob").disabled = true;
    return;
  }
  $("fileList").className = "file-list";
  $("startJob").disabled = scannedFiles.every((file) => !file.targetFormat);
  $("fileList").innerHTML = scannedFiles.map(renderFileRow).join("");
  for (const row of document.querySelectorAll(".file-row")) {
    const source = row.dataset.source;
    row.querySelector(".row-target").addEventListener("change", (event) => {
      const file = scannedFiles.find((item) => item.source === source);
      if (file) file.targetFormat = event.target.value;
    });
    row.querySelector(".format-trigger").addEventListener("click", () => openFormatPopover(source, row.querySelector(".format-trigger")));
    row.querySelector(".remove-file").addEventListener("click", () => {
      scannedFiles = scannedFiles.filter((item) => item.source !== source);
      renderFileList();
    });
  }
}

function renderFileRow(file) {
  return `<div class="file-row" data-source="${escapeHtml(file.source)}">
    <div class="file-main">
      <div class="file-name">${escapeHtml(file.name)}</div>
      <div class="file-size">${formatSize(file.sizeBytes)}</div>
    </div>
    <div class="file-output">
      <span>输出：</span>
      <button type="button" class="format-trigger" ${file.supportedTargets.length ? "" : "disabled"}>${escapeHtml(
    (file.targetFormat || "不支持").toUpperCase()
  )}⌄</button>
      <select class="row-target" hidden>
        ${file.supportedTargets
          .map((target) => `<option value="${escapeHtml(target)}" ${target === file.targetFormat ? "selected" : ""}>${target.toUpperCase()}</option>`)
          .join("")}
      </select>
    </div>
    <button type="button" class="icon-button settings-button" title="设置">⚙</button>
    <button type="button" class="icon-button code-button" title="查看格式">⌘</button>
    <button type="button" class="icon-button remove-file" title="移除">×</button>
  </div>`;
}

function openFormatPopover(source, anchor) {
  const file = scannedFiles.find((item) => item.source === source);
  if (!file || !file.supportedTargets.length) return;
  const popover = $("formatPopover");
  const choices = $("formatChoices");
  choices.innerHTML = file.supportedTargets
    .map(
      (target) =>
        `<button type="button" class="format-choice ${target === file.targetFormat ? "selected" : ""}" data-target="${escapeHtml(
          target
        )}">${escapeHtml(target.toUpperCase())}</button>`
    )
    .join("");
  const rect = anchor.getBoundingClientRect();
  const panelRect = document.querySelector(".panel").getBoundingClientRect();
  popover.style.top = `${rect.bottom - panelRect.top + 10}px`;
  popover.style.left = `${Math.max(260, rect.left - panelRect.left - 260)}px`;
  popover.hidden = false;
  for (const button of choices.querySelectorAll(".format-choice")) {
    button.addEventListener("click", () => {
      file.targetFormat = button.dataset.target;
      popover.hidden = true;
      renderFileList();
    });
  }
}

function renderBulkTargets() {
  const targets = [...new Set(scannedFiles.flatMap((file) => file.supportedTargets))];
  $("bulkTargetFormat").innerHTML = targets.length
    ? targets.map((target) => `<option value="${escapeHtml(target)}">${target.toUpperCase()}</option>`).join("")
    : '<option value="">无可用格式</option>';
  $("bulkTargetFormat").disabled = targets.length === 0;
}

function applyBulkTarget() {
  const target = $("bulkTargetFormat").value;
  if (!target) return;
  scannedFiles = scannedFiles.map((file) =>
    file.supportedTargets.includes(target) ? { ...file, targetFormat: target } : file
  );
  renderFileList();
}

function formatSize(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 KB";
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(2)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

function lexiconEntries() {
  return [...document.querySelectorAll("#lexiconRows tr")]
    .map((row) => ({
      wrong: row.querySelector('[data-field="wrong"]').value.trim(),
      correct: row.querySelector('[data-field="correct"]').value.trim(),
    }))
    .filter((entry) => entry.wrong && entry.correct);
}

function addLexiconRow(wrong = "", correct = "") {
  const row = document.createElement("tr");
  row.innerHTML = `<td><input data-field="wrong" type="text" value="${escapeHtml(wrong)}"></td>
    <td><input data-field="correct" type="text" value="${escapeHtml(correct)}"></td>
    <td><button type="button" class="remove-row">删除</button></td>`;
  row.querySelector(".remove-row").addEventListener("click", () => row.remove());
  $("lexiconRows").appendChild(row);
}

async function previewLexiconFiles() {
  const paths = lexiconFileList();
  const preview = $("lexiconPreview");
  if (!paths.length) {
    preview.hidden = true;
    preview.innerHTML = "";
    return;
  }
  preview.hidden = false;
  preview.innerHTML = '<div class="muted">正在读取词表...</div>';
  try {
    const payload = await api("/api/ai/lexicon/preview", {
      method: "POST",
      body: JSON.stringify({ paths }),
    });
    preview.innerHTML = renderLexiconPreview(payload);
  } catch (error) {
    preview.innerHTML = `<div class="error">${escapeHtml(friendlyLexiconPreviewError(error.message))}</div>`;
  }
}

function friendlyLexiconPreviewError(message) {
  const text = String(message || "");
  if (text === "Not found" || text.includes("HTTP 404")) {
    return "词表预览接口不可用：请刷新页面并确认当前启动的是最新版 LocalDocFormatter。";
  }
  if (text.includes("本地服务不可用")) {
    return `${text}。请重新启动 LocalDocFormatter 后再检查词表。`;
  }
  return text;
}

function renderLexiconPreview(payload) {
  const rows = (payload.files || []).map((file) => {
    const sample =
      file.sample && file.sample.length
        ? `<div class="lexicon-sample">${file.sample
            .map((entry) => `${escapeHtml(entry.wrong)} => ${escapeHtml(entry.correct)}`)
            .join("<br>")}</div>`
        : "";
    const error = file.error ? `<div class="error">${escapeHtml(file.error)}</div>` : "";
    return `<div class="lexicon-preview-row ${escapeHtml(file.status)}">
      <div class="path">${escapeHtml(file.path)}</div>
      <div class="lexicon-preview-meta">${file.status === "success" ? `读取 ${file.count} 条` : "读取失败"}</div>
      ${sample}
      ${error}
    </div>`;
  });
  return `<div class="lexicon-preview-summary">词表文件共读取 ${payload.totalValidEntries || 0} 条有效词条。</div>${rows.join("")}`;
}

function setAiConfigStatus(message, tone = "neutral") {
  const status = $("aiConfigStatus");
  const summary = $("aiConfigSummary");
  const text = String(message || "");
  const isError = tone === "error";
  status.textContent = message;
  status.className = `ai-status ai-status-${tone}`;
  status.hidden = !isError;
  summary.textContent = isError ? "模型列表获取失败" : text;
  summary.className = `ai-summary ai-summary-${tone}`;
}

async function loadAiConfig() {
  try {
    const config = await api("/api/ai/config", { method: "GET" });
    $("aiBaseUrl").value = config.baseUrl || "https://api.openai.com/v1";
    $("aiModel").value = config.selectedModel || "";
    if (config.selectedModel) {
      setModelOptions([config.selectedModel], config.selectedModel);
    } else {
      renderModelList();
    }
    updateApiEndpointPreview();
    applyMaskedApiKey(config);
    setAiConfigStatus(config.hasApiKey ? `已保存 key：${config.apiKeyMasked}` : "未保存 API key", config.hasApiKey ? "success" : "neutral");
  } catch (error) {
    setAiConfigStatus(error.message, "error");
  }
}

function applyMaskedApiKey(config) {
  savedApiKeyMasked = config.hasApiKey ? config.apiKeyMasked || "" : "";
  apiKeyRevealed = false;
  $("aiApiKey").type = "password";
  $("aiApiKey").value = savedApiKeyMasked;
  $("aiApiKey").placeholder = config.hasApiKey ? "已保存 API key" : "未保存 API key";
  $("toggleApiKeyVisibility").textContent = "👁";
  $("toggleApiKeyVisibility").title = config.hasApiKey ? "显示 API Key" : "暂无已保存 API Key";
}

async function saveAiConfig() {
  setAiConfigStatus("保存中...", "pending");
  const currentValue = $("aiApiKey").value.trim();
  const apiKey = currentValue === savedApiKeyMasked ? "" : currentValue;
  try {
    const config = await api("/api/ai/config", {
      method: "POST",
      body: JSON.stringify({
        baseUrl: $("aiBaseUrl").value.trim(),
        apiKey,
        selectedModel: $("aiModel").value.trim(),
      }),
    });
    applyMaskedApiKey(config);
    setAiConfigStatus(config.hasApiKey ? `已保存 key：${config.apiKeyMasked}` : "未保存 API key", config.hasApiKey ? "success" : "neutral");
  } catch (error) {
    setAiConfigStatus(error.message, "error");
  }
}

async function testAiConfig() {
  setAiConfigStatus("检测中...", "pending");
  try {
    await saveAiConfig();
    const payload = await api("/api/ai/models/refresh", { method: "POST", body: "{}" });
    setModelOptions(payload.models || [], $("aiModel").value.trim());
    setAiConfigStatus(`检测通过，获取到 ${payload.models.length} 个模型。`, "success");
  } catch (error) {
    setAiConfigStatus(friendlyModelRefreshError(error.message), "error");
  }
}

async function toggleApiKeyVisibility() {
  const input = $("aiApiKey");
  if (apiKeyRevealed) {
    input.type = "password";
    input.value = savedApiKeyMasked;
    apiKeyRevealed = false;
    $("toggleApiKeyVisibility").textContent = "👁";
    $("toggleApiKeyVisibility").title = savedApiKeyMasked ? "显示 API Key" : "暂无已保存 API Key";
    return;
  }
  try {
    const payload = await api("/api/ai/config/key", { method: "GET" });
    if (!payload.hasApiKey) {
      setAiConfigStatus("未保存 API key", "neutral");
      return;
    }
    input.type = "text";
    input.value = payload.apiKey || "";
    apiKeyRevealed = true;
    $("toggleApiKeyVisibility").textContent = "🙈";
    $("toggleApiKeyVisibility").title = "隐藏 API Key";
    setAiConfigStatus("API key 已显示，可复查。", "success");
  } catch (error) {
    setAiConfigStatus(error.message, "error");
  }
}

async function refreshAiModels() {
  setAiConfigStatus("探索模型中...", "pending");
  try {
    await saveAiConfig();
    const payload = await api("/api/ai/models/refresh", { method: "POST", body: "{}" });
    setModelOptions(payload.models || [], $("aiModel").value.trim());
    setAiConfigStatus(`找到 ${payload.models.length} 个模型`, "success");
  } catch (error) {
    setAiConfigStatus(friendlyModelRefreshError(error.message), "error");
  }
}

function setModelOptions(models, selectedModel = "") {
  const next = [];
  for (const model of [selectedModel, ...models]) {
    const value = String(model || "").trim();
    if (value && !next.includes(value)) {
      next.push(value);
    }
  }
  modelOptions = next;
  if (!$("aiModel").value && modelOptions[0]) {
    $("aiModel").value = modelOptions[0];
  }
  renderModelList();
}

function addManualModel() {
  const model = $("aiModel").value.trim();
  if (!model) {
    setAiConfigStatus("请先输入模型名。", "neutral");
    return;
  }
  setModelOptions([model, ...modelOptions], model);
  setAiConfigStatus(`已添加模型：${model}`, "success");
}

function renderModelList() {
  $("aiModelOptions").innerHTML = modelOptions.map((model) => `<option value="${escapeHtml(model)}"></option>`).join("");
  $("modelCountBadge").textContent = String(modelOptions.length);
  $("modelList").innerHTML = modelOptions.length
    ? modelOptions.map(renderModelItem).join("")
    : '<div class="empty-models">暂无模型，点击“获取模型列表”或手动输入后点 +。</div>';
  for (const button of document.querySelectorAll(".model-item")) {
    button.addEventListener("click", () => {
      $("aiModel").value = button.dataset.model || "";
      renderModelList();
    });
  }
}

function renderModelItem(model) {
  const selected = model === $("aiModel").value.trim();
  return `<button type="button" class="model-item ${selected ? "selected" : ""}" data-model="${escapeHtml(model)}">
    <span class="model-dot">${escapeHtml(modelBadge(model))}</span>
    <span class="model-name">${escapeHtml(model)}</span>
    <span class="model-actions">◉ ⚙</span>
  </button>`;
}

function modelBadge(model) {
  const value = model.toLowerCase();
  if (value.includes("image")) return "img";
  if (value.includes("gpt-5")) return "5";
  if (value.includes("gpt-4")) return "4";
  return "AI";
}

function updateApiEndpointPreview() {
  const base = ($("aiBaseUrl").value || "https://api.openai.com/v1").trim().replace(/\/+$/, "");
  const v1Base = base.endsWith("/v1") ? base : `${base}/v1`;
  $("apiEndpointPreview").textContent = `模型：${v1Base}/models；修正：${v1Base}/chat/completions`;
}

function friendlyModelRefreshError(message) {
  const text = String(message || "");
  if (text.includes("403")) {
    return "模型列表访问被拒绝（403）：请检查 API key 权限、base URL 或白名单，可手动输入模型名。";
  }
  if (text.includes("401")) {
    return "模型列表鉴权失败（401）：请检查 API key 是否正确，可手动输入模型名。";
  }
  if (text.includes("404")) {
    return "模型列表接口不存在（404）：请确认 base URL 包含 /v1，可手动输入模型名。";
  }
  if (text.includes("API key is required")) {
    return "请先填写并保存 API key；如果服务不支持模型列表，也可以直接手动输入模型名。";
  }
  if (text.includes("timeout") || text.includes("timed out") || text.includes("超时")) {
    return "模型列表请求超时：请检查网络或代理，可手动输入模型名。";
  }
  if (text.includes("request failed")) {
    return `模型列表请求失败：${text}。请检查 base URL、网络或代理，可手动输入模型名。`;
  }
  return `${text}，可手动输入模型名`;
}

async function startJob() {
  const sources = sourceList();
  const files = scannedFiles.filter((file) => file.targetFormat);
  if (!sources.length || !files.length) {
    $("jobSummary").textContent = "请先选择文件或文件夹，并确认列表中有可转换文件。";
    return;
  }
  $("startJob").disabled = true;
  $("jobSummary").textContent = "任务启动中...";
  $("results").innerHTML = "";
  try {
    if ($("enableAiCorrection").checked) {
      await saveAiConfig();
    }
    const job = await api("/api/jobs", {
      method: "POST",
      body: JSON.stringify({
        sources,
        outputDir: $("outputDir").value.trim() || null,
        mode: "target",
        files: files.map((file) => ({ source: file.source, targetFormat: file.targetFormat })),
        enableAiCorrection: $("enableAiCorrection").checked,
        correctionPrompt: $("correctionPrompt").value,
        lexiconEntries: lexiconEntries(),
        lexiconFilePaths: lexiconFileList(),
        recursive: $("recursive").checked,
      }),
    });
    currentJobId = job.id;
    $("cancelJob").disabled = false;
    renderJob(job);
    pollTimer = window.setInterval(refreshJob, 1200);
  } catch (error) {
    $("jobSummary").textContent = error.message;
  } finally {
    $("startJob").disabled = false;
  }
}

async function refreshJob() {
  if (!currentJobId) return;
  const job = await api(`/api/jobs/${currentJobId}`);
  renderJob(job);
  if (!["queued", "running"].includes(job.status)) {
    window.clearInterval(pollTimer);
    pollTimer = null;
    $("cancelJob").disabled = true;
  }
}

async function cancelJob() {
  if (!currentJobId) return;
  const payload = await api(`/api/jobs/${currentJobId}/cancel`, { method: "POST", body: "{}" });
  renderJob(payload.job);
}

function renderJob(job) {
  const counts = job.results.reduce((acc, result) => {
    acc[result.status] = (acc[result.status] || 0) + 1;
    return acc;
  }, {});
  const statusText = jobStatusText(job.status);
  $("jobSummary").textContent = `任务 ${job.id}：${statusText}，成功 ${counts.success || 0}，失败 ${counts.failed || 0}，跳过 ${
    counts.skipped || 0
  }`;
  if (job.error) {
    $("jobSummary").textContent += `。失败原因：${job.error}`;
  }
  renderProgress(job);
  $("results").innerHTML = job.results.map(renderResult).join("");
}

function renderProgress(job) {
  const progress = $("jobProgress");
  const bar = $("jobProgressBar");
  const total = scannedFiles.length || job.results.length || 0;
  const done = job.results.filter((item) => ["success", "failed", "skipped"].includes(item.status)).length;
  const percent = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0;
  progress.hidden = !total;
  bar.style.width = `${percent}%`;
  bar.textContent = `${done}/${total} (${percent}%)`;
}

function jobStatusText(status) {
  const labels = {
    queued: "排队中",
    running: "运行中",
    completed: "已完成",
    failed: "失败",
    cancelled: "已取消",
  };
  return labels[status] || status;
}

function fileStatusText(status) {
  const labels = {
    pending: "等待中",
    success: "成功",
    failed: "失败",
    skipped: "跳过",
  };
  return labels[status] || status;
}

function renderResult(result) {
  const statusClass = result.status;
  const target = result.target ? `<div class="path">输出：${escapeHtml(result.target)}</div>` : "";
  const error = result.error ? `<div class="error">${escapeHtml(result.error)}</div>` : "";
  return `<div class="result">
    <span class="badge ${statusClass}">${escapeHtml(fileStatusText(result.status))}</span>
    <div>
      <div class="path">源：${escapeHtml(result.source)}</div>
      ${target}
      <div class="path">类型：${escapeHtml(result.detectedFamily || "-")}，输出：${escapeHtml(result.targetFormat || "-")}${
    result.aiCorrection ? "，AI 修正" : ""
  }</div>
      ${error}
    </div>
  </div>`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

window.addEventListener("DOMContentLoaded", () => {
  loadHealth();
  loadAiConfig();
  $("correctionPrompt").value = defaultCorrectionPrompt;
  addLexiconRow();
  $("pickFiles").addEventListener("click", async () => appendPaths(await pick("files")));
  $("pickFolder").addEventListener("click", async () => appendPaths(await pick("directory")));
  $("sources").addEventListener("input", () => {
    window.clearTimeout(window.docformatScanTimer);
    window.docformatScanTimer = window.setTimeout(scanSources, 350);
  });
  $("scanSources").addEventListener("click", scanSources);
  $("recursive").addEventListener("change", scanSources);
  $("enableAiCorrection").addEventListener("change", scanSources);
  $("bulkTargetFormat").addEventListener("change", applyBulkTarget);
  document.addEventListener("click", (event) => {
    const popover = $("formatPopover");
    if (!popover.hidden && !popover.contains(event.target) && !event.target.classList.contains("format-trigger")) {
      popover.hidden = true;
    }
  });
  renderFileList();
  $("pickOutput").addEventListener("click", async () => {
    const paths = await pick("directory");
    if (paths[0]) $("outputDir").value = paths[0];
  });
  $("startJob").addEventListener("click", startJob);
  $("cancelJob").addEventListener("click", cancelJob);
  $("saveAiConfig").addEventListener("click", saveAiConfig);
  $("refreshAiModels").addEventListener("click", refreshAiModels);
  $("testAiConfig").addEventListener("click", testAiConfig);
  $("addManualModel").addEventListener("click", addManualModel);
  $("toggleApiKeyVisibility").addEventListener("click", toggleApiKeyVisibility);
  $("aiBaseUrl").addEventListener("input", updateApiEndpointPreview);
  $("aiModel").addEventListener("input", renderModelList);
  $("pickLexiconFiles").addEventListener("click", async () => appendLexiconPaths(await pick("files")));
  $("previewLexiconFiles").addEventListener("click", previewLexiconFiles);
  $("lexiconFiles").addEventListener("input", () => {
    window.clearTimeout(window.docformatLexiconTimer);
    window.docformatLexiconTimer = window.setTimeout(previewLexiconFiles, 350);
  });
  $("addLexiconRow").addEventListener("click", () => addLexiconRow());
  $("closePathBrowser").addEventListener("click", () => closePathBrowser([]));
  $("browserParent").addEventListener("click", () => {
    if (pathBrowserState.parentPath) loadBrowserPath(pathBrowserState.parentPath);
  });
  $("selectCurrentDirectory").addEventListener("click", () => {
    pathBrowserState.selectedPaths = [pathBrowserState.currentPath];
    $("browserSelection").textContent = selectionSummary();
  });
  $("confirmPathSelection").addEventListener("click", () => {
    const paths =
      pathBrowserState.mode === "directory" && !pathBrowserState.selectedPaths.length
        ? [pathBrowserState.currentPath]
        : pathBrowserState.selectedPaths;
    closePathBrowser(paths);
  });
});
