// YT-DL CONNECT — service worker
//
// Owns the single native-messaging port to the desktop application and routes
// every request through it. The popup is ephemeral (Chrome closes it the
// moment it loses focus), so a download must be tracked here: the popup can
// close and reopen and still pick up the same job.
//
// Protocol: see browser_integration/protocol.py
//   {protocol_version:1, action, request_id, ...}  -> host
//   {protocol_version:1, ok, request_id, ...}      <- host

const HOST_NAME = "app.ytdm.nativehost";
const PROTOCOL_VERSION = 1;
const RECONNECT_DELAY_MS = 800;
const MAX_RECONNECT_DELAY_MS = 5000;

let port = null;
let nextRequestId = 1;
const pending = new Map();       // request_id -> {resolve, reject}
const listeners = new Set();     // popup message listeners

let connectionState = {
  connected: false,
  lastError: null,
  lastErrorAt: 0,
};
let reconnectTimer = null;
let reconnectDelay = RECONNECT_DELAY_MS;

// ─── connection ─────────────────────────────────────────────────────────────
function connect() {
  if (port) return;
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  try {
    port = chrome.runtime.connectNative(HOST_NAME);
  } catch (err) {
    // connectNative throws synchronously when the host manifest is missing.
    markDisconnected(formatNativeError(err));
    scheduleReconnect();
    return;
  }
  const connectedPort = port;
  connectedPort.onMessage.addListener(onHostMessage);
  connectedPort.onDisconnect.addListener(() => onPortDisconnect(connectedPort));
  reconnectDelay = RECONNECT_DELAY_MS;
  connectionState = {connected: true, lastError: null, lastErrorAt: 0};
  broadcast({type: "connection", connected: true});
  console.info("[YT-DL] native host connected");
}

function onPortDisconnect(disconnectedPort) {
  // Ignore late disconnect notifications from a port superseded by reconnect.
  if (disconnectedPort !== port) return;
  port = null;
  const err = chrome.runtime.lastError;
  markDisconnected(formatNativeError(err));
  // Chrome disconnects on startup, on browser restart, or when the host
  // crashes. Retry softly; the popup shows the real state either way.
  scheduleReconnect();
}

function markDisconnected(error) {
  connectionState = {
    connected: false,
    lastError: error,
    lastErrorAt: Date.now(),
  };
  // Reject anything still waiting so the UI reports the failure.
  for (const [id, entry] of pending.entries()) {
    entry.reject(new Error(error || "native host disconnected"));
    pending.delete(id);
  }
  broadcast({type: "connection", connected: false, error: error});
}

function scheduleReconnect() {
  if (reconnectTimer) return;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connect();
  }, reconnectDelay);
  reconnectDelay = Math.min(reconnectDelay * 1.6, MAX_RECONNECT_DELAY_MS);
}

function formatNativeError(err) {
  const text = err ? String(err.message || err) : "disconnected";
  // Chrome's message for a missing host manifest is distinctive; surface a
  // precise hint so the popup can show the install instructions.
  if (/native messaging|not found|does not exist|access to the specified/i.test(text)) {
    return "The YT-DL desktop application is not installed, or its browser host is not registered.";
  }
  return text;
}

function onHostMessage(message) {
  if (!message || typeof message !== "object") return;
  const id = message.request_id;
  if (id !== undefined && id !== null && pending.has(id)) {
    const entry = pending.get(id);
    pending.delete(id);
    if (message.ok === true) {
      entry.resolve(message);
    } else {
      entry.reject(new HostError(message.error_code, message.error || "unknown error"));
    }
    return;
  }
  // Anything without a matching request_id is a broadcast for the popup.
  broadcast({type: "host", message});
}

// ─── messaging with the popup ───────────────────────────────────────────────
function broadcast(message) {
  for (const listener of listeners) {
    try {
      listener(message);
    } catch (err) {
      console.error("[YT-DL] listener failed", err);
    }
  }
}

// A single request/response round trip. Never resolves on its own with a fake
// success: the promise rejects unless the host actually answered ok.
function send(action, params = {}, timeoutMs = 60000) {
  return new Promise((resolve, reject) => {
    if (!port) {
      reject(new HostError("HOST_DISCONNECTED", "Not connected to the YT-DL application."));
      return;
    }
    const requestId = `ext-${Date.now()}-${nextRequestId++}`;
    const timer = setTimeout(() => {
      if (pending.has(requestId)) {
        pending.delete(requestId);
        reject(new HostError("TIMEOUT", "The application did not respond in time."));
      }
    }, timeoutMs);

    pending.set(requestId, {
      resolve: (message) => { clearTimeout(timer); resolve(message); },
      reject: (err) => { clearTimeout(timer); reject(err); },
    });

    const message = {protocol_version: PROTOCOL_VERSION, action, request_id: requestId};
    Object.assign(message, params);
    try {
      port.postMessage(message);
    } catch (err) {
      pending.delete(requestId);
      clearTimeout(timer);
      reject(new HostError("SEND_FAILED", String(err.message || err)));
    }
  });
}

class HostError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "HostError";
    this.code = code;
  }
}

// ─── tab helpers ────────────────────────────────────────────────────────────
async function activeTab() {
  // Keep the target in the user's focused window; another window may also
  // have an active YouTube tab with a previously granted activeTab permission.
  const [tab] = await chrome.tabs.query({active: true, lastFocusedWindow: true});
  return tab;
}

// The host accepts YouTube watch/shorts/embed/live/playlist links. Mirror that
// rule here so an unsupported tab shows a clear message instead of a failed
// request.
const SUPPORTED_URL = /^https?:\/\/(?:www\.|m\.)?(?:youtube\.com\/(?:watch|embed|v|shorts|live|playlist)(?:[/?#]|$)|youtu\.be\/)/i;

function supportedUrl(url) {
  if (!url) return null;
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return null;
  } catch (err) {
    return null;
  }
  return SUPPORTED_URL.test(url) ? url : null;
}

// ─── message router ─────────────────────────────────────────────────────────
// Popup → worker RPC arrives as {type:"rpc", action, params}.
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || message.type !== "rpc") return false;
  if (sender.id !== chrome.runtime.id || sender.url !== chrome.runtime.getURL("popup.html")) {
    return false;
  }

  (async () => {
    try {
      let reply;
      if (message.action === "tab-info") {
        const tab = await activeTab();
        const url = tab ? tab.url : null;
        sendResponse({
          ok: true,
          // All successful RPC replies use the same envelope. popup.rpc()
          // returns reply.body, including for this local (non-native) action.
          body: {
            url,
            supported: Boolean(supportedUrl(url)),
            title: tab ? tab.title : "",
          },
        });
        return;
      }
      const allowedActions = new Set([
        "ping", "get_info", "start_download", "get_status",
        "cancel_download", "list_downloads",
      ]);
      if (!allowedActions.has(message.action)) {
        throw new HostError("UNKNOWN_ACTION", "Unsupported request.");
      }
      reply = await send(message.action, message.params || {});
      sendResponse({ok: true, body: reply});
    } catch (err) {
      sendResponse({
        ok: false,
        code: err.code || "HOST_DISCONNECTED",
        error: err.message || String(err),
      });
    }
  })();
  return true;  // keep the channel open for the async response
});

// A persistent subscription channel the popup opens on load.
chrome.runtime.onConnect.addListener((channel) => {
  if (channel.name !== "ytdl-events") return;
  const forward = (message) => {
    try {
      channel.postMessage(message);
    } catch (err) {
      // Popup closed; nothing to do.
    }
  };
  listeners.add(forward);
  channel.onDisconnect.addListener(() => listeners.delete(forward));
  // Send the current state immediately so the popup does not flash stale UI.
  forward({type: "connection", connected: connectionState.connected,
           error: connectionState.lastError});
});

// ─── lifecycle ──────────────────────────────────────────────────────────────
chrome.runtime.onInstalled.addListener((details) => {
  if (details.reason === "install") {
    console.info("[YT-DL] extension installed");
  }
  connect();
});

// The worker is woken for any event; make sure the port exists then too.
chrome.runtime.onStartup.addListener(connect);

// Re-establish on activity if we somehow ended up without a port.
chrome.tabs.onActivated.addListener(() => {
  if (!port && !reconnectTimer) connect();
});

connect();
