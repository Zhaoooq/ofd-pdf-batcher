const fileInput = document.querySelector("#fileInput");
const uploadForm = document.querySelector("#uploadForm");
const dropzone = document.querySelector("#dropzone");
const selectedList = document.querySelector("#selectedList");
const convertBtn = document.querySelector("#convertBtn");
const clearBtn = document.querySelector("#clearBtn");
const resultRows = document.querySelector("#resultRows");
const resultTitle = document.querySelector("#resultTitle");
const downloadAll = document.querySelector("#downloadAll");
const downloadMerged = document.querySelector("#downloadMerged");
const engineStatus = document.querySelector("#engineStatus");
const statTotal = document.querySelector("#statTotal");
const statSuccess = document.querySelector("#statSuccess");
const statFailed = document.querySelector("#statFailed");
const paperSizeSelect = document.querySelector("#paperSizeSelect");
const mergeOption = document.querySelector("#mergeOption");

const ERROR_LABELS = {
  api_not_found: "接口不存在",
  empty_upload: "没有收到上传内容",
  multipart_required: "上传格式不正确",
  no_files_selected: "请选择至少一个文件",
  unsupported_file_type: "不支持的文件类型",
  unsupported_paper_size: "不支持的纸张尺寸",
  invalid_ofd_container: "不是有效的 OFD 文件",
  easyofd_missing: "缺少 easyofd 转换库",
  pillow_missing: "缺少 Pillow 图片库",
  pymupdf_missing: "缺少 PyMuPDF PDF 处理库",
  empty_pdf_output: "转换器没有生成 PDF",
  invalid_pdf_output: "转换结果不是有效 PDF",
  image_has_no_frames: "图片没有可转换内容",
  file_not_found: "文件不存在",
  not_a_file: "不是可读取的文件",
  empty_file: "文件为空",
  no_successful_pdfs_to_merge: "没有可合并的成功 PDF",
  merged_pdf_has_no_pages: "合并后的 PDF 没有页面",
};

let selectedFiles = [];

init();

function init() {
  refreshEngine();
  setupGlobalDropGuard();

  fileInput.addEventListener("change", () => {
    selectedFiles = Array.from(fileInput.files || []);
    renderSelected();
  });

  uploadForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    await convertSelected();
  });

  clearBtn.addEventListener("click", () => {
    fileInput.value = "";
    selectedFiles = [];
    renderSelected();
    resetResults();
  });

  ["dragenter", "dragover"].forEach((name) => {
    dropzone.addEventListener(name, (event) => {
      event.preventDefault();
      dropzone.classList.add("dragover");
    });
  });

  ["dragleave", "drop"].forEach((name) => {
    dropzone.addEventListener(name, (event) => {
      event.preventDefault();
      dropzone.classList.remove("dragover");
    });
  });

  dropzone.addEventListener("drop", (event) => {
    selectedFiles = Array.from(event.dataTransfer.files || []);
    const transfer = new DataTransfer();
    selectedFiles.forEach((file) => transfer.items.add(file));
    fileInput.files = transfer.files;
    renderSelected();
  });
}

function setupGlobalDropGuard() {
  ["dragover", "drop"].forEach((name) => {
    window.addEventListener(name, (event) => {
      if (!(event.target instanceof Node) || !dropzone.contains(event.target)) {
        event.preventDefault();
      }
    });
  });
}

async function refreshEngine() {
  try {
    const response = await fetch("/api/health");
    const payload = await response.json();
    const converter = payload.converter || {};
    if (converter.available) {
      const ofdVersion = converter.engines?.ofd?.version || "ready";
      const imageVersion = converter.engines?.image?.version || "ready";
      const pdfVersion = converter.engines?.pdf?.version || "ready";
      const detail = `OFD 引擎 easyofd ${ofdVersion}；图片引擎 Pillow ${imageVersion}；PDF 引擎 PyMuPDF ${pdfVersion}`;
      engineStatus.className = "engine ready";
      engineStatus.title = detail;
      engineStatus.setAttribute("aria-label", detail);
      engineStatus.innerHTML = `<span class="pulse"></span><span>转换引擎正常</span>`;
    } else {
      engineStatus.className = "engine failed";
      engineStatus.title = "转换引擎不可用";
      engineStatus.setAttribute("aria-label", "转换引擎不可用");
      engineStatus.innerHTML = `<span class="pulse"></span><span>转换引擎不可用</span>`;
    }
  } catch (error) {
    engineStatus.className = "engine failed";
    engineStatus.title = "服务未连接";
    engineStatus.setAttribute("aria-label", "服务未连接");
    engineStatus.innerHTML = `<span class="pulse"></span><span>服务未连接</span>`;
  }
}

function renderSelected() {
  convertBtn.disabled = selectedFiles.length === 0;
  clearBtn.disabled = selectedFiles.length === 0;

  if (selectedFiles.length === 0) {
    selectedList.innerHTML = "";
    return;
  }

  selectedList.innerHTML = selectedFiles
    .map(
      (file) => `
        <div class="selected-item">
          <span title="${escapeHtml(file.name)}">${escapeHtml(file.name)}</span>
          <small>${formatBytes(file.size)}</small>
        </div>
      `,
    )
    .join("");
}

async function convertSelected() {
  if (selectedFiles.length === 0) return;

  setBusy(true);
  resultTitle.textContent = "转换中";
  disableDownload(downloadAll);
  disableDownload(downloadMerged);
  renderPendingRows(selectedFiles);

  const data = new FormData();
  selectedFiles.forEach((file) => data.append("files", file, file.name));
  data.append("paperSize", paperSizeSelect.value);
  data.append("merge", mergeOption.checked ? "true" : "false");

  try {
    const response = await fetch("/api/convert", { method: "POST", body: data });
    const payload = await response.json();
    if (!response.ok || !payload.ok) {
      throw new Error(errorLabel(payload.error || "转换失败"));
    }
    renderResults(payload);
  } catch (error) {
    resultTitle.textContent = "转换失败";
    resultRows.innerHTML = `
      <tr>
        <td colspan="7" class="error-text">${escapeHtml(error.message || "转换失败")}</td>
      </tr>
    `;
  } finally {
    setBusy(false);
  }
}

function renderPendingRows(files) {
  statTotal.textContent = files.length;
  statSuccess.textContent = "0";
  statFailed.textContent = "0";
  const paper = paperLabel(paperSizeSelect.value);
  resultRows.innerHTML = files
    .map(
      (file) => `
        <tr>
          <td><div class="file-name" title="${escapeHtml(file.name)}">${escapeHtml(file.name)}</div></td>
          <td>${sourceLabel(file.name)}</td>
          <td><span class="chip pending">处理中</span></td>
          <td>${paper}</td>
          <td>-</td>
          <td><span class="meta">${formatBytes(file.size)}</span></td>
          <td>-</td>
        </tr>
      `,
    )
    .join("");
}

function renderResults(payload) {
  const summary = payload.summary || { total: 0, success: 0, failed: 0 };
  statTotal.textContent = summary.total;
  statSuccess.textContent = summary.success;
  statFailed.textContent = summary.failed;
  resultTitle.textContent = `任务 ${payload.jobId.slice(0, 8)}`;

  if (payload.zipUrl) {
    enableDownload(downloadAll, payload.zipUrl);
  }

  if (payload.merged?.status === "success" && payload.merged.downloadUrl) {
    enableDownload(downloadMerged, payload.merged.downloadUrl);
  } else {
    disableDownload(downloadMerged);
  }

  const rows = (payload.files || []).map(renderResultRow);
  if (payload.merged) {
    rows.push(renderMergedRow(payload.merged, payload.options?.paperSize));
  }
  resultRows.innerHTML = rows.join("");
}

function renderResultRow(item) {
  const success = item.status === "success";
  const status = success
    ? `<span class="chip success">成功</span>`
    : `<span class="chip failed">失败</span>`;
  const action = success
    ? `<a class="row-action" href="${item.downloadUrl}">下载 PDF</a>`
    : `<div class="error-text" title="${escapeHtml(errorLabel(item.error || ""))}">${escapeHtml(errorLabel(item.error || "转换失败"))}</div>`;
  const pages = item.pages == null ? "-" : String(item.pages);
  const size = success ? `${formatBytes(item.outputBytes)} <span class="meta">PDF</span>` : formatBytes(item.inputBytes);

  return `
    <tr>
      <td>
        <div class="file-name" title="${escapeHtml(item.originalName)}">${escapeHtml(item.originalName)}</div>
        ${success ? `<div class="meta">${escapeHtml(item.pdfName)}</div>` : ""}
      </td>
      <td>${escapeHtml(sourceTypeLabel(item.sourceType))}</td>
      <td>${status}</td>
      <td>${paperLabel(item.paperSize)}</td>
      <td>${pages}</td>
      <td>${size}</td>
      <td>${action}</td>
    </tr>
  `;
}

function renderMergedRow(item, paperSize) {
  const success = item.status === "success";
  const status = success
    ? `<span class="chip success">已合并</span>`
    : `<span class="chip failed">合并失败</span>`;
  const action = success
    ? `<a class="row-action" href="${item.downloadUrl}">下载合并 PDF</a>`
    : `<div class="error-text">${escapeHtml(errorLabel(item.error || "合并失败"))}</div>`;
  return `
    <tr>
      <td>
        <div class="file-name">合并文件</div>
        <div class="meta">${escapeHtml(item.pdfName || "merged.pdf")}</div>
      </td>
      <td>合并</td>
      <td>${status}</td>
      <td>${paperLabel(paperSize)}</td>
      <td>${item.pages == null ? "-" : item.pages}</td>
      <td>${success ? `${formatBytes(item.outputBytes)} <span class="meta">PDF</span>` : "-"}</td>
      <td>${action}</td>
    </tr>
  `;
}

function resetResults() {
  resultTitle.textContent = "等待文件";
  statTotal.textContent = "0";
  statSuccess.textContent = "0";
  statFailed.textContent = "0";
  disableDownload(downloadAll);
  disableDownload(downloadMerged);
  resultRows.innerHTML = `<tr class="empty-row"><td colspan="7">还没有转换任务</td></tr>`;
}

function setBusy(isBusy) {
  convertBtn.disabled = isBusy || selectedFiles.length === 0;
  clearBtn.disabled = isBusy || selectedFiles.length === 0;
  paperSizeSelect.disabled = isBusy;
  mergeOption.disabled = isBusy;
  convertBtn.querySelector("span").textContent = isBusy ? "转换中" : "开始转换";
}

function enableDownload(element, href) {
  element.href = href;
  element.classList.remove("disabled");
  element.setAttribute("aria-disabled", "false");
}

function disableDownload(element) {
  element.href = "#";
  element.classList.add("disabled");
  element.setAttribute("aria-disabled", "true");
}

function sourceLabel(name) {
  return sourceTypeLabel(name.split(".").pop() || "");
}

function sourceTypeLabel(value) {
  const type = String(value || "").toLowerCase();
  if (type === "ofd") return "OFD";
  if (type === "image") return "图片";
  if (["jpg", "jpeg", "png", "bmp", "tif", "tiff", "webp"].includes(type)) return "图片";
  if (type === "merged") return "合并";
  return type ? type.toUpperCase() : "未知";
}

function paperLabel(value) {
  const labels = {
    original: "原尺寸",
    a4: "A4 竖向",
    a4_landscape: "A4 横向",
    a3: "A3 竖向",
    a3_landscape: "A3 横向",
    letter: "Letter 竖向",
    letter_landscape: "Letter 横向",
  };
  return labels[String(value || "original").toLowerCase()] || "原尺寸";
}

function errorLabel(value) {
  const key = String(value || "");
  return ERROR_LABELS[key] || key;
}

function formatBytes(bytes) {
  const value = Number(bytes || 0);
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

