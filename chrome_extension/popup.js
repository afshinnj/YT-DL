// YT-DL CONNECT — popup logic
//
// The popup is ephemeral: Chrome destroys it the moment it loses focus, so it
// keeps no state of its own. On every open it re-derives the whole view from
// the tab URL and the host's answers. A download in flight is tracked by
// polling get_status; a closed popup simply means the progress bar is not
// being looked at, not that the transfer stopped.
//
// Transport: chrome.runtime.sendMessage({type:"rpc", action, params}) ->
//   {ok:true, body} | {ok:false, code, error}
// The service worker owns the single native-messaging port; the popup never
// talks to the host directly.

"use strict";

// ─── element helpers ─────────────────────────────────────────────────────────
const $ = (id) => document.getElementById(id);
const esc = (text) => String(text ?? "").replace(/[&<>"']/g, (ch) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
}[ch]));

// Views are mutually exclusive sections under #content.
const VIEWS = ["unsupported", "not-installed", "main", "recent"];
function showView(name) {
  for (const view of VIEWS) {
    $(`view-${view}`).classList.toggle("hidden", view !== name);
  }
}

// ─── rpc ─────────────────────────────────────────────────────────────────────
class HostError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "HostError";
    this.code = code;
  }
}

function rpc(action, params = {}, timeoutMs = 60000) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      reject(new HostError("TIMEOUT", "The application did not respond in time."));
    }, timeoutMs);
    chrome.runtime.sendMessage(
      {type: "rpc", action, params},
      (reply) => {
        clearTimeout(timer);
        const err = chrome.runtime.lastError;
        if (err) {
          reject(new HostError("PORT_CLOSED", String(err.message || err)));
          return;
        }
        if (!reply || typeof reply !== "object") {
          reject(new HostError("NO_REPLY", "The background page did not answer."));
          return;
        }
        if (!reply.ok) {
          reject(new HostError(reply.code || "HOST_ERROR", reply.error || "request failed"));
          return;
        }
        resolve(reply.body);
      },
    );
  });
}

// ─── formatting ──────────────────────────────────────────────────────────────
function humanSize(bytes) {
  const n = Number(bytes);
  if (!isFinite(n) || n <= 0) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = n, i = 0;
  while (value >= 1024 && i < units.length - 1) { value /= 1024; i++; }
  return `${value.toFixed(value < 10 ? 1 : 0)} ${units[i]}`;
}

function humanSpeed(bytesPerSec) {
  const n = Number(bytesPerSec);
  if (!isFinite(n) || n <= 0) return "—";
  return `${humanSize(n)}/s`;
}

function humanEta(seconds) {
  const n = Number(seconds);
  if (!isFinite(n) || n <= 0) return "—";
  if (n < 60) return `${Math.round(n)}s`;
  const m = Math.floor(n / 60), s = Math.round(n % 60);
  if (m < 60) return `${m}m ${String(s).padStart(2, "0")}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${String(m % 60).padStart(2, "0")}m`;
}

function humanDuration(seconds) {
  const n = Math.round(Number(seconds) || 0);
  if (n <= 0) return "";
  const h = Math.floor(n / 3600), m = Math.floor((n % 3600) / 60), s = n % 60;
  const pad = (v) => String(v).padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

// Error codes the host can send, in the friendliest wording we have.
const ERROR_HINTS = {
  HOST_DISCONNECTED: "The desktop application is not running or is not registered with Chrome.",
  ENGINE_MISSING: "yt-dlp could not be found next to the application.",
  FFMPEG_MISSING: "This format needs FFmpeg, which is bundled with the application.",
  NETWORK_ERROR: "The video site could not be reached. Configure a proxy in the application settings.",
  UNSUPPORTED_URL: "Only YouTube video and playlist links are supported.",
  NOT_FOUND: "This video is unavailable, private, or has been removed.",
};

function errorMessage(code, fallback) {
  return ERROR_HINTS[code] || fallback || "An unexpected error occurred.";
}

// ─── status tracking ─────────────────────────────────────────────────────────
// A live download, remembered so a reopened popup can reattach to it.
const STATUS_LABEL = {
  queued: "QUEUED", running: "DOWNLOADING", completed: "COMPLETE",
  failed: "FAILED", cancelled: "CANCELLED",
};
const TERMINAL = new Set(["completed", "failed", "cancelled"]);

let activeJobId = null;      // job we started or attached to in this popup
let pollTimer = null;
let currentType = "video";   // which picker is showing

function persistJobId(jobId) {
  activeJobId = jobId;
  if (jobId) {
    chrome.storage.local.set({activeJobId});
  } else {
    chrome.storage.local.remove("activeJobId");
  }
}

function stopPolling() {
  if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
}

// ─── connection state ────────────────────────────────────────────────────────
function setConnectionState(connected, error) {
  const pill = $("connection-pill");
  const text = $("connection-text");
  pill.dataset.state = connected ? "connected" : (error ? "error" : "connecting");
  text.textContent = connected ? "host online" : (error ? "host offline" : "connecting…");
  pill.title = error ? error : "";
}

// The persistent subscription channel: connection flips arrive here. The
// worker also sends the current state immediately on connect, which is why no
// separate request is needed.
let popupChannel = null;
function openChannel() {
  try {
    popupChannel = chrome.runtime.connect({name: "ytdl-events"});
  } catch (err) {
    // connect() failing is not fatal: the RPC path still works.
    return;
  }
  popupChannel.onMessage.addListener((msg) => {
    if (!msg || typeof msg !== "object") return;
    if (msg.type === "connection") {
      setConnectionState(Boolean(msg.connected), msg.error || null);
      // A reconnect while we are showing the install screen is worth a retry:
      // the user may have just registered the host.
      if (msg.connected && !$("view-not-installed").classList.contains("hidden")) {
        loadTab();
      }
    }
  });
  popupChannel.onDisconnect.addListener(() => { popupChannel = null; });
}

// ─── main flow ───────────────────────────────────────────────────────────────
async function init() {
  bindControls();
  const versionLine = $("version-line");
  if (versionLine) {
    versionLine.textContent = `desktop downloader bridge · v${chrome.runtime.getManifest().version}`;
  }
  openChannel();  // pushes the current connection state on connect
  // Resume an in-flight download if there is one, else read the tab.
  const stored = await chrome.storage.local.get("activeJobId");
  if (stored.activeJobId) {
    const attached = await attachToJob(stored.activeJobId);
    if (attached) return;
    persistJobId(null);  // the job is gone; start fresh
  }
  await loadTab();
}

async function loadTab() {
  let tab;
  try {
    tab = await rpc("tab-info");
  } catch (err) {
    showUnsupported("The active tab could not be read.", "");
    return;
  }
  if (!tab || !tab.url) {
    showUnsupported("No page is open in this tab.", "");
    return;
  }
  $("page-url").textContent = tab.url;
  $("page-url").title = tab.url;
  if (!tab.supported) {
    showUnsupported("Open a YouTube video, then click this button again.", tab.url);
    return;
  }
  showView("main");
  await pingHost(tab.url);
}

async function pingHost(url) {
  let ping;
  try {
    ping = await rpc("ping", {url});
  } catch (err) {
    // The service worker reports a missing host as HOST_DISCONNECTED.
    if (err.code === "HOST_DISCONNECTED" || err.code === "TIMEOUT") {
      showNotInstalled();
      return;
    }
    showMainError(err.code, err.message);
    return;
  }
  if (!ping || ping.status !== "connected") {
    showNotInstalled();
    return;
  }
  paintEnvironment(ping);
  await fetchInfo(url);
}

function paintEnvironment(ping) {
  const parts = [];
  if (ping.ytdm_version && ping.ytdm_version !== "unknown") {
    parts.push(`yt-dlp ${ping.ytdm_version}`);
  }
  parts.push(ping.ffmpeg_available ? "ffmpeg ready" : "ffmpeg missing");
  if (ping.queue_length > 0) parts.push(`${ping.queue_length} queued`);
  $("engine-line").textContent = parts.join(" · ") || "—";
  $("save-path").textContent = ping.download_dir || "—";
  $("save-path").title = ping.download_dir || "";
}

// ─── metadata + pickers ──────────────────────────────────────────────────────
let lastInfo = null;  // the video_info block, keyed by the picker type

async function fetchInfo(url) {
  setBusy("fetching information…");
  try {
    const info = await rpc("get_info", {url});
    if (!info) throw new HostError("NO_REPLY", "The application returned no video information.");
    lastInfo = info;
    paintVideo(info);
    buildPickers(info);
    releaseBusy();
  } catch (err) {
    releaseBusy();
    showMainError(err.code, err.message);
  }
}

function paintVideo(info) {
  $("video-title").textContent = info.title || "untitled video";
  $("video-channel").textContent = info.channel || "—";
  const dur = humanDuration(info.duration);
  $("video-duration").textContent = dur ? dur : (info.is_playlist ? "playlist" : "—");
  $("video-duration").classList.toggle("hidden", !dur && !info.is_playlist);

  const thumb = $("thumbnail");
  if (info.thumbnail) {
    thumb.src = info.thumbnail;
    thumb.classList.remove("hidden");
    thumb.onerror = () => { thumb.classList.add("hidden"); thumb.onerror = null; };
  } else {
    thumb.classList.add("hidden");
  }
}

function clearOptions(select) {
  while (select.firstChild) select.removeChild(select.firstChild);
}

function optionFor(entry, valueKey, sizeKey) {
  const opt = document.createElement("option");
  opt.value = entry[valueKey];
  let label = entry.label || entry[valueKey];
  if (sizeKey && entry[sizeKey] > 0) label += ` · ${humanSize(entry[sizeKey])}`;
  if (entry.note) label += ` (${entry.note})`;
  opt.textContent = label;
  if (entry.available === false) {
    opt.disabled = true;
    opt.classList.add("unavailable");
  }
  return opt;
}

function buildPickers(info) {
  buildQualityPicker(info);
  buildAudioPicker(info);
  applyType(currentType);
  $("start-button").disabled = false;
}

function buildQualityPicker(info) {
  const select = $("quality-select");
  clearOptions(select);
  const qualities = info.qualities || [];
  if (!qualities.length) {
    const opt = document.createElement("option");
    opt.textContent = "no downloadable streams found";
    opt.disabled = true;
    select.appendChild(opt);
    return;
  }
  for (const entry of qualities) {
    select.appendChild(optionFor(entry, "quality", "size"));
  }
  // Prefer 1080p when the page offers it, else the first (best) entry.
  const preferred = qualities.find((q) => q.quality === "1080" && q.available !== false)
    || qualities.find((q) => q.available !== false);
  if (preferred) select.value = preferred.quality;
  paintQualityHint(qualities, select.value);
}

function buildAudioPicker(info) {
  // The audio picker reuses the same <select>; the type switch swaps the
  // option set so no extra DOM is needed.
  audioPickerInfo = info.audio_formats || [];
}

let audioPickerInfo = [];

function paintQualityHint(qualities, value) {
  const entry = qualities.find((q) => q.quality === value);
  const hint = $("quality-hint");
  if (!entry) { hint.textContent = ""; return; }
  const bits = [];
  if (entry.height) bits.push(`${entry.height}p`);
  if (entry.fps) bits.push(`${entry.fps}fps`);
  if (entry.ext) bits.push(entry.ext.toUpperCase());
  hint.textContent = bits.join(" · ") + (entry.note ? ` — ${entry.note}` : "");
}

function applyType(type) {
  currentType = type;
  for (const btn of $("type-switch").querySelectorAll(".seg")) {
    btn.classList.toggle("active", btn.dataset.type === type);
  }
  const select = $("quality-select");
  const title = $("quality-panel-title");
  const hint = $("quality-hint");
  if (type === "audio") {
    title.textContent = "AUDIO FORMAT";
    clearOptions(select);
    if (!audioPickerInfo.length) {
      const opt = document.createElement("option");
      opt.textContent = "no audio stream found";
      opt.disabled = true;
      select.appendChild(opt);
    } else {
      for (const entry of audioPickerInfo) {
        select.appendChild(optionFor(entry, "format", null));
      }
      const first = audioPickerInfo.find((e) => e.available !== false);
      if (first) select.value = first.format;
    }
    hint.textContent = "extracted from the best audio track";
  } else {
    title.textContent = "QUALITY";
    if (lastInfo) buildQualityPicker(lastInfo);
    if (lastInfo && select.options.length) {
      paintQualityHint(lastInfo.qualities || [], select.value);
    } else {
      hint.textContent = "";
    }
  }
  // The HTML starts with a disabled placeholder. Re-enable the picker only
  // when the selected media type has at least one usable option.
  select.disabled = !Array.from(select.options).some((option) => !option.disabled);
}

// ─── start / cancel / status ─────────────────────────────────────────────────
function currentSelection() {
  const select = $("quality-select");
  const value = select.value;
  return currentType === "audio"
    ? {media_type: "audio", audio_format: value || "best"}
    : {media_type: "video", quality: value || "best"};
}

function setBusy(message) {
  $("start-button").disabled = true;
  const label = $("start-button").querySelector(".btn-label");
  if (label) label.textContent = message.toUpperCase();
}

function releaseBusy() {
  const label = $("start-button").querySelector(".btn-label");
  if (label) label.textContent = "START DOWNLOAD";
  $("start-button").disabled = false;
}

async function startDownload() {
  if (!lastInfo) return;
  hideError();
  const params = {
    url: $("page-url").textContent.trim(),
    ...currentSelection(),
  };
  setBusy("queuing…");
  try {
    const reply = await rpc("start_download", params);
    if (!reply || !reply.job_id) throw new HostError("NO_REPLY", "The application did not return a job id.");
    persistJobId(reply.job_id);
    if (reply.already_queued) {
      $("progress-status").textContent = "ALREADY QUEUED";
    }
    enterDownloading(reply.job_id, reply.status);
  } catch (err) {
    releaseBusy();
    showMainError(err.code, err.message);
  }
}

function enterDownloading(jobId, status) {
  $("start-button").classList.add("hidden");
  $("cancel-button").classList.remove("hidden");
  $("progress-wrap").classList.remove("hidden");
  $("progress-status").textContent = STATUS_LABEL[status] || "QUEUED";
  stopPolling();
  const tick = () => { pollStatus(jobId); };
  tick();  // immediate first reading, then on an interval
  pollTimer = setInterval(tick, 1200);
}

async function pollStatus(jobId) {
  let status;
  try {
    status = await rpc("get_status", {job_id: jobId});
  } catch (err) {
    // The host may have gone away; keep the last frame rather than flashing.
    if (err.code === "HOST_DISCONNECTED") {
      stopPolling();
      showMainError(err.code, err.message);
    }
    return;
  }
  if (!status) return;
  paintProgress(status);
  if (TERMINAL.has(status.status)) {
    stopPolling();
    finishDownload(status);
  }
}

function paintProgress(status) {
  const prog = status.progress || {};
  const percent = Math.max(0, Math.min(100, Number(prog.percent) || 0));
  $("progress-percent").textContent = `${percent.toFixed(1)}%`;
  $("bar-fill").style.width = `${percent}%`;
  $("progress-status").textContent = STATUS_LABEL[status.status] || status.status.toUpperCase();

  const detail = [
    `${humanSize(prog.downloaded)} / ${humanSize(prog.total)}`,
    humanSpeed(prog.speed),
    prog.eta > 0 ? `${humanEta(prog.eta)} left` : "",
  ].filter(Boolean).join(" · ");
  $("progress-detail").textContent = detail || "preparing…";

  if (status.title && $("video-title").textContent === "fetching information…") {
    $("video-title").textContent = status.title;
  }
}

function finishDownload(status) {
  $("cancel-button").classList.add("hidden");
  $("bar-fill").classList.toggle("done", status.status === "completed");
  $("bar-fill").classList.toggle("failed", status.status !== "completed");
  if (status.status === "completed") {
    $("progress-status").textContent = "COMPLETE";
    const files = status.output_files || [];
    $("progress-detail").textContent = files.length
      ? `saved ${files.length} file${files.length > 1 ? "s" : ""}`
      : "finished";
    $("start-button").classList.remove("hidden");
    releaseBusy();
    persistJobId(null);
  } else if (status.status === "failed") {
    showMainError(status.error_code, status.error);
    $("start-button").classList.remove("hidden");
    releaseBusy();
    persistJobId(null);
  } else {  // cancelled
    $("progress-status").textContent = "CANCELLED";
    $("start-button").classList.remove("hidden");
    releaseBusy();
    persistJobId(null);
  }
}

async function cancelDownload() {
  if (!activeJobId) return;
  $("cancel-button").disabled = true;
  try {
    await rpc("cancel_download", {job_id: activeJobId});
    $("progress-status").textContent = "CANCELLING…";
  } catch (err) {
    $("cancel-button").disabled = false;
    showMainError(err.code, err.message);
  }
}

// Reattach to a job that survived the popup closing.
async function attachToJob(jobId) {
  try {
    const status = await rpc("get_status", {job_id: jobId});
    if (!status || TERMINAL.has(status.status)) return false;
    showView("main");
    $("page-url").textContent = status.url;
    $("page-url").title = status.url;
    $("video-title").textContent = status.title || status.url;
    $("video-channel").textContent =
      status.media_type === "audio" ? `AUDIO · ${(status.audio_format || "best").toUpperCase()}`
      : (status.quality === "best" ? "BEST" : `${status.quality}P`);
    $("video-duration").classList.add("hidden");
    $("thumbnail").classList.add("hidden");
    try { const ping = await rpc("ping"); paintEnvironment(ping); } catch (err) { /* optional */ }
    persistJobId(jobId);
    paintProgress(status);
    enterDownloading(jobId, status.status);
    return true;
  } catch (err) {
    return false;  // unknown job: fall through to the normal tab flow
  }
}

// ─── recent downloads �────────────────────────────────────────────────────────
async function showRecent() {
  showView("recent");
  const list = $("recent-list");
  list.innerHTML = "";
  const placeholder = document.createElement("div");
  placeholder.className = "muted";
  placeholder.textContent = "loading…";
  list.appendChild(placeholder);
  try {
    const reply = await rpc("list_downloads", {limit: 15});
    const downloads = (reply && reply.downloads) || [];
    list.innerHTML = "";
    if (!downloads.length) {
      const empty = document.createElement("div");
      empty.className = "muted";
      empty.textContent = "No downloads yet.";
      list.appendChild(empty);
      return;
    }
    for (const item of downloads) {
      list.appendChild(recentRow(item));
    }
  } catch (err) {
    list.innerHTML = "";
    const failed = document.createElement("div");
    failed.className = "muted";
    failed.textContent = `could not read the list: ${esc(err.message)}`;
    list.appendChild(failed);
  }
}

function recentRow(item) {
  const row = document.createElement("div");
  row.className = `recent-row status-${item.status}`;

  const title = document.createElement("div");
  title.className = "recent-title";
  title.textContent = item.title || item.url;
  title.title = item.title || item.url;

  const meta = document.createElement("div");
  meta.className = "recent-meta";
  const kind = item.media_type === "audio"
    ? `audio ${(item.audio_format || "best").toUpperCase()}`
    : (item.quality === "best" ? "best" : `${item.quality}p`);
  meta.textContent = [
    kind,
    (STATUS_LABEL[item.status] || item.status),
    item.percent > 0 && !TERMINAL.has(item.status) ? `${Number(item.percent).toFixed(0)}%` : null,
    item.timestamp || null,
  ].filter(Boolean).join(" · ");

  row.append(title, meta);
  return row;
}

// ─── error display ───────────────────────────────────────────────────────────
function showMainError(code, fallback) {
  const box = $("error-box");
  box.classList.remove("hidden");
  $("error-code").textContent = code || "ERROR";
  $("error-message").textContent = errorMessage(code, fallback);
  if ($("progress-wrap").classList.contains("hidden")) {
    // Nothing was in flight; restore the start button so the user can retry.
    releaseBusy();
  }
}

function hideError() {
  $("error-box").classList.add("hidden");
  $("error-message").textContent = "";
}

function showUnsupported(detail, url) {
  $("unsupported-detail").textContent = detail;
  const box = $("unsupported-url");
  box.textContent = url || "";
  box.classList.toggle("hidden", !url);
  showView("unsupported");
}

function showNotInstalled() {
  showView("not-installed");
}

// ─── controls ────────────────────────────────────────────────────────────────
function bindControls() {
  for (const btn of $("type-switch").querySelectorAll(".seg")) {
    btn.addEventListener("click", () => applyType(btn.dataset.type));
  }
  $("quality-select").addEventListener("change", (event) => {
    if (currentType === "video" && lastInfo) {
      paintQualityHint(lastInfo.qualities || [], event.target.value);
    }
  });
  $("start-button").addEventListener("click", startDownload);
  $("cancel-button").addEventListener("click", cancelDownload);
  $("retry-connection").addEventListener("click", () => {
    hideError();
    init();
  });
  $("show-recent").addEventListener("click", showRecent);
  $("back-from-recent").addEventListener("click", () => {
    showView("main");
    // The tab may have changed while the list was open.
    loadTab();
  });
}

document.addEventListener("DOMContentLoaded", init);
