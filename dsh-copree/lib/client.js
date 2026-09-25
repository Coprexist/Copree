window.__ModuleLoader__.load({ id: "dsh-copree", factory: (require) => {
var module = { exports: {} }; var exports = module.exports;

// src/client.ts
var React = require("react");
var { useEffect, useState, useRef, useCallback, useMemo } = React;
var { MarkdownText, IconNewChatOutline16 } = require("@deepseek-ai/dsh-client-ui-primitives");
var MARKDOWN_LABELS = {
  code: { copyLabel: "\u590D\u5236", copiedLabel: "\u5DF2\u590D\u5236" },
  footnotes: "\u811A\u6CE8"
};
var API = "/copree-api";
var WS_BASE = "/copree-ws";
var PLUGIN_API = "/copree-plugin";
var CONSENT_API = "/copree-consent";
var K_TOKEN = "aisc.token";
var K_USER = "aisc.user";
var store = {
  token: guardedGet(K_TOKEN),
  user: parseUser(guardedGet(K_USER)),
  contacts: null,
  contactsLoadedAt: 0,
  nameCache: {},
  // sender_id -> display name
  active: null,
  // { kind: 'group'|'dm', id: number|string, title: string }
  messages: null,
  ws: null
};
function guardedGet(key) {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}
function guardedSet(key, value) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
  }
}
function parseUser(raw) {
  try {
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}
function broadcast(what) {
  window.dispatchEvent(new CustomEvent("copree:" + what));
}
async function api(path, options = {}) {
  const headers = { ...options.headers || {} };
  if (options.json !== void 0) headers["Content-Type"] = "application/json";
  if (store.token) headers["Authorization"] = `Bearer ${store.token}`;
  const res = await fetch(`${API}${path}`, {
    method: options.method || "GET",
    headers,
    body: options.json !== void 0 ? JSON.stringify(options.json) : options.body,
    cache: "no-store"
  });
  let data = null;
  try {
    data = await res.json();
  } catch {
  }
  if (!res.ok) {
    if (res.status === 401 && store.token && !path.startsWith("/auth/login")) {
      doLogout();
    }
    const detail = data && (data.detail || data.message);
    const err = new Error(typeof detail === "string" ? detail : `request failed (${res.status})`);
    err.status = res.status;
    throw err;
  }
  return data;
}
async function pluginApi(path, options = {}) {
  const res = await fetch(PLUGIN_API + path, { method: options.method || "GET", cache: "no-store" });
  let data = null;
  try {
    data = await res.json();
  } catch {
  }
  if (!res.ok || data && data.ok === false) {
    throw new Error(data && (data.error || data.detail) || `request failed (${res.status})`);
  }
  return data;
}
function shortId(id) {
  return id ? String(id).slice(0, 7) : "\u2014";
}
function pluginStateText(status) {
  if (!status) return "\u8BFB\u53D6\u4E2D\u2026";
  if (status.updateChannel === "market") return "\u63A8\u8350\u5728\u300C\u8BBE\u7F6E \u2192 \u63D2\u4EF6\u5E02\u573A\u300D\u66F4\u65B0";
  if (status.updateChannel === "package-manager") {
    return `\u4ECE\u539F\u6765\u6E90\u66F4\u65B0\uFF08${status.installKind === "git" ? "git" : "npm"}\uFF09`;
  }
  if (status.state === "update-available" && status.reason === "incomplete") {
    return `\u5B89\u88C5\u4E0D\u5B8C\u6574\uFF0C\u7F3A ${status.missing} \u4E2A\u6587\u4EF6`;
  }
  if (status.state === "update-available") return `\u53EF\u66F4\u65B0\u5230 ${shortId(status.available && status.available.id)}`;
  if (status.state === "up-to-date") return "\u5DF2\u662F\u6700\u65B0";
  if (status.state === "source-unavailable") return "\u672A\u627E\u5230\u66F4\u65B0\u6E90";
  if (status.state === "not-installed") return "\u5B89\u88C5\u76EE\u5F55\u7F3A\u5C11\u6784\u5EFA\u6E05\u5355";
  return String(status.state);
}
function pluginCanAct(status) {
  return Boolean(status) && (status.updateChannel === "self" || status.updateChannel === "package-manager");
}
function pluginNeedsAttention(status) {
  if (!status) return false;
  const localOutdated = status.updateChannel === "self" && status.state === "update-available";
  return localOutdated || Boolean(status.backend && status.backend.mismatch);
}
async function loadContacts(force = false) {
  if (!store.token) return;
  if (!force && store.contacts && Date.now() - store.contactsLoadedAt < 3e4) return store.contacts;
  const [groups, dms] = await Promise.all([
    api("/groups").catch(() => []),
    api("/dm/sessions").catch(() => [])
  ]);
  store.contacts = { groups: Array.isArray(groups) ? groups : [], dms: Array.isArray(dms) ? dms : [] };
  store.contactsLoadedAt = Date.now();
  return store.contacts;
}
async function loadMessages(active) {
  if (!active) return [];
  let list = [];
  const path = active.kind === "group" ? `/gm/${encodeURIComponent(active.id)}/messages?limit=50` : `/dm/${encodeURIComponent(active.id)}/messages?limit=50`;
  const fetched = await api(path);
  list = Array.isArray(fetched) ? fetched : [];
  store.messages = list;
  warmNameCache(list);
  return list;
}
async function warmNameCache(messages) {
  if (!store.token || !Array.isArray(messages)) return;
  const ids = /* @__PURE__ */ new Set();
  for (const m of messages) {
    if (m && m.sender_id != null && store.nameCache[m.sender_id] === void 0) ids.add(m.sender_id);
  }
  for (const id of ids) {
    if (!store.token) break;
    try {
      const u = await api(`/chat/user/${encodeURIComponent(id)}`);
      store.nameCache[id] = u && (u.username || u.name) || `\u7528\u6237${id}`;
    } catch {
      store.nameCache[id] = `\u7528\u6237${id}`;
    }
  }
}
function senderName(m, user) {
  if (m == null) return "";
  if (m.sender_id != null && String(m.sender_type) !== "ai" && String(m.sender_id) === String(user ? user.id : "")) return "\u6211";
  if (m.sender_name) return m.sender_name;
  if (m.sender_id != null && m.sender_type) {
    const keyed = store.nameCache[`${m.sender_type}:${m.sender_id}`];
    if (keyed) return keyed;
  }
  if (m.sender_id != null && store.nameCache[m.sender_id]) return store.nameCache[m.sender_id];
  if (String(m.sender_type) === "ai") return "AI";
  return "\u7528\u6237" + (m.sender_id != null ? m.sender_id : "");
}
function wsUrl(token) {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${window.location.host}${WS_BASE}?token=${encodeURIComponent(token)}`;
}
function mediaUrl(url) {
  if (!url || typeof url !== "string") return url;
  if (url.startsWith("/api/")) return url.replace(/^\/api\//, API + "/");
  if (url.startsWith("/") && !url.startsWith(API + "/")) return API + url;
  return url;
}
function avatarUrl(url) {
  const rewritten = mediaUrl(url);
  if (!rewritten) return rewritten;
  if (rewritten.includes("/download-avatar/") && !/\.gif($|\?)/i.test(rewritten) && !rewritten.includes("thumb=")) {
    return rewritten + (rewritten.includes("?") ? "&thumb=1" : "?thumb=1");
  }
  return rewritten;
}
var attachmentBlobCache = /* @__PURE__ */ new Map();
function AttachmentImage({ fileId, name, style: imgStyle }) {
  const [src, setSrc] = useState(() => attachmentBlobCache.get(String(fileId)) || null);
  useEffect(() => {
    const key = String(fileId);
    if (attachmentBlobCache.has(key)) {
      setSrc(attachmentBlobCache.get(key));
      return;
    }
    let url = null;
    let cancelled = false;
    (async () => {
      try {
        const headers = {};
        if (store.token) headers["Authorization"] = `Bearer ${store.token}`;
        const res = await fetch(`${API}/fs/download/${encodeURIComponent(fileId)}`, { headers, cache: "no-store" });
        if (!res.ok || cancelled) return;
        const blob = await res.blob();
        url = URL.createObjectURL(blob);
        attachmentBlobCache.set(key, url);
        if (!cancelled) setSrc(url);
      } catch {
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [fileId]);
  return src ? h("img", { src, alt: name || "", style: imgStyle }) : h("div", { style: { ...imgStyle, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--dsw-alias-label-tertiary)", fontSize: 12, background: "var(--dsw-alias-interactive-bg-hover)" } }, "\u2026");
}
function mdText(content) {
  const raw = String(content || "");
  return raw.replace(/(!?\[[^\]]*\]\()([^)\s]*)(\))/g, (_all, pre, url, post) => {
    if (!url || /^[a-z]+:/i.test(url) || url.startsWith("//") || url.startsWith("#") || url.startsWith("data:")) {
      return _all;
    }
    const rewritten = mediaUrl(url);
    const absolute = rewritten.startsWith("/") ? window.location.origin + rewritten : rewritten;
    return pre + absolute + post;
  });
}
function connectWs() {
  if (!store.token) return;
  closeWs();
  let ws;
  try {
    ws = new WebSocket(wsUrl(store.token));
  } catch {
    return;
  }
  store.ws = ws;
  ws.onopen = () => {
    subscribeActive();
  };
  ws.onmessage = (event) => {
    let msg;
    try {
      msg = JSON.parse(event.data);
    } catch {
      return;
    }
    if (!msg || typeof msg !== "object") return;
    if (msg.type === "ping") {
      try {
        ws.send(JSON.stringify({ type: "pong" }));
      } catch {
      }
      return;
    }
    if (msg.type === "error") {
      broadcast("error:" + (msg.message || msg.code || "unknown"));
      return;
    }
    if (msg.type !== "message" && msg.type !== "ai_response") return;
    const data = msg.data;
    if (!data) return;
    const active = store.active;
    if (!active) return;
    const ctype = msg.conversation_type || (data.session_id !== void 0 ? "dm" : "group");
    const matches = active.kind === "group" ? ctype === "group" || String(data.group_id) === String(active.id) : ctype === "dm" || String(data.session_id || data.dm_session_id) === String(active.id);
    if (!matches) return;
    const list = store.messages || [];
    if (!list.some((m) => m && m.id === data.id)) {
      list.push(data);
      store.messages = list;
      warmNameCache([data]);
      broadcast("message");
    }
  };
  ws.onclose = () => {
    if (store.ws === ws) {
      store.ws = null;
      if (store.token) setTimeout(() => {
        if (store.token && !store.ws) connectWs();
      }, 3e3);
    }
  };
  ws.onerror = () => {
  };
}
function closeWs() {
  if (store.ws) {
    try {
      store.ws.close(1e3);
    } catch {
    }
    store.ws = null;
  }
}
function subscribeActive() {
  const ws = store.ws;
  const active = store.active;
  if (!ws || ws.readyState !== WebSocket.OPEN || !active) return;
  const payload = { type: "subscribe" };
  if (active.kind === "group") payload.group_id = active.id;
  else payload.session_id = active.id;
  ws.send(JSON.stringify(payload));
}
async function sendMessage(content) {
  const active = store.active;
  if (!active || !content) return;
  if (store.ws && store.ws.readyState === WebSocket.OPEN) {
    const payload = { type: "send", content };
    if (active.kind === "group") payload.group_id = active.id;
    else payload.session_id = active.id;
    store.ws.send(JSON.stringify(payload));
    return;
  }
  await api(
    active.kind === "group" ? `/gm/${encodeURIComponent(active.id)}/messages` : `/dm/${encodeURIComponent(active.id)}/messages`,
    { method: "POST", json: { content } }
  );
}
async function doLogin(loginId, password) {
  const data = await api("/auth/login", {
    method: "POST",
    json: { login_id: loginId, password, method: "direct" }
  });
  store.token = data.access_token;
  store.user = { id: data.user_id, name: data.username, role: data.role };
  guardedSet(K_TOKEN, store.token);
  guardedSet(K_USER, JSON.stringify(store.user));
  store.contacts = null;
  store.nameCache = {};
  connectWs();
  broadcast("auth");
}
function doLogout() {
  closeWs();
  store.token = null;
  store.user = null;
  store.contacts = null;
  store.active = null;
  store.messages = null;
  store.nameCache = {};
  guardedSet(K_TOKEN, "");
  guardedSet(K_USER, "");
  broadcast("auth");
}
function h(type, props, ...children) {
  return React.createElement(type, props, ...children);
}
var style = {
  board: { position: "fixed", inset: 0, zIndex: 30, display: "flex", background: "var(--dsw-alias-bg-base)", fontFamily: "var(--ds-font-family, system-ui, sans-serif)", color: "var(--dsw-alias-label-primary)" },
  // 侧边栏 rail：照搬官方 SidebarRoot 容器（--dsh-sidebar-inline-padding:12px、6px 12px 内边距、sidebar-fill 背景、14px 字号）。
  rail: { width: 264, flex: "none", borderRight: "1px solid var(--dsw-alias-border-l2)", display: "flex", flexDirection: "column", minHeight: 0, background: "var(--dsw-specific-sidebar-fill)", padding: "6px 12px", boxSizing: "border-box", fontSize: 14, color: "var(--dsw-alias-label-primary)" },
  // 板块头：照搬官方 WorkspaceBrowser sectionHeader（36px 高、tertiary 色、12px 圆角、左内边距 4px）。
  railHead: { boxSizing: "border-box", height: 36, color: "var(--dsw-alias-label-tertiary)", borderRadius: 12, flex: "none", alignItems: "center", gap: 4, marginBottom: 4, paddingLeft: 4, display: "flex", overflow: "hidden" },
  // 板块头标题：照搬官方 sectionLabel（nowrap、max-width 45%、20px 行高）。
  railLabel: { whiteSpace: "nowrap", minWidth: 0, maxWidth: "45%", flex: "none", lineHeight: "20px", overflow: "hidden" },
  railUser: { display: "flex", alignItems: "center", gap: 8, padding: "8px 4px", borderTop: "1px solid var(--dsw-alias-border-l2)" },
  group: { padding: "2px 0" },
  groupLabel: { padding: "8px 12px 4px", fontSize: 12, color: "var(--dsw-alias-label-tertiary)", fontWeight: 600, letterSpacing: ".02em" },
  row: { display: "flex", alignItems: "center", gap: 8, padding: "7px 4px", cursor: "pointer", fontSize: 13, color: "var(--dsw-alias-label-primary)", border: "none", background: "transparent", width: "100%", textAlign: "left", boxSizing: "border-box" },
  rowHover: { background: "var(--dsw-alias-interactive-bg-hover)" },
  rowActive: { background: "var(--dsw-alias-interactive-bg-hover-solid, var(--dsw-alias-interactive-bg-hover))" },
  avatar: { width: 28, height: 28, borderRadius: "50%", flex: "none", background: "var(--dsw-alias-state-business-tertiary)", display: "inline-flex", alignItems: "center", justifyContent: "center", fontSize: 12, fontWeight: 600, color: "var(--dsw-alias-label-primary)", overflow: "hidden" },
  rowText: { minWidth: 0, flex: 1 },
  rowTitle: { whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" },
  rowSub: { fontSize: 11, color: "var(--dsw-alias-label-tertiary)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" },
  badge: { flex: "none", minWidth: 16, height: 16, borderRadius: 8, background: "var(--dsw-alias-state-danger-primary, #e5484d)", color: "#fff", fontSize: 10, lineHeight: "16px", textAlign: "center", padding: "0 5px", boxSizing: "border-box" },
  main: { flex: 1, minWidth: 0, display: "flex", flexDirection: "column", minHeight: 0 },
  mainHead: { padding: "12px 20px", borderBottom: "1px solid var(--dsw-alias-border-l2)", fontSize: 14, fontWeight: 600, color: "var(--dsw-alias-label-primary)", flex: "none", display: "flex", alignItems: "center", gap: 8 },
  msgs: { flex: 1, minHeight: 0, overflowY: "auto", padding: "16px 20px", display: "flex", flexDirection: "column", gap: 14 },
  msgRow: { display: "flex", flexDirection: "row", alignItems: "flex-start", gap: 8, width: "100%" },
  msgMine: { justifyContent: "flex-end" },
  msgOther: { justifyContent: "flex-start" },
  msgCol: { display: "flex", flexDirection: "column", minWidth: 0, alignItems: "flex-start" },
  msgColMine: { alignItems: "flex-end" },
  // 我方消息：完全照搬 DSH 用户气泡（gdEzaW_bubble）——专用气泡 token +
  // label-primary 文字 + 22px 圆角 + 16/24 排版，随主题/风格插件联动。
  msgMineBubble: { background: "var(--dsw-specific-bubble)", color: "var(--dsw-alias-label-primary)", maxWidth: "min(525px, 100%)", borderRadius: 22, padding: "10px 16px", fontSize: 16, lineHeight: "24px", wordBreak: "break-word" },
  // 对方消息：DSH AI 消息同款——无气泡，MarkdownText 原生排版。
  msgOtherBubble: { color: "var(--dsw-alias-label-primary)", maxWidth: "min(720px, 100%)", fontSize: 15, lineHeight: "24px", wordBreak: "break-word" },
  msgMeta: { fontSize: 11, color: "var(--dsw-alias-label-tertiary)", marginBottom: 3, padding: "0 4px" },
  msgImages: { display: "flex", flexDirection: "row", flexWrap: "wrap", gap: 6, maxWidth: "min(525px, 100%)" },
  msgImage: { maxWidth: 240, maxHeight: 240, borderRadius: 12, objectFit: "cover", cursor: "zoom-in" },
  inviteCard: { display: "flex", flexDirection: "column", gap: 8, minWidth: 260, maxWidth: 320, borderRadius: 14, border: "1px solid var(--dsw-alias-border-l2)", background: "var(--dsw-alias-bg-layer-2)", padding: "12px 14px", color: "var(--dsw-alias-label-primary)" },
  inviteHead: { display: "flex", alignItems: "center", gap: 8, fontSize: 14, fontWeight: 600 },
  inviteBody: { fontSize: 13, lineHeight: "20px", color: "var(--dsw-alias-label-secondary)" },
  inviteActions: { display: "flex", gap: 8, marginTop: 2 },
  inviteBtn: { flex: 1, height: 30, borderRadius: 8, border: "none", fontSize: 12, fontWeight: 600, cursor: "pointer" },
  inviteAccept: { background: "var(--dsw-alias-state-business-primary)", color: "#fff" },
  inviteReject: { background: "var(--dsw-alias-interactive-bg-hover)", color: "var(--dsw-alias-label-primary)" },
  inviteStatus: { fontSize: 12, color: "var(--dsw-alias-label-tertiary)", paddingTop: 2 },
  headBtn: { flex: "none", padding: "4px 10px", borderRadius: 8, border: "none", background: "transparent", color: "var(--dsw-alias-label-secondary)", cursor: "pointer", fontSize: 14, lineHeight: 1 },
  headBtnActive: { background: "var(--dsw-alias-interactive-bg-hover)", color: "var(--dsw-alias-label-primary)" },
  settingsPanel: { flex: "none", maxHeight: "40%", overflowY: "auto", borderBottom: "1px solid var(--dsw-alias-border-l2)", background: "var(--dsw-alias-bg-layer-2)", padding: "12px 16px", fontSize: 13, color: "var(--dsw-alias-label-primary)", display: "flex", flexDirection: "column", gap: 10 },
  settingsRow: { display: "flex", alignItems: "center", gap: 8 },
  settingsLabel: { flex: 1, minWidth: 0, color: "var(--dsw-alias-label-secondary)" },
  settingsTitle: { fontSize: 13, fontWeight: 600, color: "var(--dsw-alias-label-primary)" },
  settingsHint: { fontSize: 12, color: "var(--dsw-alias-label-tertiary)", lineHeight: 1.5 },
  memberRow: { display: "flex", alignItems: "center", gap: 8, padding: "4px 0" },
  memberName: { flex: 1, minWidth: 0, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", fontSize: 13 },
  memberRole: { flex: "none", fontSize: 11, color: "var(--dsw-alias-label-tertiary)" },
  smallBtn: { flex: "none", padding: "4px 12px", borderRadius: 8, border: "1px solid var(--dsw-alias-border-l2)", background: "transparent", color: "var(--dsw-alias-label-primary)", cursor: "pointer", fontSize: 12, fontWeight: 500 },
  smallBtnOn: { background: "var(--dsw-alias-interactive-bg-hover)", borderColor: "transparent" },
  // 沉浸式覆盖层：zIndex 必须高于 board（30），否则在 Copree board 打开时
  // 会被 board 盖住（两者同在 shell.overlay 槽内，board fixed z30 > 本层 z5）。
  immersive: { position: "fixed", inset: 0, zIndex: 40, display: "flex", flexDirection: "column", background: "var(--dsw-alias-bg-base)" },
  immersiveBar: { flex: "none", display: "flex", alignItems: "center", gap: 10, padding: "8px 14px", borderBottom: "1px solid var(--dsw-alias-border-l2)", fontSize: 13, fontWeight: 600, color: "var(--dsw-alias-label-primary)" },
  immersiveFrame: { flex: 1, minHeight: 0, border: "none", width: "100%", background: "var(--dsw-alias-bg-base)" },
  composer: { flex: "none", display: "flex", gap: 8, padding: "12px 20px", borderTop: "1px solid var(--dsw-alias-border-l2)", background: "var(--dsw-alias-bg-base)" },
  input: { flex: 1, minHeight: 38, borderRadius: 12, border: "1px solid var(--dsw-alias-border-l2)", background: "var(--dsw-specific-input-major, var(--dsw-alias-bg-base))", color: "var(--dsw-alias-label-primary)", padding: "8px 14px", fontSize: 13, outline: "none", fontFamily: "inherit" },
  send: { flex: "none", padding: "0 18px", height: 38, borderRadius: 12, border: "none", background: "var(--dsw-alias-state-business-primary)", color: "#fff", fontSize: 13, fontWeight: 600, cursor: "pointer" },
  empty: { flex: 1, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--dsw-alias-label-tertiary)", fontSize: 13 },
  login: { flex: 1, display: "flex", alignItems: "center", justifyContent: "center", minHeight: 0 },
  loginCard: { width: 320, padding: "24px 24px 20px", borderRadius: 14, border: "1px solid var(--dsw-alias-border-l2)", background: "var(--dsw-alias-bg-base)", boxShadow: "var(--dsw-shadow-lv2)" },
  loginTitle: { fontSize: 16, fontWeight: 600, marginBottom: 16, color: "var(--dsw-alias-label-primary)" },
  field: { width: "100%", marginBottom: 10, padding: "8px 12px", borderRadius: 8, border: "1px solid var(--dsw-alias-border-l2)", background: "var(--dsw-specific-input-major, var(--dsw-alias-bg-base))", color: "var(--dsw-alias-label-primary)", fontSize: 13, boxSizing: "border-box", outline: "none", fontFamily: "inherit" },
  btn: { width: "100%", padding: "8px 0", borderRadius: 8, border: "none", background: "var(--dsw-alias-state-business-primary)", color: "#fff", fontSize: 13, fontWeight: 600, cursor: "pointer" },
  err: { color: "var(--dsw-alias-state-error-primary)", fontSize: 12, margin: "6px 0 0" },
  hint: { color: "var(--dsw-alias-label-tertiary)", fontSize: 12, marginTop: 10, lineHeight: 1.5 },
  footBtn: { display: "flex", alignItems: "center", gap: 6, padding: "6px 10px", borderRadius: 8, border: "none", background: "transparent", color: "var(--dsw-alias-label-primary)", cursor: "pointer", fontSize: 13, width: "100%" },
  // 侧边栏底部动作按钮：照搬官方设置 trigger（VOzbGW_trigger）——宽 calc(100%+8px)、
  // margin 4px -4px、高 34、圆角 12、14px/22px 排版，保证与下方"设置"按钮完全对齐。
  footTrigger: { boxSizing: "border-box", cursor: "pointer", width: "calc(100% + 8px)", height: 34, color: "var(--dsw-alias-label-primary)", background: "transparent", border: "none", borderRadius: 12, flex: "none", display: "flex", alignItems: "center", gap: 8, margin: "4px -4px", padding: "6px 2px 6px 10px", fontFamily: "inherit", fontSize: 14, lineHeight: "22px", overflow: "hidden" },
  // rail（侧边栏收起）变体：官方 trigger rail——36px 圆形、仅图标。
  footTriggerRail: { borderRadius: "50%", justifyContent: "center", gap: 0, width: 36, height: 36, margin: "8px 0 10px", padding: 0 },
  // 板块头/面板小按钮：官方 iconButton 观感——无边框、hover 圆角背景。
  closeBtn: { flex: "none", height: 28, padding: "0 10px", borderRadius: 14, border: "none", background: "transparent", color: "var(--dsw-alias-label-secondary)", cursor: "pointer", fontSize: 12, lineHeight: 1, display: "inline-flex", alignItems: "center" },
  scroll: { flex: 1, minHeight: 0, overflowY: "auto" }
};
function initials(name) {
  if (!name) return "?";
  const parts = String(name).trim().split(/\s+/);
  return (parts[0][0] || "?").toUpperCase();
}
function fmtTime(raw) {
  if (!raw) return "";
  const m = String(raw).match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/);
  if (!m) return "";
  const [, , month, day, hh, mm] = m;
  const now = /* @__PURE__ */ new Date();
  const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  const datePart = `${m[1]}-${month}-${day}`;
  const hm = `${hh}:${mm}`;
  return datePart === today ? hm : `${month}-${day} ${hm}`;
}
function LoginForm() {
  const [id, setId] = useState("");
  const [pw, setPw] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const submit = async () => {
    setBusy(true);
    setErr(null);
    try {
      await doLogin(id, pw);
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };
  return h(
    "div",
    { style: style.login },
    h(
      "div",
      { style: style.loginCard },
      h("div", { style: style.loginTitle }, "Copree \u767B\u5F55"),
      h("input", { style: style.field, placeholder: "\u7528\u6237\u540D / \u90AE\u7BB1", value: id, onChange: (e) => setId(e.target.value), onKeyDown: (e) => {
        if (e.key === "Enter") submit();
      } }),
      h("input", { style: style.field, placeholder: "\u5BC6\u7801", type: "password", value: pw, onChange: (e) => setPw(e.target.value), onKeyDown: (e) => {
        if (e.key === "Enter") submit();
      } }),
      h("button", { style: style.btn, onClick: submit, disabled: busy }, busy ? "\u767B\u5F55\u4E2D\u2026" : "\u767B\u5F55"),
      err ? h("div", { style: style.err }, err) : null,
      h("div", { style: style.hint }, "\u51ED\u636E\u4EC5\u4FDD\u5B58\u5728\u672C\u673A\u6D4F\u89C8\u5668\uFF0C\u901A\u8FC7\u672C\u5730\u540C\u6E90\u4EE3\u7406\u8BBF\u95EE Copree \u670D\u52A1\u3002")
    )
  );
}
function ContactRow({ contact, kind, active, onPick }) {
  const title = kind === "group" ? contact.name : contact.partner && contact.partner.name || "\u79C1\u4FE1";
  const sub = contact.last_message_preview || (contact.last_message_at ? "" : "");
  const unread = Number(contact.unread_count || 0);
  const avatarText = kind === "group" ? initials(title) : initials(contact.partner && contact.partner.name);
  const avatarSrc = kind === "group" ? avatarUrl(contact.avatar_url) : avatarUrl(contact.partner && contact.partner.avatar_url);
  const isActive = active && active.kind === kind && String(active.id) === String(kind === "group" ? contact.id : contact.session_id);
  return h(
    "button",
    {
      style: { ...style.row, ...isActive ? style.rowActive : {} },
      onMouseEnter: (e) => {
        if (!isActive) e.currentTarget.style.background = style.rowHover.background;
      },
      onMouseLeave: (e) => {
        if (!isActive) e.currentTarget.style.background = "transparent";
      },
      onClick: () => onPick(kind, kind === "group" ? contact.id : contact.session_id, title)
    },
    avatarSrc ? h("img", { src: avatarSrc, style: { ...style.avatar, objectFit: "cover" }, alt: "" }) : h("span", { style: style.avatar }, avatarText),
    h(
      "span",
      { style: style.rowText },
      h("div", { style: style.rowTitle }, title),
      sub ? h("div", { style: style.rowSub }, sub) : null
    ),
    unread > 0 ? h("span", { style: style.badge }, unread > 99 ? "99+" : String(unread)) : null
  );
}
function imageAttachments(m) {
  const atts = m && m.attachments;
  if (!Array.isArray(atts)) return [];
  return atts.filter((a) => a && (a.mime_type || "").startsWith("image/") && a.file_id != null);
}
function invitationAttachment(m) {
  if (!m || String(m.message_type) !== "group_invitation") return null;
  const atts = m.attachments;
  if (!Array.isArray(atts)) return null;
  return atts.find((a) => a && a.type === "group_invitation") || null;
}
async function respondInvitation(inv, accept) {
  if (!inv || !inv.invitation_id) return;
  try {
    await api(`/group-invitations/${encodeURIComponent(inv.invitation_id)}/${accept ? "accept" : "reject"}`, { method: "POST" });
    broadcast("message");
  } catch (e) {
    window.dispatchEvent(new CustomEvent("copree:error:" + (e.message || "\u64CD\u4F5C\u5931\u8D25")));
  }
}
var INVITE_STATUS_LABEL = { pending: "\u5F85\u5904\u7406", accepted: "\u5DF2\u63A5\u53D7", rejected: "\u5DF2\u62D2\u7EDD" };
function InviteCard({ inv }) {
  const label = INVITE_STATUS_LABEL[inv.status] || inv.status || "";
  const pending = inv.status === "pending";
  return h(
    "div",
    { style: style.inviteCard },
    h(
      "div",
      { style: style.inviteHead },
      h("span", { style: { fontSize: 16 } }, "\u{1F4E8}"),
      h("span", {}, "\u7FA4\u804A\u9080\u8BF7")
    ),
    h(
      "div",
      { style: style.inviteBody },
      (inv.inviter_name || "\u6709\u4EBA") + " \u9080\u8BF7\u4F60\u52A0\u5165\u7FA4\u804A\u300C" + (inv.group_name || "\u672A\u77E5\u7FA4\u7EC4") + "\u300D"
    ),
    pending ? h(
      "div",
      { style: style.inviteActions },
      h("button", { style: { ...style.inviteBtn, ...style.inviteAccept }, onClick: () => respondInvitation(inv, true) }, "\u63A5\u53D7"),
      h("button", { style: { ...style.inviteBtn, ...style.inviteReject }, onClick: () => respondInvitation(inv, false) }, "\u62D2\u7EDD")
    ) : h("div", { style: style.inviteStatus }, label)
  );
}
function GroupSettings({ active }) {
  const [members, setMembers] = useState(null);
  const [info, setInfo] = useState(null);
  const [, force] = useState(0);
  const refresh = () => force((n) => n + 1);
  useEffect(() => {
    let alive = true;
    setMembers(null);
    api(`/groups/${encodeURIComponent(active.id)}`).then((g) => {
      if (alive) setInfo(g);
    }).catch(() => {
    });
    api(`/groups/${encodeURIComponent(active.id)}/members`).then((m) => {
      if (alive) setMembers(m);
    }).catch(() => {
    });
    return () => {
      alive = false;
    };
  }, [active.id]);
  const togglePin = async () => {
    try {
      await api(`/groups/${encodeURIComponent(active.id)}/pin`, { method: "POST" });
      store.contacts = null;
      loadContacts(true).catch(() => {
      });
      const g = await api(`/groups/${encodeURIComponent(active.id)}`);
      setInfo(g);
    } catch (e) {
      window.dispatchEvent(new CustomEvent("copree:error:" + (e.message || "\u64CD\u4F5C\u5931\u8D25")));
    }
  };
  const toggleDnd = async () => {
    try {
      if (info && info.dnd_until) {
        await api(`/groups/${encodeURIComponent(active.id)}/dnd/cancel`, { method: "POST" });
      } else {
        await api(`/groups/${encodeURIComponent(active.id)}/dnd`, { method: "POST", json: { duration_minutes: null } });
      }
      const g = await api(`/groups/${encodeURIComponent(active.id)}`);
      setInfo(g);
    } catch (e) {
      window.dispatchEvent(new CustomEvent("copree:error:" + (e.message || "\u64CD\u4F5C\u5931\u8D25")));
    }
  };
  const pinned = !!(info && info.is_pinned);
  const inDnd = !!(info && info.dnd_until);
  const roleLabel = (r) => r === "owner" ? "\u7FA4\u4E3B" : r === "admin" ? "\u7BA1\u7406\u5458" : "\u6210\u5458";
  return h(
    "div",
    { style: style.settingsPanel },
    h("div", { style: style.settingsTitle }, "\u7FA4\u804A\u8BBE\u7F6E"),
    info ? h(
      "div",
      { style: style.settingsRow },
      h("span", { style: style.settingsLabel }, "\u7F6E\u9876\u7FA4\u804A"),
      h("button", { style: { ...style.smallBtn, ...pinned ? style.smallBtnOn : {} }, onClick: togglePin }, pinned ? "\u5DF2\u7F6E\u9876" : "\u7F6E\u9876")
    ) : null,
    info ? h(
      "div",
      { style: style.settingsRow },
      h("span", { style: style.settingsLabel }, "\u514D\u6253\u6270"),
      h("button", { style: { ...style.smallBtn, ...inDnd ? style.smallBtnOn : {} }, onClick: toggleDnd }, inDnd ? "\u5DF2\u5F00\u542F" : "\u5F00\u542F")
    ) : null,
    info && info.announcement ? h("div", { style: { ...style.settingsHint, background: "var(--dsw-alias-bg-base)", borderRadius: 8, padding: "8px 10px" } }, "\u516C\u544A\uFF1A" + info.announcement) : null,
    h("div", { style: { ...style.settingsTitle, marginTop: 4 } }, "\u6210\u5458\uFF08" + (members ? members.length : "\u2026") + "\uFF09"),
    members ? h(
      "div",
      { style: { display: "flex", flexDirection: "column", gap: 2 } },
      members.map((m) => h(
        "div",
        { key: String(m.type) + ":" + String(m.id), style: style.memberRow },
        h("span", { style: style.memberName }, m.name),
        h("span", { style: style.memberRole }, roleLabel(m.role))
      ))
    ) : h("div", { style: style.settingsHint }, "\u52A0\u8F7D\u4E2D\u2026")
  );
}
function DmSettings({ active }) {
  const [, force] = useState(0);
  const refresh = () => force((n) => n + 1);
  const [info, setInfo] = useState(null);
  useEffect(() => {
    let alive = true;
    setInfo(null);
    api(`/dm/${encodeURIComponent(active.id)}?summary=true`).then((d) => {
      if (alive) setInfo(d);
    }).catch(() => {
    });
    return () => {
      alive = false;
    };
  }, [active.id]);
  const togglePin = async () => {
    try {
      await api(`/dm/${encodeURIComponent(active.id)}/pin`, { method: "POST" });
      store.contacts = null;
      loadContacts(true).catch(() => {
      });
      const d = await api(`/dm/${encodeURIComponent(active.id)}?summary=true`);
      setInfo(d);
    } catch (e) {
      window.dispatchEvent(new CustomEvent("copree:error:" + (e.message || "\u64CD\u4F5C\u5931\u8D25")));
    }
  };
  const toggleDnd = async () => {
    try {
      const inDnd2 = !!(info && info.my_dnd_until);
      await api(`/dm/${encodeURIComponent(active.id)}/dnd`, { method: "POST", json: { duration_minutes: inDnd2 ? 0 : null } });
      const d = await api(`/dm/${encodeURIComponent(active.id)}?summary=true`);
      setInfo(d);
    } catch (e) {
      window.dispatchEvent(new CustomEvent("copree:error:" + (e.message || "\u64CD\u4F5C\u5931\u8D25")));
    }
  };
  const pinned = !!(info && info.is_pinned);
  const inDnd = !!(info && info.my_dnd_until);
  return h(
    "div",
    { style: style.settingsPanel },
    h("div", { style: style.settingsTitle }, "\u79C1\u4FE1\u8BBE\u7F6E"),
    h(
      "div",
      { style: style.settingsRow },
      h("span", { style: style.settingsLabel }, "\u7F6E\u9876\u5BF9\u8BDD"),
      h("button", { style: { ...style.smallBtn, ...pinned ? style.smallBtnOn : {} }, onClick: togglePin }, pinned ? "\u5DF2\u7F6E\u9876" : "\u7F6E\u9876")
    ),
    h(
      "div",
      { style: style.settingsRow },
      h("span", { style: style.settingsLabel }, "\u514D\u6253\u6270"),
      h("button", { style: { ...style.smallBtn, ...inDnd ? style.smallBtnOn : {} }, onClick: toggleDnd }, inDnd ? "\u5DF2\u5F00\u542F" : "\u5F00\u542F")
    )
  );
}
var MessageBoundary = class extends React.Component {
  constructor(props) {
    super(props);
    this.state = { failed: false };
  }
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch(error) {
    console.warn("[copree] \u6D88\u606F\u6E32\u67D3\u5931\u8D25\uFF0C\u5DF2\u964D\u7EA7\u4E3A\u7EAF\u6587\u672C", error);
  }
  render() {
    if (this.state.failed) {
      return h("div", { style: { ...style.msgOtherBubble, whiteSpace: "pre-wrap" } }, String(this.props.text || ""));
    }
    return this.props.children;
  }
};
function MsgList({ messages, user }) {
  const listRef = useRef(null);
  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);
  if (!messages || messages.length === 0) {
    return h("div", { style: style.empty }, "\u6682\u65E0\u6D88\u606F\uFF0C\u53D1\u9001\u7B2C\u4E00\u6761\u5427");
  }
  return h(
    "div",
    { ref: listRef, style: style.msgs },
    messages.map((m) => {
      const mine = String(m.sender_type) === "user" || String(m.sender_type) === "human" ? String(m.sender_id) === String(user ? user.id : "") : false;
      const name = senderName(m, user);
      const avatarSrc = mine ? null : avatarUrl(m.sender_avatar_url);
      const images = imageAttachments(m);
      const inv = invitationAttachment(m);
      const hasText = String(m.content || "").trim() !== "";
      let body = null;
      if (inv) {
        body = h(InviteCard, { inv });
      } else {
        if (hasText) {
          body = h(
            "div",
            { style: mine ? style.msgMineBubble : style.msgOtherBubble },
            h(MarkdownText, { text: mdText(m.content), labels: MARKDOWN_LABELS })
          );
        }
        if (images.length > 0) {
          const gallery = h(
            "div",
            { style: style.msgImages },
            images.map((img) => h(AttachmentImage, {
              key: String(img.file_id),
              fileId: img.file_id,
              name: img.name || "",
              style: style.msgImage
            }))
          );
          body = body ? h("div", { style: { display: "flex", flexDirection: "column", gap: 6, maxWidth: "min(525px, 100%)" } }, body, gallery) : gallery;
        }
        if (!body) body = h("div", { style: mine ? style.msgMineBubble : style.msgOtherBubble }, "");
      }
      return h(
        "div",
        { key: m.id, style: { ...style.msgRow, ...mine ? style.msgMine : style.msgOther } },
        !mine && avatarSrc ? h("img", { src: avatarSrc, style: { ...style.avatar, width: 26, height: 26 }, alt: "" }) : null,
        h(
          "div",
          { style: { ...style.msgCol, ...mine ? style.msgColMine : {} } },
          h("div", { style: style.msgMeta }, name + (m.created_at ? " \xB7 " + fmtTime(m.created_at) : "")),
          h(MessageBoundary, { text: m.content }, body)
        )
      );
    })
  );
}
function ConversationColumn({ refresh, onImmersive }) {
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [worldId, setWorldId] = useState(null);
  const active = store.active;
  const user = store.user;
  const messages = store.messages;
  useEffect(() => {
    let alive = true;
    setWorldId(null);
    if (active && active.kind === "group") {
      api(`/worlds/by-entity?entity_type=group&entity_id=${encodeURIComponent(active.id)}`).then((d) => {
        if (alive && d && d.world_id != null) setWorldId(d.world_id);
      }).catch(() => {
      });
    }
    return () => {
      alive = false;
    };
  }, [active]);
  const send = async () => {
    const text = draft.trim();
    if (!text || sending) return;
    setSending(true);
    try {
      await sendMessage(text);
      setDraft("");
      if (!store.ws || store.ws.readyState !== WebSocket.OPEN) {
        await loadMessages(active).catch(() => {
        });
        refresh();
      }
    } catch (e) {
      window.dispatchEvent(new CustomEvent("copree:error:" + (e.message || "send failed")));
    } finally {
      setSending(false);
    }
  };
  if (!active) {
    return h(
      "div",
      { style: style.main },
      h("div", { style: style.mainHead }, "Copree"),
      h("div", { style: style.empty }, "\u4ECE\u5DE6\u4FA7\u9009\u62E9\u4E00\u4E2A\u5BF9\u8BDD")
    );
  }
  return h(
    "div",
    { style: style.main },
    h(
      "div",
      { style: style.mainHead },
      h("span", { style: { fontWeight: 600 } }, active.title),
      h("span", { style: { fontSize: 12, color: "var(--dsw-alias-label-tertiary)" } }, active.kind === "group" ? "\u7FA4\u804A" : "\u79C1\u4FE1"),
      worldId != null ? h("button", {
        style: { ...style.headBtn, color: "var(--dsw-alias-state-business-primary)", marginLeft: "auto" },
        onClick: () => onImmersive && onImmersive(worldId),
        title: "\u5728\u6C89\u6D78\u5F0F\u754C\u9762\u6253\u5F00"
      }, "\u6C89\u6D78\u5F0F") : null,
      h("button", {
        style: { ...style.headBtn, ...showSettings ? style.headBtnActive : {}, ...worldId != null ? {} : { marginLeft: "auto" } },
        onClick: () => setShowSettings((v) => !v),
        title: active.kind === "group" ? "\u7FA4\u804A\u8BBE\u7F6E" : "\u79C1\u4FE1\u8BBE\u7F6E"
      }, "\u2699")
    ),
    showSettings ? active.kind === "group" ? h(GroupSettings, { active }) : h(DmSettings, { active }) : null,
    h(MsgList, { messages, user }),
    h(
      "div",
      { style: style.composer },
      h("input", {
        style: style.input,
        placeholder: "\u8F93\u5165\u6D88\u606F\uFF0C\u56DE\u8F66\u53D1\u9001",
        value: draft,
        onChange: (e) => setDraft(e.target.value),
        onKeyDown: (e) => {
          if (e.key === "Enter") send();
        }
      }),
      h("button", { style: style.send, onClick: send, disabled: sending }, sending ? "\u2026" : "\u53D1\u9001")
    )
  );
}
function ImmersivePanel({ path, title, onClose }) {
  return h(
    "div",
    { style: style.immersive },
    h(
      "div",
      { style: style.immersiveBar },
      h("span", { style: { flex: 1 } }, title || "\u6C89\u6D78\u5F0F\u754C\u9762"),
      h("button", { style: style.closeBtn, onClick: onClose }, "\u8FD4\u56DE")
    ),
    h("iframe", { src: path, style: style.immersiveFrame, title: title || "\u6C89\u6D78\u5F0F\u754C\u9762", sandbox: "allow-scripts allow-same-origin allow-forms allow-popups" })
  );
}
var immersiveState = { path: null, title: "" };
function openImmersive(path, title) {
  const sep = path.includes("?") ? "&" : "?";
  immersiveState.path = path + sep + "token=" + encodeURIComponent(store.token || "");
  immersiveState.title = title || "";
  window.dispatchEvent(new CustomEvent("copree:immersive"));
}
function closeImmersive() {
  immersiveState.path = null;
  immersiveState.title = "";
  window.dispatchEvent(new CustomEvent("copree:immersive"));
}
function ImmersiveOverlay() {
  const [, force] = useState(0);
  useEffect(() => {
    const on = () => force((n) => n + 1);
    window.addEventListener("copree:immersive", on);
    return () => window.removeEventListener("copree:immersive", on);
  }, []);
  if (!immersiveState.path) return null;
  return h(ImmersivePanel, { path: immersiveState.path, title: immersiveState.title, onClose: closeImmersive });
}
var FEATURES = [
  { id: "worlds", label: "\u7FA4\u89C6\u754C", path: "/worlds" },
  { id: "friends", label: "\u597D\u53CB", path: "/friends" },
  { id: "agents", label: "\u6211\u7684 AI", path: "/agents" },
  { id: "admin", label: "\u7BA1\u7406", path: "/admin" },
  { id: "settings", label: "\u8BBE\u7F6E", path: "/settings" }
];
function AisChatBoard({ onClose }) {
  const [, force] = useState(0);
  const refresh = useCallback(() => force((n) => n + 1), []);
  const user = store.user;
  useEffect(() => {
    const onMsg = () => refresh();
    window.addEventListener("copree:message", onMsg);
    window.addEventListener("copree:auth", onMsg);
    window.addEventListener("copree:error", (e) => {
      console.warn("[copree]", e.detail);
    });
    return () => {
      window.removeEventListener("copree:message", onMsg);
      window.removeEventListener("copree:auth", onMsg);
    };
  }, [refresh]);
  useEffect(() => {
    if (store.token) {
      loadContacts().then(() => refresh()).catch(() => {
      });
      connectWs();
    }
    return () => {
    };
  }, [refresh]);
  const pick = async (kind, id, title) => {
    store.active = { kind, id, title };
    store.messages = null;
    refresh();
    try {
      const list = await loadMessages(store.active);
      store.messages = list;
      refresh();
    } catch {
    }
    connectWs();
  };
  if (!user || !store.token) {
    return h(
      "div",
      { style: style.board },
      h(
        "div",
        { style: style.rail },
        h(
          "div",
          { style: style.railHead },
          h("span", {}, "Copree"),
          h("button", { style: style.closeBtn, onClick: onClose }, "\u8FD4\u56DE\u5DE5\u4F5C\u533A")
        )
      ),
      h(LoginForm, null)
    );
  }
  const contacts = store.contacts;
  const pinnedGroups = (contacts && contacts.groups || []).filter((g) => g.is_pinned);
  const pinnedDms = (contacts && contacts.dms || []).filter((d) => d.is_pinned);
  const restGroups = (contacts && contacts.groups || []).filter((g) => !g.is_pinned);
  const restDms = (contacts && contacts.dms || []).filter((d) => !d.is_pinned);
  const section = (label, items, kind) => items.length ? h(
    "div",
    { style: style.group },
    h("div", { style: style.groupLabel }, label),
    items.map((it) => h(ContactRow, { key: kind === "group" ? String(it.id) : String(it.session_id), contact: it, kind, active: store.active, onPick: pick }))
  ) : null;
  return h(
    "div",
    { style: style.board },
    h(
      "div",
      { style: style.rail },
      h(
        "div",
        { style: style.railHead },
        h("span", { style: style.railLabel }, "Copree"),
        h("button", { style: { ...style.closeBtn, marginLeft: "auto", fontSize: 12 }, onClick: onClose }, "\u8FD4\u56DE\u5DE5\u4F5C\u533A")
      ),
      h(
        "div",
        { style: style.railUser },
        h("span", { style: style.avatar }, initials(user.name)),
        h("span", { style: { flex: 1, fontSize: 13, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" } }, user.name),
        h("button", { style: { ...style.closeBtn, fontSize: 11 }, onClick: doLogout, title: "\u9000\u51FA\u767B\u5F55" }, "\u9000\u51FA")
      ),
      h(
        "div",
        { style: style.scroll },
        section("\u7F6E\u9876\u79C1\u4FE1", pinnedDms, "dm"),
        section("\u7F6E\u9876\u7FA4\u804A", pinnedGroups, "group"),
        section("\u79C1\u4FE1", restDms, "dm"),
        section("\u7FA4\u804A", restGroups, "group"),
        !pinnedDms.length && !pinnedGroups.length && !restDms.length && !restGroups.length ? h("div", { style: { ...style.empty, padding: 24 } }, "\u6682\u65E0\u8054\u7CFB\u4EBA") : null,
        h(
          "div",
          { style: { ...style.group, marginTop: 10, borderTop: "1px solid var(--dsw-alias-border-l2)", paddingTop: 8 } },
          h("div", { style: style.groupLabel }, "\u529F\u80FD"),
          FEATURES.map((f) => h("button", {
            key: f.id,
            style: { ...style.row, fontSize: 13 },
            onClick: () => openImmersive(`/copree-ui${f.path}?embed=1`, f.label)
          }, f.label))
        )
      )
    ),
    h(ConversationColumn, {
      refresh,
      onImmersive: (wid) => openImmersive(`/copree-ui/world-view/${encodeURIComponent(wid)}?embed=1`, "\u6C89\u6D78\u5F0F\u754C\u9762")
    })
  );
}
async function readConsentState() {
  const res = await fetch(CONSENT_API, { cache: "no-store" });
  const text = await res.text();
  if (!res.ok || !text.trim()) throw new Error("HOST_STALE");
  const data = JSON.parse(text);
  return data.allowed === true;
}
var CONSENT_STALE_HINT = "\u5BBF\u4E3B\u534A\u4FA7\u8FD8\u6CA1\u52A0\u8F7D\u8FD9\u4E2A\u7AEF\u70B9\uFF08\u63D2\u4EF6\u521A\u66F4\u65B0\u8FC7\uFF09\uFF1A\u91CD\u542F dsh-web \u540E\u5237\u65B0\u672C\u9875\u5373\u53EF";
function BridgeConsent() {
  const [allowed, setAllowed] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  useEffect(() => {
    let alive = true;
    readConsentState().then((value) => {
      if (alive) {
        setAllowed(value);
        setErr("");
      }
    }).catch((e) => {
      if (alive) {
        setAllowed(null);
        setErr(e.message === "HOST_STALE" ? CONSENT_STALE_HINT : "\u8BFB\u53D6\u5931\u8D25\uFF1A" + e.message);
      }
    });
    return () => {
      alive = false;
    };
  }, []);
  const set = async (next) => {
    setBusy(true);
    setErr("");
    try {
      const res = await fetch(CONSENT_API, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ allowed: next })
      });
      const text = await res.text();
      let data = null;
      try {
        data = text ? JSON.parse(text) : null;
      } catch {
        data = null;
      }
      if (!res.ok || !data || typeof data.allowed !== "boolean") {
        throw new Error(data && data.error ? data.error : res.ok ? "HOST_STALE" : String(res.status));
      }
      setAllowed(data.allowed === true);
    } catch (e) {
      setErr(e.message === "HOST_STALE" ? CONSENT_STALE_HINT : "\u5207\u6362\u5931\u8D25\uFF1A" + e.message);
    } finally {
      setBusy(false);
    }
  };
  const state = allowed === null ? "\u72B6\u6001\u672A\u77E5" : allowed ? "\u5DF2\u540C\u610F\u63A5\u5165" : "\u672A\u540C\u610F\u63A5\u5165";
  return h(
    "div",
    { style: { border: "1px solid var(--dsw-alias-border-l2, #e4e4e7)", borderRadius: 10, padding: 12, marginBottom: 18 } },
    h("div", { style: { fontSize: 13, fontWeight: 600, color: "var(--dsw-alias-label-primary)" } }, "Copree \u53CD\u5411\u63A5\u5165"),
    h(
      "div",
      { style: { fontSize: 12, marginTop: 4, color: "var(--dsw-alias-label-secondary)" } },
      "\u540C\u610F\u540E\uFF0CCopree \u7BA1\u7406\u7AEF\u624D\u80FD\u770B\u5230\u672C\u673A DSH \u5E76\u6309\u7BA1\u7406\u5458\u6307\u4EE4\u64CD\u4F5C\u4F1A\u8BDD\uFF1B\u672A\u540C\u610F\u65F6\u672C\u673A\u4E0D\u53D1\u5FC3\u8DF3\u3001\u4E0D\u5F00\u653E\u4EFB\u4F55\u6865\u63A5\u7AEF\u70B9\u3002\u5F53\u524D\uFF1A" + state
    ),
    h(
      "div",
      { style: { display: "flex", gap: 8, marginTop: 10 } },
      h("button", {
        style: { ...style.smallBtn, opacity: busy || allowed === true ? 0.55 : 1 },
        disabled: busy || allowed === true,
        onClick: () => {
          void set(true);
        }
      }, "\u540C\u610F\u63A5\u5165"),
      h("button", {
        style: { ...style.smallBtn, opacity: busy || allowed === false ? 0.55 : 1 },
        disabled: busy || allowed === false,
        onClick: () => {
          void set(false);
        }
      }, "\u64A4\u9500\u540C\u610F")
    ),
    err ? h("div", { style: { ...style.hint, marginTop: 6 } }, err) : null
  );
}
function SettingsPage() {
  const [, force] = useState(0);
  const refresh = useCallback(() => force((n) => n + 1), []);
  const user = store.user;
  const [plugin, setPlugin] = useState(null);
  const [pluginMsg, setPluginMsg] = useState("");
  const [pluginBusy, setPluginBusy] = useState(false);
  useEffect(() => {
    window.addEventListener("copree:auth", refresh);
    let alive = true;
    pluginApi("/status").then((s) => {
      if (alive) setPlugin(s);
    }).catch(() => {
    });
    return () => {
      alive = false;
      window.removeEventListener("copree:auth", refresh);
    };
  }, [refresh]);
  const applyPluginUpdate = async () => {
    setPluginBusy(true);
    setPluginMsg("");
    const viaPackageManager = Boolean(plugin) && plugin.updateChannel === "package-manager";
    try {
      const result = await pluginApi(viaPackageManager ? "/update" : "/apply", { method: "POST" });
      if (result.applyMode === "restart") {
        setPluginMsg(viaPackageManager ? "\u5DF2\u5B8C\u6210\u3002host \u534A\u6709\u53D8\u5316\uFF0C\u9700\u91CD\u542F dsh-web \u540E\u751F\u6548\u3002" : `\u5DF2\u6362\u5165 ${result.changed.length} \u4E2A\u6587\u4EF6\u3002host \u534A\u5DF2\u53D8\u66F4\uFF0C\u9700\u91CD\u542F dsh-web \u540E\u751F\u6548\u3002`);
        setPlugin(await pluginApi("/status").catch(() => plugin));
      } else if (viaPackageManager) {
        setPluginMsg("\u5DF2\u662F\u6700\u65B0\uFF0C\u6216\u5DF2\u66F4\u65B0\u5230\u6700\u65B0\u3002");
        setPlugin(await pluginApi("/status").catch(() => plugin));
      } else {
        setPluginMsg("\u5DF2\u66F4\u65B0\uFF0C\u6B63\u5728\u5237\u65B0\u9875\u9762\u2026");
        setTimeout(() => window.location.reload(), 600);
      }
    } catch (e) {
      setPluginMsg(`\u66F4\u65B0\u5931\u8D25\uFF1A${e.message}`);
    } finally {
      setPluginBusy(false);
    }
  };
  if (!user || !store.token) {
    return h(
      "div",
      { style: { padding: 20, maxWidth: 420 } },
      h("div", { style: { fontSize: 16, fontWeight: 600, marginBottom: 16, color: "var(--dsw-alias-label-primary)" } }, "Copree"),
      // 闸门放登录之前：允不允许 Copree 接入，与"我在 Copree 有没有账号"是两件事
      h(BridgeConsent, null),
      h("div", { style: { ...style.hint, marginTop: 0 } }, "\u767B\u5F55 Copree \u540E\u5373\u53EF\u5728\u4FA7\u8FB9\u680F\u4F7F\u7528\u804A\u5929\u3002\u51ED\u636E\u4EC5\u4FDD\u5B58\u5728\u672C\u673A\u6D4F\u89C8\u5668\u3002"),
      h(LoginForm, null)
    );
  }
  return h(
    "div",
    { style: { padding: 20, maxWidth: 480 } },
    h("div", { style: { fontSize: 16, fontWeight: 600, marginBottom: 16, color: "var(--dsw-alias-label-primary)" } }, "Copree"),
    h(BridgeConsent, null),
    h(
      "div",
      { style: { ...style.row, padding: "8px 0" } },
      h("span", { style: style.avatar }, initials(user.name)),
      h(
        "div",
        { style: style.rowText },
        h("div", { style: style.rowTitle }, user.name),
        h("div", { style: style.rowSub }, "\u5DF2\u767B\u5F55")
      )
    ),
    h("div", { style: { fontSize: 13, fontWeight: 600, margin: "18px 0 8px", color: "var(--dsw-alias-label-primary)" } }, "\u529F\u80FD"),
    h(
      "div",
      { style: { display: "flex", flexDirection: "column", gap: 2 } },
      FEATURES.map((f) => h("button", {
        key: f.id,
        style: { ...style.footBtn, padding: "9px 10px", fontSize: 14 },
        onClick: () => openImmersive(`/copree-ui${f.path}?embed=1`, f.label)
      }, f.label))
    ),
    h("div", { style: { fontSize: 13, fontWeight: 600, margin: "18px 0 8px", color: "var(--dsw-alias-label-primary)" } }, "\u63D2\u4EF6"),
    h(
      "div",
      { style: { ...style.row, padding: "6px 0" } },
      h(
        "div",
        { style: style.rowText },
        h(
          "div",
          { style: style.rowTitle },
          plugin ? `dsh-copree ${plugin.installed && plugin.installed.version || "\u672A\u77E5"} \xB7 ${shortId(plugin.installed && plugin.installed.id)}` : "dsh-copree \u7248\u672C\u68C0\u6D4B\u4E2D\u2026"
        ),
        h("div", { style: style.rowSub }, pluginStateText(plugin))
      ),
      pluginCanAct(plugin) ? h("button", {
        style: style.smallBtn,
        disabled: pluginBusy,
        onClick: applyPluginUpdate
      }, pluginBusy ? "\u5904\u7406\u4E2D\u2026" : plugin.updateChannel === "package-manager" ? "\u68C0\u67E5\u66F4\u65B0" : "\u66F4\u65B0") : null
    ),
    plugin && plugin.backend && plugin.backend.mismatch ? h(
      "div",
      { style: { ...style.hint, marginTop: 2 } },
      `\u63D2\u4EF6\u6784\u5EFA\u65F6\u540E\u7AEF\u4E3A ${plugin.backend.builtAgainst}\uFF0C\u5F53\u524D\u4E3A ${plugin.backend.running}\uFF1B\u5185\u7F6E\u754C\u9762\u53EF\u80FD\u5DF2\u4E0E\u540E\u7AEF API \u4E0D\u4E00\u81F4\uFF0C\u5EFA\u8BAE\u66F4\u65B0\u63D2\u4EF6\u3002`
    ) : null,
    pluginMsg ? h("div", { style: { ...style.hint, marginTop: 2 } }, pluginMsg) : null,
    h("button", { style: { ...style.btn, background: "var(--dsw-alias-state-danger-primary, #e5484d)", marginTop: 20 }, onClick: doLogout }, "\u9000\u51FA\u767B\u5F55"),
    h("div", { style: style.hint }, "\u670D\u52A1\u901A\u8FC7\u672C\u673A\u540C\u6E90\u4EE3\u7406\u8BBF\u95EE\uFF0C\u65E0\u516C\u7F51\u5730\u5740\u53C2\u4E0E\u3002")
  );
}
function FooterButton({ wide }) {
  const [open, setOpen] = useState(false);
  const [needsUpdate, setNeedsUpdate] = useState(false);
  useEffect(() => {
    let alive = true;
    pluginApi("/status").then((s) => {
      if (alive) setNeedsUpdate(pluginNeedsAttention(s));
    }).catch(() => {
    });
    return () => {
      alive = false;
    };
  }, []);
  useEffect(() => {
    const onRefresh = () => setOpen(boardOpenRef.current);
    window.addEventListener("copree:board-refresh", onRefresh);
    return () => window.removeEventListener("copree:board-refresh", onRefresh);
  }, []);
  const toggle = () => {
    const next = !open;
    setOpen(next);
    window.dispatchEvent(new CustomEvent("copree:board-toggle", { detail: next }));
  };
  const rail = wide === false;
  return h(
    "button",
    {
      style: { ...style.footTrigger, ...rail ? style.footTriggerRail : {}, ...open ? { background: "var(--dsw-alias-interactive-bg-hover-solid, var(--dsw-alias-interactive-bg-hover))" } : {} },
      onClick: toggle,
      title: "Copree \u804A\u5929",
      "aria-label": rail ? "Copree" : void 0
    },
    h(IconNewChatOutline16, { size: rail ? 18 : 16 }),
    rail ? null : h("span", { style: { fontWeight: 500 } }, "Copree"),
    needsUpdate ? h("span", {
      title: "Copree \u63D2\u4EF6\u6709\u66F4\u65B0",
      style: { flex: "none", width: 7, height: 7, borderRadius: "50%", background: "var(--dsw-alias-state-danger-primary, #e5484d)" }
    }) : null
  );
}
var boardOpenRef = { current: false };
module.exports = {
  name: "dsh-copree",
  inject: ["slots", "workspaces"],
  apply(ctx) {
    let boardOpen = false;
    const bump = () => {
      boardOpenRef.current = boardOpen;
      window.dispatchEvent(new CustomEvent("copree:board-refresh"));
    };
    let lastWorldsSync = 0;
    const syncWorlds = async (force = false) => {
      if (!store.token || !store.user || !ctx.workspaces) return;
      const now = Date.now();
      if (!force && now - lastWorldsSync < 3e4) return;
      lastWorldsSync = now;
      try {
        const worlds = await api("/worlds").catch(() => null);
        if (!Array.isArray(worlds)) return;
        for (const w of worlds) {
          if (!w || !w.id) continue;
          const dirRes = await fetch("/copree-worlds/dir", {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ worldId: w.id, name: w.name || `\u4E16\u754C${w.id}` })
          }).catch(() => null);
          if (!dirRes || !dirRes.ok) continue;
          const dir = await dirRes.json().catch(() => null);
          if (!dir || !dir.path) continue;
          const ws = await ctx.workspaces.create({ path: dir.path }).catch(() => null);
          if (!ws) continue;
          const workspaceId = ws.workspaceId || ws.id;
          if (!workspaceId) continue;
          await ctx.workspaces.connectWorkspace(workspaceId).catch(() => null);
          await fetch("/copree-worlds/token", {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ worldId: w.id, token: store.token })
          }).catch(() => {
          });
          await fetch("/copree-worlds/pull", {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ worldId: w.id })
          }).catch(() => {
          });
        }
      } catch {
      }
    };
    window.addEventListener("copree:auth", () => {
      if (store.token) syncWorlds();
    });
    if (store.token && store.user) syncWorlds();
    window.addEventListener("copree:board-toggle", (e) => {
      boardOpen = !!e.detail;
      if (boardOpen && store.token) syncWorlds(true);
      bump();
    });
    window.addEventListener("copree:error", (e) => {
      console.warn("[copree]", e.detail);
    });
    const disposers = [];
    const onConnectionReset = () => {
      if (store.token && store.user) syncWorlds();
    };
    if (typeof ctx.on === "function") disposers.push(ctx.on("connection/reset", onConnectionReset));
    const syncTimer = setInterval(() => {
      if (store.token && store.user) syncWorlds();
    }, 6e4);
    disposers.push(() => clearInterval(syncTimer));
    disposers.push(ctx.slots.inject("sidebar.footer.action", () => ctx.slots.register(
      { name: "sidebar.footer.action", id: "copree-entry", order: 10, label: "Copree" },
      FooterButton
    )));
    const BoardEntry = () => {
      const [openState, setOpenState] = useState(false);
      const [, force] = useState(0);
      useEffect(() => {
        const onRefresh = () => {
          setOpenState(boardOpen);
          force((n) => n + 1);
        };
        window.addEventListener("copree:board-refresh", onRefresh);
        return () => window.removeEventListener("copree:board-refresh", onRefresh);
      }, []);
      if (!openState) return null;
      return h(AisChatBoard, {
        onClose: () => {
          boardOpen = false;
          bump();
        }
      });
    };
    disposers.push(ctx.slots.inject("shell.overlay", () => ctx.slots.register(
      { name: "shell.overlay", id: "copree-board", order: 30 },
      BoardEntry
    )));
    disposers.push(ctx.slots.inject("shell.overlay", () => ctx.slots.register(
      { name: "shell.overlay", id: "copree-immersive", order: 40 },
      ImmersiveOverlay
    )));
    disposers.push(ctx.slots.inject("settings.section", () => ctx.slots.register(
      { name: "settings.section", id: "copree", order: 40, label: "Copree" },
      SettingsPage
    )));
    window.addEventListener("focus", () => {
      if (store.token && !store.ws) connectWs();
    });
    window.addEventListener("message", (event) => {
      const data = event.data;
      if (!data || typeof data !== "object" || data.source !== "copree-embed") return;
      if (data.type === "request-login" || data.type === "unauthorized") {
        if (!boardOpen) {
          boardOpen = true;
          bump();
        }
      }
    });
    return () => {
      for (const dispose of disposers.splice(0)) dispose();
      closeWs();
    };
  }
};
return module.exports; } });
//# sourceMappingURL=client.js.map
