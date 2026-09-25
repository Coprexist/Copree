// src/index.ts
import http from "node:http";
import z from "@deepseek-ai/schemastery";
import { createReadStream, existsSync as existsSync2, statSync, mkdirSync as mkdirSync3, readFileSync as readFileSync3, writeFileSync as writeFileSync3, realpathSync, readdirSync, unlinkSync } from "node:fs";
import { join as join3, normalize, extname, sep } from "node:path";
import os from "node:os";

// src/plugin-update.ts
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import {
  copyFileSync,
  existsSync,
  mkdirSync,
  readFileSync,
  renameSync,
  rmSync,
  writeFileSync
} from "node:fs";
import { basename, dirname, isAbsolute, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
var PLUGIN_NAME = "dsh-copree";
var MANIFEST_REL = "lib/manifest.json";
var PLUGIN_PREFIX = "/copree-plugin";
var HOST_ENTRY = "lib/index.js";
var STAGING_DIR = ".copree-plugin-staging";
var BACKUP_DIR = ".copree-plugin-previous";
var PACKAGE_ROOT = join(fileURLToPath(new URL(".", import.meta.url)), "..");
function sha256File(path) {
  return createHash("sha256").update(readFileSync(path)).digest("hex");
}
function manifestId(manifest) {
  const canonical = JSON.stringify(
    Object.keys(manifest.files).sort().map((rel) => [rel, manifest.files[rel]])
  );
  return createHash("sha256").update(canonical).digest("hex");
}
function readManifest(root) {
  const path = join(root, MANIFEST_REL);
  if (!existsSync(path)) return null;
  try {
    const parsed = JSON.parse(readFileSync(path, "utf8"));
    if (!parsed || typeof parsed !== "object" || !parsed.files || typeof parsed.files !== "object") return null;
    return parsed;
  } catch {
    return null;
  }
}
function packageNameOf(root) {
  try {
    const pkg = JSON.parse(readFileSync(join(root, "package.json"), "utf8"));
    return typeof pkg.name === "string" ? pkg.name : null;
  } catch {
    return null;
  }
}
function resolveSourceRoot(installRoot, explicit) {
  if (explicit && explicit.trim()) {
    const root = resolve(explicit.trim());
    return packageNameOf(root) === PLUGIN_NAME ? { root, how: "config" } : { root: null, how: "config-invalid" };
  }
  const profileRoot = dirname(dirname(installRoot));
  if (packageNameOf(profileRoot) === null && !existsSync(join(profileRoot, "package.json"))) {
    return { root: null, how: "no-profile-package" };
  }
  try {
    const pkg = JSON.parse(readFileSync(join(profileRoot, "package.json"), "utf8"));
    const spec = pkg.dependencies?.[PLUGIN_NAME];
    if (!spec || !spec.startsWith("file:")) return { root: null, how: "no-file-spec" };
    const raw = spec.slice("file:".length);
    const root = isAbsolute(raw) ? raw : resolve(profileRoot, raw);
    return packageNameOf(root) === PLUGIN_NAME ? { root, how: "profile-file-spec" } : { root: null, how: "source-missing" };
  } catch {
    return { root: null, how: "no-profile-package" };
  }
}
async function fetchBackendVersion(backendUrl) {
  try {
    const res = await fetch(new URL("/health", backendUrl.endsWith("/") ? backendUrl : backendUrl + "/"), {
      signal: AbortSignal.timeout(1500)
    });
    if (!res.ok) return null;
    const body = await res.json();
    return typeof body.version === "string" ? body.version : null;
  } catch {
    return null;
  }
}
var MARKET_PLUGIN = "dshmarket";
function missingArtifacts(root, manifest) {
  return Object.keys(manifest.files).filter((rel) => !existsSync(join(root, rel)));
}
function profileRootOf(installRoot) {
  return dirname(dirname(installRoot));
}
function readProfileDependencies(installRoot) {
  try {
    const pkg = JSON.parse(readFileSync(join(profileRootOf(installRoot), "package.json"), "utf8"));
    return pkg.dependencies ?? {};
  } catch {
    return null;
  }
}
function classifySpec(spec) {
  if (!spec) return "unknown";
  if (spec.startsWith("file:") || spec.startsWith("link:")) return "local-file";
  if (spec.startsWith("github:") || spec.startsWith("git+") || spec.startsWith("git:")) return "git";
  if (/^https?:\/\/(github\.com|codeload\.github\.com)\//.test(spec)) return "git";
  return "npm";
}
function summarize(manifest) {
  return manifest ? { version: manifest.version, id: manifestId(manifest), buildStamp: manifest.buildStamp } : null;
}
async function computeStatus(installRoot, backendUrl, explicitSource) {
  const installedManifest = readManifest(installRoot);
  const dependencies = readProfileDependencies(installRoot);
  const installKind = classifySpec(dependencies?.[PLUGIN_NAME]);
  const marketInstalled = Boolean(dependencies?.[MARKET_PLUGIN]);
  const source = resolveSourceRoot(installRoot, explicitSource);
  const availableManifest = source.root ? readManifest(source.root) : null;
  const running = await fetchBackendVersion(backendUrl);
  const installed = summarize(installedManifest);
  const available = summarize(availableManifest);
  const missing = installedManifest ? missingArtifacts(installRoot, installedManifest) : [];
  const updateChannel = installKind === "local-file" ? "self" : installKind === "unknown" ? "unavailable" : marketInstalled ? "market" : "package-manager";
  let state;
  let reason = null;
  if (!installed) {
    state = "not-installed";
  } else if (missing.length) {
    state = "update-available";
    reason = "incomplete";
  } else if (updateChannel === "market" || updateChannel === "package-manager") {
    state = "up-to-date";
  } else if (!available) {
    state = "source-unavailable";
  } else if (installed.id !== available.id) {
    state = "update-available";
    reason = "behind";
  } else {
    state = "up-to-date";
  }
  const applyMode = installedManifest && availableManifest && installedManifest.files[HOST_ENTRY] !== availableManifest.files[HOST_ENTRY] ? "restart" : "hot";
  const builtAgainst = installedManifest?.backendVersion ?? null;
  return {
    installed,
    available,
    source,
    installKind,
    updateChannel,
    marketInstalled,
    state,
    reason,
    missing: missing.length,
    applyMode,
    backend: {
      builtAgainst,
      running,
      mismatch: Boolean(builtAgainst && running && builtAgainst !== running)
    }
  };
}
var applying = false;
function applyUpdate(installRoot, sourceRoot) {
  if (applying) return { ok: false, changed: [], applyMode: "hot", error: "\u5DF2\u6709\u66F4\u65B0\u6B63\u5728\u8FDB\u884C" };
  applying = true;
  const staging = join(installRoot, STAGING_DIR);
  try {
    const manifest = readManifest(sourceRoot);
    if (!manifest) {
      return { ok: false, changed: [], applyMode: "hot", error: "\u66F4\u65B0\u6E90\u6CA1\u6709\u6784\u5EFA\u6E05\u5355\uFF0C\u8BF7\u5148\u5728\u6E90\u7801\u76EE\u5F55\u6267\u884C node scripts/build.mjs" };
    }
    const previous = readManifest(installRoot);
    const rels = Object.keys(manifest.files).sort();
    rmSync(staging, { recursive: true, force: true });
    for (const rel of rels) {
      const src = join(sourceRoot, rel);
      if (!existsSync(src)) {
        return { ok: false, changed: [], applyMode: "hot", error: `\u66F4\u65B0\u6E90\u7F3A\u5C11\u6E05\u5355\u5217\u51FA\u7684\u6587\u4EF6\uFF1A${rel}` };
      }
      const actual = sha256File(src);
      if (actual !== manifest.files[rel]) {
        return { ok: false, changed: [], applyMode: "hot", error: `\u66F4\u65B0\u6E90\u6587\u4EF6\u4E0E\u6E05\u5355\u4E0D\u7B26\uFF1A${rel}\uFF08\u6E90\u7801\u53EF\u80FD\u5DF2\u6539\u52A8\u4F46\u672A\u91CD\u65B0\u6784\u5EFA\uFF09` };
      }
      const staged = join(staging, rel);
      mkdirSync(dirname(staged), { recursive: true });
      copyFileSync(src, staged);
    }
    const hostChanged = previous?.files[HOST_ENTRY] !== manifest.files[HOST_ENTRY];
    const backup = join(installRoot, BACKUP_DIR);
    const changed = [];
    for (const rel of [...rels, MANIFEST_REL]) {
      const target = join(installRoot, rel);
      if (existsSync(target)) {
        const saved = join(backup, rel);
        mkdirSync(dirname(saved), { recursive: true });
        copyFileSync(target, saved);
      }
      if (rel === MANIFEST_REL) continue;
      if (!existsSync(target) || sha256File(target) !== manifest.files[rel]) changed.push(rel);
      mkdirSync(dirname(target), { recursive: true });
      renameSync(join(staging, rel), target);
    }
    writeFileSync(join(installRoot, MANIFEST_REL), JSON.stringify(manifest, null, 2), "utf8");
    return {
      ok: true,
      changed,
      applyMode: hostChanged ? "restart" : "hot",
      installed: { version: manifest.version, id: manifestId(manifest) }
    };
  } catch (e) {
    return { ok: false, changed: [], applyMode: "hot", error: String(e.message ?? e) };
  } finally {
    rmSync(staging, { recursive: true, force: true });
    applying = false;
  }
}
function updateViaPackageManager(installRoot) {
  const profileRoot = profileRootOf(installRoot);
  const profile = basename(profileRoot);
  const hostEntry = join(installRoot, HOST_ENTRY);
  const before = existsSync(hostEntry) ? sha256File(hostEntry) : null;
  return new Promise((resolve2) => {
    let child;
    try {
      child = spawn("dsh", ["plugin", "--profile", profile, "update", PLUGIN_NAME], {
        cwd: profileRoot,
        env: process.env
      });
    } catch (e) {
      resolve2({ ok: false, profile, output: "", applyMode: "hot", error: String(e.message ?? e) });
      return;
    }
    let output = "";
    const collect = (buf) => {
      output += buf.toString("utf8");
    };
    child.stdout?.on("data", collect);
    child.stderr?.on("data", collect);
    child.on("error", (e) => {
      resolve2({ ok: false, profile, output, applyMode: "hot", error: String(e.message ?? e) });
    });
    child.on("close", (code) => {
      const after = existsSync(hostEntry) ? sha256File(hostEntry) : null;
      resolve2({
        ok: code === 0,
        profile,
        output: output.slice(-4e3),
        applyMode: before !== after ? "restart" : "hot",
        error: code === 0 ? void 0 : `dsh plugin update \u9000\u51FA\u7801 ${code}`
      });
    });
  });
}
function rollback(installRoot) {
  const backup = join(installRoot, BACKUP_DIR);
  const manifest = readManifest(backup);
  if (!manifest) return { ok: false, changed: [], applyMode: "hot", error: "\u6CA1\u6709\u53EF\u56DE\u6EDA\u7684\u5907\u4EFD" };
  try {
    const restored = [];
    for (const rel of [...Object.keys(manifest.files), MANIFEST_REL]) {
      const src = join(backup, rel);
      if (!existsSync(src)) continue;
      const target = join(installRoot, rel);
      mkdirSync(dirname(target), { recursive: true });
      copyFileSync(src, target);
      restored.push(rel);
    }
    return {
      ok: true,
      changed: restored,
      applyMode: "restart",
      installed: { version: manifest.version, id: manifestId(manifest) }
    };
  } catch (e) {
    return { ok: false, changed: [], applyMode: "hot", error: String(e.message ?? e) };
  }
}
function registerPluginRoutes(register, opts) {
  register({
    kind: "prefix",
    path: PLUGIN_PREFIX,
    handler: (req, res) => {
      const route = (req.url ?? "/").split("?")[0];
      const send = (status, payload) => {
        res.writeHead(status, { "content-type": "application/json; charset=utf-8" });
        res.end(JSON.stringify(payload));
      };
      if (req.method === "GET" && route === `${PLUGIN_PREFIX}/status`) {
        computeStatus(opts.installRoot, opts.backendUrl, opts.sourceDir).then((status) => send(200, status)).catch((e) => send(500, { error: String(e?.message ?? e) }));
        return;
      }
      if (req.method === "POST" && route === `${PLUGIN_PREFIX}/apply`) {
        const resolved = resolveSourceRoot(opts.installRoot, opts.sourceDir);
        if (!resolved.root) {
          send(409, { ok: false, error: `\u65E0\u6CD5\u5B9A\u4F4D\u66F4\u65B0\u6E90\uFF08${resolved.how}\uFF09`, source: resolved });
          return;
        }
        const result = applyUpdate(opts.installRoot, resolved.root);
        opts.log?.(result.ok ? `updated ${result.changed.length} file(s), applyMode=${result.applyMode}` : `update failed: ${result.error}`);
        send(result.ok ? 200 : 409, { ...result, source: resolved });
        return;
      }
      if (req.method === "POST" && route === `${PLUGIN_PREFIX}/update`) {
        updateViaPackageManager(opts.installRoot).then((result) => {
          opts.log?.(result.ok ? `updated via package manager, applyMode=${result.applyMode}` : `package-manager update failed: ${result.error}`);
          send(result.ok ? 200 : 409, result);
        }).catch((e) => send(500, { error: String(e?.message ?? e) }));
        return;
      }
      if (req.method === "POST" && route === `${PLUGIN_PREFIX}/rollback`) {
        const result = rollback(opts.installRoot);
        send(result.ok ? 200 : 409, result);
        return;
      }
      send(404, { error: "not found" });
    }
  });
}

// src/bridge.ts
import { randomUUID, timingSafeEqual } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { mkdirSync as mkdirSync2, readFileSync as readFileSync2, writeFileSync as writeFileSync2 } from "node:fs";
import { basename as basename2, dirname as dirname2, join as join2 } from "node:path";
import { homedir, tmpdir } from "node:os";

// src/http.ts
var DEFAULT_BODY_LIMIT = 262144;
function readJsonBody(req, limit = DEFAULT_BODY_LIMIT) {
  return new Promise((resolve2, reject) => {
    let size = 0;
    const chunks = [];
    req.on("data", (c) => {
      size += c.length;
      if (size > limit) {
        reject(new Error("body too large"));
        req.destroy();
        return;
      }
      chunks.push(c);
    });
    req.on("end", () => {
      try {
        resolve2(JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}"));
      } catch {
        reject(new Error("invalid json"));
      }
    });
    req.on("error", reject);
  });
}
function sendJson(res, status, body) {
  res.writeHead(status, { "content-type": "application/json; charset=utf-8" });
  res.end(JSON.stringify(body));
}

// src/bridge.ts
var BRIDGE_PREFIX = "/copree-bridge";
var HEARTBEAT_MS = 2e4;
var TEXT_LIMIT = 2e4;
var ARGS_LIMIT = 2e3;
var SUMMARY_LIMIT = 400;
var ATTACHMENT_FENCE = "copree-attachments";
var MAX_IMAGE_BYTES = 8 * 1024 * 1024;
var MAX_IMAGES_PER_PROMPT = 8;
var PROMPT_BODY_LIMIT = 48 * 1024 * 1024;
var ASK_TIMEOUT_MS = 10 * 6e4;
var AskBroker = class {
  streams = /* @__PURE__ */ new Map();
  pending = /* @__PURE__ */ new Map();
  /** 某个会话开了流：此后它就有"人在看"，新请求才会往 Copree 送 */
  subscribe(sessionId, write) {
    let set = this.streams.get(sessionId);
    if (!set) {
      set = /* @__PURE__ */ new Set();
      this.streams.set(sessionId, set);
    }
    set.add(write);
    for (const { ask } of this.pending.values()) {
      if (ask.sessionId === sessionId) write({ k: "ask", ask });
    }
    return () => {
      const current = this.streams.get(sessionId);
      if (!current) return;
      current.delete(write);
      if (current.size === 0) this.streams.delete(sessionId);
    };
  }
  /** 把请求送出去并等回答；没人在看返回 null（调用方据此交回默认链） */
  ask(ask) {
    const audience = this.streams.get(ask.sessionId);
    if (!audience || audience.size === 0) return Promise.resolve(null);
    return new Promise((resolve2) => {
      const timer = setTimeout(() => {
        this.settle(ask.id, null);
      }, ASK_TIMEOUT_MS);
      timer.unref?.();
      this.pending.set(ask.id, { ask, resolve: resolve2, timer });
      for (const write of audience) write({ k: "ask", ask });
    });
  }
  /**
   * 当前哪些会话开着流（谁在看）——诊断用。
   * 提问帧只发给「正在看这条会话」的页面，没有观众就回落 DSH 默认链；
   * 排查「我在页面里没看到提问」时，第一眼要能看出观众到底有没有。
   */
  watchers() {
    return [...this.streams.entries()].map(([sessionId, set]) => ({ sessionId, subscribers: set.size }));
  }
  /** 页面回的决定：命中即唤醒等待方，并通知所有在看的页面把弹窗收掉 */
  answer(id, decision) {
    return this.settle(id, decision);
  }
  settle(id, decision) {
    const entry = this.pending.get(id);
    if (!entry) return false;
    this.pending.delete(id);
    clearTimeout(entry.timer);
    const audience = this.streams.get(entry.ask.sessionId);
    if (audience) for (const write of audience) write({ k: "askDone", id });
    entry.resolve(decision);
    return true;
  }
};
var askBroker = new AskBroker();
function questionsOf(raw) {
  if (!Array.isArray(raw)) return [];
  return raw.map((q) => ({
    id: String(q?.id ?? ""),
    question: String(q?.question ?? ""),
    ...q?.detail ? { detail: String(q.detail) } : {},
    ...q?.header ? { header: String(q.header) } : {},
    ...Array.isArray(q?.options) ? { options: q.options.map((o) => ({ label: String(o?.label ?? ""), ...o?.description ? { description: String(o.description) } : {} })) } : {},
    ...q?.multiSelect === true ? { multiSelect: true } : {}
  }));
}
function registerAnswerers(ctx) {
  const anyCtx = ctx;
  if (typeof anyCtx?.on !== "function") return () => {
  };
  const onApproval = async (req, next) => {
    const sessionId = String(req?.agent?.sessionId ?? "");
    if (!sessionId) return next();
    const decision = await askBroker.ask({
      id: randomUUID(),
      sessionId,
      kind: "approval",
      toolName: String(req?.toolName ?? ""),
      ...req?.reason ? { reason: String(req.reason) } : {},
      createdAt: Date.now()
    });
    if (!decision) return next();
    return decision.approve === true ? "allowed-once" : "rejected";
  };
  const onQuestions = async (request, next) => {
    const sessionId = String(request?.agent?.sessionId ?? "");
    if (!sessionId) return next();
    const decision = await askBroker.ask({
      id: randomUUID(),
      sessionId,
      kind: "question",
      questions: questionsOf(request?.questions),
      createdAt: Date.now()
    });
    if (!decision) return next();
    return { answers: decision.answers ?? [] };
  };
  anyCtx.on("approval/request", onApproval);
  anyCtx.on("user-questions/request", onQuestions);
  return () => {
    anyCtx.off?.("approval/request", onApproval);
    anyCtx.off?.("user-questions/request", onQuestions);
  };
}
function splitInlineImages(text) {
  const images = [];
  let fenced = false;
  const fence = new RegExp(`<${ATTACHMENT_FENCE}>([\\s\\S]*?)</${ATTACHMENT_FENCE}>`, "g");
  const cleaned = text.replace(fence, (_all, payload) => {
    fenced = true;
    try {
      const parsed = JSON.parse(payload);
      if (Array.isArray(parsed)) {
        for (const item of parsed) {
          const mime = String(item?.mime ?? "");
          const data = item?.data;
          if (mime.startsWith("image/") && typeof data === "string" && data.length > 0) {
            images.push({ name: String(item?.name ?? "image"), mime, data });
          }
        }
      }
    } catch {
    }
    return "";
  });
  if (!fenced) return { text, images: [] };
  return { text: cleaned.replace(/\n{3,}/g, "\n\n").trim(), images };
}
function attachmentDir(cwd) {
  return join2(cwd || tmpdir(), ".copree", "attachments");
}
async function saveInlineImages(images, cwd) {
  const saved = [];
  let skipped = Math.max(0, images.length - MAX_IMAGES_PER_PROMPT);
  const dir = attachmentDir(cwd);
  await mkdir(dir, { recursive: true });
  for (const img of images.slice(0, MAX_IMAGES_PER_PROMPT)) {
    const buf = Buffer.from(img.data, "base64");
    if (buf.length === 0 || buf.length > MAX_IMAGE_BYTES) {
      skipped += 1;
      continue;
    }
    const safe = basename2(img.name).replace(/[^\w.\-]+/g, "_").slice(-80) || "image";
    const file = join2(dir, `${Date.now()}-${saved.length}-${safe}`);
    await writeFile(file, buf);
    saved.push(file);
  }
  return { saved, skipped };
}
async function pullAttachments(refs, cwd, source) {
  const saved = [];
  let skipped = Math.max(0, refs.length - MAX_IMAGES_PER_PROMPT);
  const dir = attachmentDir(cwd);
  await mkdir(dir, { recursive: true });
  for (const ref of refs.slice(0, MAX_IMAGES_PER_PROMPT)) {
    const fileId = Number(ref?.fileId);
    if (!Number.isInteger(fileId) || fileId <= 0) {
      skipped += 1;
      continue;
    }
    try {
      const response = await fetch(`${source.backendUrl}/dsh-bridge/attachment/${fileId}`, {
        headers: { "x-copree-bridge-token": source.secret },
        signal: AbortSignal.timeout(3e4)
      });
      if (!response.ok) {
        skipped += 1;
        continue;
      }
      const buf = Buffer.from(await response.arrayBuffer());
      if (buf.length === 0 || buf.length > MAX_IMAGE_BYTES) {
        skipped += 1;
        continue;
      }
      const safe = basename2(String(ref.name ?? "image")).replace(/[^\w.\-]+/g, "_").slice(-80) || "image";
      const file = join2(dir, `${Date.now()}-${saved.length}-${safe}`);
      await writeFile(file, buf);
      saved.push(file);
    } catch {
      skipped += 1;
    }
  }
  return { saved, skipped };
}
function textOf(content) {
  if (!Array.isArray(content)) return "";
  return content.filter((block) => block?.type === "text" && typeof block.text === "string").map((block) => block.text).join("").slice(0, TEXT_LIMIT);
}
function toolResultOf(data) {
  const block = Array.isArray(data?.message?.content) ? data.message.content[0] : void 0;
  const callId = String(data?.message?.source?.callId ?? block?.toolCallId ?? "");
  const ok = data?.error === void 0 && block?.isError !== true;
  const text = textOf(block?.content).replace(/\s+/g, " ").trim();
  return { callId, ok, summary: text.slice(0, SUMMARY_LIMIT) };
}
function reasoningOf(content) {
  if (!Array.isArray(content)) return "";
  return content.filter((block) => block?.type === "reasoning" && typeof block.text === "string").map((block) => block.text).join("").slice(0, TEXT_LIMIT);
}
var argsByCallId = /* @__PURE__ */ new Map();
var parentsWithSubOps = /* @__PURE__ */ new Set();
var MAX_RESULT_LINES = 12;
function definitionFor(tools, name2, scope) {
  if (!name2) return void 0;
  try {
    const scoped = tools?.get?.(name2, scope);
    if (scoped) return scoped;
  } catch {
  }
  try {
    return tools?.get?.(name2);
  } catch {
    return void 0;
  }
}
function presentCallOf(tools, name2, args, scope) {
  let view;
  try {
    view = definitionFor(tools, name2, scope)?.presentCall?.(args);
  } catch {
    view = void 0;
  }
  const rawArgs = typeof args === "string" ? args : JSON.stringify(args ?? {});
  if (!view || typeof view !== "object") {
    return { card: "generic", title: name2, kind: "other", detail: String(rawArgs ?? "").slice(0, ARGS_LIMIT) };
  }
  const detail = view.card === "diff" ? Array.isArray(view.diffs) ? view.diffs.map((d) => String(d?.path ?? "")).filter(Boolean).join("\n") : "" : typeof view.rawInput === "string" ? view.rawInput : JSON.stringify(view.rawInput ?? args ?? {});
  return {
    card: String(view.card ?? "generic"),
    title: String(view.title ?? name2),
    kind: String(view.kind ?? "other"),
    detail: String(detail ?? "").slice(0, ARGS_LIMIT)
  };
}
function presentResultLines(tools, name2, args, result, fallback, scope) {
  let view;
  try {
    view = definitionFor(tools, name2, scope)?.presentResult?.(args, result);
  } catch {
    view = void 0;
  }
  const lines = [];
  const push = (value) => {
    if (typeof value !== "string") return;
    const flat = value.replace(/\s+/g, " ").trim();
    if (flat) lines.push(flat.slice(0, SUMMARY_LIMIT));
  };
  if (view && typeof view === "object") {
    if (view.title) push(view.title);
    if (view.card === "terminal") {
      push(view.output);
      if (view.exitCode !== void 0) push(`exit ${view.exitCode}`);
    } else if (view.card === "diff") {
      for (const diff of view.diffs ?? []) push(diff?.path);
    } else if (Array.isArray(view.matches)) {
      for (const group of view.matches) push(group?.path);
    } else {
      for (const block of view.content ?? []) if (block?.type === "text") push(block.text);
    }
  }
  if (lines.length === 0) push(fallback);
  return lines.slice(0, MAX_RESULT_LINES);
}
function frameForEvent(event, tools, scope) {
  const data = event?.data ?? {};
  switch (event?.type) {
    case "user/message":
      return data.source?.kind === "user" ? { k: "user", text: textOf(data.content) } : null;
    case "step/start":
      return { k: "step", turn: Number(data.turn ?? 0), step: Number(data.step ?? 0) };
    case "assistant/message": {
      const content = data.message?.content ?? data.content;
      const thinking = reasoningOf(content);
      const text = textOf(content);
      const frames = [];
      if (thinking) frames.push({ k: "think", text: thinking });
      if (text) frames.push({ k: "say", text, ...data.interrupted === true ? { interrupted: true } : {} });
      return frames.length > 0 ? frames : null;
    }
    case "tool/call": {
      const callId = String(data.callId ?? "");
      const name2 = String(data.name ?? "");
      let parsed = data.arguments;
      try {
        parsed = JSON.parse(String(data.arguments ?? "{}"));
      } catch {
      }
      argsByCallId.set(callId, { name: name2, args: parsed });
      if (argsByCallId.size > 200) argsByCallId.delete(String(argsByCallId.keys().next().value));
      return {
        k: "tool",
        callId,
        name: name2,
        args: String(data.arguments ?? "").slice(0, ARGS_LIMIT),
        ...presentCallOf(tools, name2, parsed, scope)
      };
    }
    case "tool/ptc-dispatch": {
      const parentCallId = String(data.parentCallId ?? "");
      if (!parentCallId) return null;
      parentsWithSubOps.add(parentCallId);
      if (parentsWithSubOps.size > 200) parentsWithSubOps.delete(String(parentsWithSubOps.values().next().value));
      const callId = String(data.subCallId ?? "");
      if (!callId) return null;
      const name2 = String(data.name ?? "");
      let parsed = data.arguments;
      try {
        parsed = JSON.parse(String(data.arguments ?? "{}"));
      } catch {
      }
      const lines = presentResultLines(
        tools,
        name2,
        parsed,
        { content: Array.isArray(data.content) ? data.content : [], isError: data.isError === true },
        name2,
        scope
      );
      return [
        { k: "tool", callId, name: name2, args: String(data.arguments ?? "").slice(0, ARGS_LIMIT), ...presentCallOf(tools, name2, parsed, scope) },
        { k: "toolDone", callId, ok: data.isError !== true, summary: lines[0] ?? name2, lines }
      ];
    }
    case "tool/result": {
      const { callId, ok, summary } = toolResultOf(data);
      const block = Array.isArray(data?.message?.content) ? data.message.content[0] : void 0;
      const known = argsByCallId.get(callId);
      const lines = parentsWithSubOps.has(callId) ? [] : presentResultLines(
        tools,
        known?.name ?? "",
        known?.args,
        {
          content: Array.isArray(block?.content) ? block.content : [],
          isError: block?.isError === true,
          ...data?.meta === void 0 ? {} : { meta: data.meta }
        },
        summary,
        scope
      );
      return { k: "toolDone", callId, ok, summary: lines[0] ?? summary, lines };
    }
    case "turn/end":
      return { k: "turnEnd", reason: String(data.reason?.kind ?? data.reason ?? "completed") };
    default:
      return null;
  }
}
function frameForStreamFrame(frame) {
  const chunk = frame?.chunk;
  if (frame?.type !== "chunk") return null;
  if (chunk?.type === "text-delta") {
    const text = typeof chunk.text === "string" ? chunk.text : "";
    return text ? { k: "delta", text, live: true } : null;
  }
  if (chunk?.type === "reasoning-delta") {
    const text = typeof chunk.text === "string" ? chunk.text : "";
    return text ? { k: "think", text, live: true } : null;
  }
  return null;
}
function framesOf(records, tools, scope) {
  if (!Array.isArray(records)) return [];
  const out = [];
  for (const record of records) {
    const frame = frameForEvent(record?.event, tools, scope);
    if (Array.isArray(frame)) out.push(...frame);
    else if (frame) out.push(frame);
  }
  return out;
}
var CONSENT_PATH = "/copree-consent";
function consentFile() {
  return join2(process.env.DSH_HOME || join2(homedir(), ".dsh"), "dsh-copree-consent.json");
}
function readConsent() {
  try {
    return JSON.parse(readFileSync2(consentFile(), "utf8"))?.copree === true;
  } catch {
    return false;
  }
}
function writeConsent(allowed) {
  const file = consentFile();
  mkdirSync2(dirname2(file), { recursive: true });
  writeFileSync2(file, JSON.stringify({ copree: allowed, at: Date.now() }, null, 2), { mode: 384 });
}
function handleConsent(req, res, apply2) {
  const route = new URL(req.url ?? "/", "http://dsh.local").pathname;
  if (route !== CONSENT_PATH && route !== `${CONSENT_PATH}/`) {
    sendJson(res, 404, { error: "not found" });
    return;
  }
  if (req.method === "GET") {
    sendJson(res, 200, { allowed: readConsent() });
    return;
  }
  if (req.method !== "POST") {
    sendJson(res, 405, { error: "method not allowed" });
    return;
  }
  readJsonBody(req).then((body) => {
    const allowed = body?.allowed === true;
    try {
      writeConsent(allowed);
    } catch (error) {
      sendJson(res, 500, { error: String(error?.message ?? error) });
      return;
    }
    apply2(allowed);
    sendJson(res, 200, { allowed });
  }).catch((error) => sendJson(res, 400, { error: String(error?.message ?? error) }));
}
async function sessionCwd(controller, sessionId, fallback) {
  try {
    const items = (await controller.list({})).items;
    const hit = items.find((item) => String(item?.sessionId) === sessionId);
    return String(hit?.cwd ?? "") || fallback;
  } catch {
    return fallback;
  }
}
function sessionSummary(item) {
  const projected = item?.projections?.values?.title;
  return {
    sessionId: String(item?.sessionId ?? ""),
    title: typeof projected === "string" ? projected : String(item?.title ?? ""),
    cwd: String(item?.cwd ?? ""),
    updatedAt: Number(item?.updatedAt ?? 0),
    running: item?.running === true
  };
}
function authorized(req, expected) {
  const got = req.headers["x-copree-bridge-token"];
  if (typeof got !== "string" || got.length !== expected.length) return false;
  return timingSafeEqual(Buffer.from(got), Buffer.from(expected));
}
function handle(req, res, controller, options, tools, scopeOf) {
  const route = new URL(req.url ?? "/", "http://dsh.local").pathname.slice(BRIDGE_PREFIX.length) || "/";
  if (!authorized(req, options.secret)) {
    sendJson(res, 401, { error: "unauthorized" });
    return;
  }
  const query = new URL(req.url ?? "/", "http://dsh.local").searchParams;
  const run = (fn) => {
    fn().then(
      ({ status = 200, body }) => sendJson(res, status, body),
      (error) => sendJson(res, 500, { error: String(error?.message ?? error) })
    );
  };
  if (req.method === "GET" && route === "/status") {
    sendJson(res, 200, { ok: true, plugin: "dsh-copree", version: options.version, streams: askBroker.watchers() });
    return;
  }
  if (req.method === "GET" && route === "/sessions") {
    run(async () => ({ body: { items: (await controller.list({})).items.map(sessionSummary) } }));
    return;
  }
  if (req.method === "POST" && route === "/prompt") {
    run(async () => {
      const body = await readJsonBody(req, PROMPT_BODY_LIMIT);
      const { text: cleaned, images } = splitInlineImages(String(body.text ?? ""));
      if (!cleaned.trim() && images.length === 0) return { status: 400, body: { error: "text is required" } };
      const sessionId = body.sessionId ? String(body.sessionId) : (await controller.create(body.cwd ? { cwd: String(body.cwd) } : {})).sessionId;
      const refs = Array.isArray(body.attachments) ? body.attachments : [];
      let text = cleaned;
      if (images.length > 0 || refs.length > 0) {
        const cwd = await sessionCwd(controller, sessionId, body.cwd ? String(body.cwd) : "");
        const inline = images.length > 0 ? await saveInlineImages(images, cwd) : { saved: [], skipped: 0 };
        const pulled = refs.length > 0 ? await pullAttachments(refs, cwd, options) : { saved: [], skipped: 0 };
        const saved = [...inline.saved, ...pulled.saved];
        const skipped = inline.skipped + pulled.skipped;
        if (saved.length > 0) {
          text = [text, `[\u56FE\u7247\u9644\u4EF6]
${saved.map((p) => `- ${p}`).join("\n")}`].filter(Boolean).join("\n\n");
        }
        if (skipped > 0) {
          text = [text, `[\u56FE\u7247\u9644\u4EF6] \u6709 ${skipped} \u5F20\u672A\u80FD\u4FDD\u5B58\uFF08\u8D85\u51FA\u5927\u5C0F\u6216\u6570\u91CF\u4E0A\u9650\uFF09`].filter(Boolean).join("\n\n");
        }
      }
      const mode = body.mode === "steer" ? "steer" : void 0;
      await controller.prompt(
        { sessionId, content: [{ type: "text", text }], requestId: randomUUID(), ...mode === void 0 ? {} : { mode } },
        new AbortController().signal
      );
      return { body: { sessionId } };
    });
    return;
  }
  if (req.method === "POST" && route === "/cancel") {
    run(async () => {
      const body = await readJsonBody(req);
      await controller.cancel({ sessionId: String(body.sessionId ?? "") });
      return { body: { ok: true } };
    });
    return;
  }
  if (req.method === "POST" && route === "/answer") {
    run(async () => {
      const body = await readJsonBody(req);
      const id = String(body.id ?? "");
      if (!id) return { status: 400, body: { error: "id is required" } };
      const decision = {};
      if (body.approve !== void 0) decision.approve = body.approve === true;
      if (Array.isArray(body.answers)) {
        decision.answers = body.answers.map((a) => ({
          id: String(a?.id ?? ""),
          selected: Array.isArray(a?.selected) ? a.selected.map((x) => String(x)) : [],
          ...a?.custom ? { custom: String(a.custom) } : {}
        }));
      }
      if (!askBroker.answer(id, decision)) return { status: 404, body: { error: "ask not found" } };
      return { body: { ok: true } };
    });
    return;
  }
  if (req.method === "GET" && route === "/stream") {
    const sessionId = query.get("sessionId") ?? "";
    if (!sessionId) {
      sendJson(res, 400, { error: "sessionId is required" });
      return;
    }
    void streamSession(req, res, controller, sessionId, Number(query.get("afterSeq") ?? -1), tools, scopeOf);
    return;
  }
  sendJson(res, 404, { error: "not found" });
}
async function streamSession(req, res, controller, sessionId, afterSeq, tools, scopeOf) {
  const scope = scopeOf(sessionId);
  res.writeHead(200, {
    "content-type": "text/event-stream; charset=utf-8",
    "cache-control": "no-cache, no-transform",
    connection: "keep-alive",
    "x-accel-buffering": "no"
  });
  const write = (frame) => {
    res.write(`data: ${JSON.stringify(frame)}

`);
  };
  const abort = new AbortController();
  const unsubscribeAsks = askBroker.subscribe(sessionId, write);
  req.on("close", () => abort.abort());
  const ping = setInterval(() => {
    res.write(": ping\n\n");
  }, HEARTBEAT_MS);
  ping.unref?.();
  try {
    const request = {
      address: { kind: "session", sessionId },
      assistantStream: true,
      ...Number.isSafeInteger(afterSeq) && afterSeq >= 0 ? { afterSeq } : {}
    };
    for await (const item of controller.follow(request, abort.signal)) {
      if (item?.type === "snapshot") {
        write({ k: "snapshot", records: framesOf(item.records, tools, scope) });
        continue;
      }
      if (item?.type === "assistant-stream") {
        const frame2 = frameForStreamFrame(item.frame);
        if (frame2) write(frame2);
        continue;
      }
      const frame = frameForEvent(item?.event, tools, scope);
      if (Array.isArray(frame)) frame.forEach(write);
      else if (frame) write(frame);
    }
  } catch (error) {
    if (!abort.signal.aborted) write({ k: "error", message: String(error?.message ?? error) });
  } finally {
    unsubscribeAsks();
    clearInterval(ping);
    res.end();
  }
}
function startHeartbeat(options) {
  if (!options.advertiseUrl) {
    options.log("dsh-copree: \u53CD\u5411\u6865\u63A5\u5DF2\u542F\u7528\u4F46\u672A\u6CE8\u518C\uFF08\u672A\u914D\u7F6E bridgeAdvertiseUrl\uFF09");
    return () => {
    };
  }
  let reported = null;
  const beat = async () => {
    let ok = false;
    try {
      const response = await fetch(`${options.backendUrl}/dsh-bridge/register`, {
        method: "POST",
        headers: { "content-type": "application/json", "x-copree-bridge-token": options.secret },
        body: JSON.stringify({
          plugin: "dsh-copree",
          version: options.version,
          advertiseUrl: options.advertiseUrl
        }),
        signal: AbortSignal.timeout(5e3)
      });
      ok = response.ok;
    } catch {
      ok = false;
    }
    if (ok !== reported) {
      reported = ok;
      options.log(ok ? `dsh-copree: \u53CD\u5411\u6865\u63A5\u5DF2\u6CE8\u518C -> ${options.advertiseUrl}` : "dsh-copree: \u53CD\u5411\u6865\u63A5\u6CE8\u518C\u5931\u8D25\uFF08Copree \u540E\u7AEF\u4E0D\u53EF\u8FBE\u6216\u5BC6\u94A5\u4E0D\u5339\u914D\uFF09");
    }
  };
  void beat();
  const timer = setInterval(() => {
    void beat();
  }, options.heartbeatMs ?? HEARTBEAT_MS);
  timer.unref?.();
  return () => clearInterval(timer);
}
var scopeOfFn;
async function initScopeSupport(log) {
  try {
    const mod = await import("@deepseek-ai/dsh-scope");
    if (typeof mod?.scopeOf === "function") {
      scopeOfFn = mod.scopeOf;
      return;
    }
    log("dsh-copree: dsh-scope \u6CA1\u6709\u5BFC\u51FA scopeOf\uFF08\u5DE5\u5177\u5361\u7247\u5C06\u9000\u5316\u4E3A\u901A\u7528\u5361\u7247\uFF09");
  } catch (error) {
    log(`dsh-copree: \u8F7D\u5165 dsh-scope \u5931\u8D25\uFF08\u5DE5\u5177\u5361\u7247\u5C06\u9000\u5316\u4E3A\u901A\u7528\u5361\u7247\uFF09\uFF1A${String(error?.message ?? error)}`);
  }
}
function registerBridge(ctx, options) {
  if (!options.enabled) {
    options.log("dsh-copree: \u53CD\u5411\u6865\u63A5\u672A\u542F\u7528\uFF08bridgeEnabled=false\uFF09");
    return () => {
    };
  }
  const controller = ctx.sessionController;
  let scopeState = null;
  const scopeOf = (sessionId) => {
    try {
      const agentCtx = ctx.agents?.get?.(sessionId)?.ctx;
      const scope = scopeOfFn ? scopeOfFn(agentCtx) : void 0;
      if (scope === void 0) {
        if (scopeState !== "missing") {
          scopeState = "missing";
          options.log("dsh-copree: \u6865\u63A5\u53D6\u4E0D\u5230\u4F1A\u8BDD\u7684 agent scope\uFF08\u5DE5\u5177\u5361\u7247\u5C06\u9000\u5316\u4E3A\u901A\u7528\u5361\u7247\uFF09");
        }
      } else if (scopeState !== "ok") {
        scopeState = "ok";
        options.log("dsh-copree: \u6865\u63A5\u5DF2\u62FF\u5230 agent scope\uFF08\u5DE5\u5177\u5361\u7247\u6309\u5DE5\u5177\u81EA\u5DF1\u7684\u58F0\u660E\u6E32\u67D3\uFF09");
      }
      return scope;
    } catch {
      return void 0;
    }
  };
  let disposeRoute = null;
  let stopHeartbeat = null;
  const apply2 = (allowed) => {
    if (allowed === (disposeRoute !== null)) return;
    if (allowed) {
      if (!options.secret) {
        options.log("dsh-copree: \u5DF2\u540C\u610F\u63A5\u5165\uFF0C\u4F46\u672A\u914D\u7F6E bridgeSecret\uFF0C\u6865\u63A5\u65E0\u6CD5\u542F\u52A8");
        return;
      }
      if (!controller) {
        options.log("dsh-copree: \u5DF2\u540C\u610F\u63A5\u5165\uFF0C\u4F46\u5F53\u524D profile \u6CA1\u6709 sessionController\uFF0C\u6865\u63A5\u65E0\u6CD5\u542F\u52A8");
        return;
      }
      void initScopeSupport(options.log);
      disposeRoute = ctx.webServer.register({
        kind: "prefix",
        path: BRIDGE_PREFIX,
        handler: (req, res) => handle(req, res, controller, options, ctx.tools, scopeOf)
      });
      stopHeartbeat = startHeartbeat(options);
      options.log("dsh-copree: \u5DF2\u540C\u610F Copree \u63A5\u5165\uFF0C\u6865\u63A5\u8DEF\u7531\u4E0E\u5FC3\u8DF3\u5DF2\u542F\u52A8");
    } else {
      disposeRoute?.();
      disposeRoute = null;
      stopHeartbeat?.();
      stopHeartbeat = null;
      options.log("dsh-copree: \u5DF2\u64A4\u9500\u540C\u610F\uFF0C\u6865\u63A5\u8DEF\u7531\u4E0E\u5FC3\u8DF3\u5DF2\u505C");
    }
  };
  const disposeAnswerers = registerAnswerers(ctx);
  const disposeConsent = ctx.webServer.register({
    kind: "prefix",
    path: CONSENT_PATH,
    handler: (req, res) => handleConsent(req, res, apply2)
  });
  apply2(readConsent());
  return () => {
    disposeConsent();
    disposeRoute?.();
    stopHeartbeat?.();
    disposeAnswerers();
  };
}

// src/index.ts
var name = "dsh-copree";
var inject = ["webServer", "tools", "systemPrompt", "sessionController", "agents"];
var PLUGIN_VERSION = (() => {
  try {
    return String(JSON.parse(readFileSync3(join3(PACKAGE_ROOT, "package.json"), "utf8")).version ?? "0.0.0");
  } catch {
    return "0.0.0";
  }
})();
var Config = z.object({
  backendUrl: z.string().default("http://127.0.0.1:5228"),
  pluginSourceDir: z.string().default(""),
  bridgeEnabled: z.boolean().default(true),
  bridgeSecret: z.string().default(""),
  bridgeAdvertiseUrl: z.string().default(""),
  bridgeHeartbeatMs: z.natural().default(HEARTBEAT_MS)
});
var HTTP_PREFIX = "/copree-api";
var WS_PATH = "/copree-ws";
var UI_PREFIX = "/copree-ui";
var UI_ROOT = join3(PACKAGE_ROOT, "dist");
var MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".gif": "image/gif",
  ".svg": "image/svg+xml",
  ".webp": "image/webp",
  ".ico": "image/x-icon",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
  ".ttf": "font/ttf",
  ".map": "application/json",
  ".txt": "text/plain; charset=utf-8",
  ".md": "text/markdown; charset=utf-8"
};
function serveStatic(req, res) {
  const raw = (req.url ?? "/").split("?")[0];
  const rel = raw === UI_PREFIX || raw === `${UI_PREFIX}/` ? "/index.html" : raw.slice(UI_PREFIX.length);
  const candidate = normalize(join3(UI_ROOT, rel));
  if (!candidate.startsWith(UI_ROOT)) {
    res.writeHead(403);
    res.end("forbidden");
    return;
  }
  let file = candidate;
  if (!existsSync2(file) || statSync(file).isDirectory()) {
    file = join3(UI_ROOT, "index.html");
  }
  if (!existsSync2(file)) {
    res.writeHead(404);
    res.end("not found");
    return;
  }
  res.writeHead(200, {
    "content-type": MIME[extname(file).toLowerCase()] ?? "application/octet-stream",
    "cache-control": extname(file) === ".html" ? "no-cache" : "public, max-age=31536000, immutable"
  });
  createReadStream(file).pipe(res);
}
var HOP_BY_HOP = /* @__PURE__ */ new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade"
]);
function stripHopByHop(headers) {
  const out = {};
  for (const [key, value] of Object.entries(headers)) {
    if (key === void 0 || value === void 0) continue;
    if (HOP_BY_HOP.has(key.toLowerCase())) continue;
    out[key] = value;
  }
  return out;
}
function proxyHttp(backendUrl, req, res, targetPath) {
  let target;
  try {
    target = new URL(targetPath, backendUrl.endsWith("/") ? backendUrl : `${backendUrl}/`);
  } catch (error) {
    res.writeHead(500, { "content-type": "application/json" });
    res.end(JSON.stringify({ error: "invalid proxy target" }));
    return;
  }
  const upstream = http.request(
    {
      protocol: target.protocol,
      hostname: target.hostname,
      port: target.port || (target.protocol === "https:" ? 443 : 80),
      path: `${target.pathname}${target.search}`,
      method: req.method ?? "GET",
      headers: {
        ...stripHopByHop(req.headers),
        host: target.host,
        // 告知后端当前部署形态：世界代码注入 window.WORLD_API / WORLD_UI 用
        // （群聊面板、平台菜单等组件据此拼同源代理前缀，避免落到宿主 SPA fallback）。
        "x-copree-api-prefix": "/copree-api",
        "x-copree-ui-prefix": "/copree-ui"
      }
    },
    (upRes) => {
      const headers = stripHopByHop(upRes.headers);
      const status = upRes.statusCode ?? 502;
      if (status >= 300 && status < 400 && headers.location !== void 0) {
        const loc = Array.isArray(headers.location) ? String(headers.location[0]) : String(headers.location);
        if (loc.startsWith("/") && !loc.startsWith("/copree-api")) {
          headers.location = `/copree-api${loc}`;
        }
      }
      res.writeHead(status, upRes.statusMessage ?? "", headers);
      upRes.pipe(res);
    }
  );
  upstream.on("error", () => {
    if (!res.headersSent) {
      res.writeHead(502, { "content-type": "application/json" });
      res.end(JSON.stringify({ error: "backend unreachable" }));
    } else {
      res.destroy();
    }
  });
  req.pipe(upstream);
}
function proxyWs(backendUrl, req, socket, head) {
  let target;
  try {
    target = new URL("/ws", backendUrl.endsWith("/") ? backendUrl : `${backendUrl}/`);
  } catch {
    socket.destroy();
    return;
  }
  const search = req.url?.includes("?") ? req.url.slice(req.url.indexOf("?")) : "";
  const upstream = http.request({
    protocol: target.protocol,
    hostname: target.hostname,
    port: target.port || (target.protocol === "https:" ? 443 : 80),
    path: `/ws${search}`,
    method: "GET",
    headers: {
      ...stripHopByHop(req.headers),
      host: target.host,
      connection: "Upgrade",
      upgrade: "websocket"
    }
  });
  upstream.on("upgrade", (upRes, upSocket, upHead) => {
    const statusLine = `HTTP/1.1 ${upRes.statusCode ?? 101} ${upRes.statusMessage ?? "Switching Protocols"}\r
`;
    let headerText = statusLine;
    for (const [key, value] of Object.entries(upRes.headers)) {
      if (value === void 0) continue;
      headerText += `${key}: ${Array.isArray(value) ? value.join(", ") : value}\r
`;
    }
    headerText += "\r\n";
    socket.write(headerText);
    if (upHead.length > 0) socket.write(upHead);
    upSocket.pipe(socket);
    socket.pipe(upSocket);
    const close = () => {
      upSocket.destroy();
      socket.destroy();
    };
    upSocket.on("error", close);
    socket.on("error", close);
    upSocket.on("close", close);
    socket.on("close", close);
  });
  upstream.on("error", () => {
    socket.destroy();
  });
  upstream.on("response", () => {
    socket.destroy();
  });
  if (head.length > 0) upstream.write(head);
  upstream.end();
}
var WORLD_DIR_BASE = join3(process.env.DSH_HOME ?? join3(os.homedir(), ".dsh"), "copree-worlds");
var WORLDS_PREFIX = "/copree-worlds";
var LEGACY_WORLD_DIR_BASE = join3(process.env.DSH_HOME ?? join3(os.homedir(), ".dsh"), "aischat-worlds");
var WORLD_DIR_BASES = [WORLD_DIR_BASE, LEGACY_WORLD_DIR_BASE];
var META_FILES = [".copree-world.json", ".aischat-world.json"];
function readWorldMeta(dir) {
  for (const file of META_FILES) {
    const p = join3(dir, file);
    try {
      if (existsSync2(p)) return JSON.parse(readFileSync3(p, "utf8"));
    } catch {
    }
  }
  return null;
}
var worldTokenMap = /* @__PURE__ */ new Map();
var sessionTokenMap = /* @__PURE__ */ new Map();
function sanitizeDirName(name2) {
  return String(name2 || "").replace(/[\\/:*?"<>|\u0000-\u001f]/g, "_").trim() || "\u672A\u547D\u540D\u4E16\u754C";
}
function backendRequest(backendUrl, method, path, opts = {}) {
  return new Promise((resolve2, reject) => {
    let target;
    try {
      target = new URL(path, backendUrl.endsWith("/") ? backendUrl : `${backendUrl}/`);
    } catch {
      reject(new Error("invalid target"));
      return;
    }
    const data = opts.json === void 0 ? null : JSON.stringify(opts.json);
    const req = http.request({
      protocol: target.protocol,
      hostname: target.hostname,
      port: target.port || (target.protocol === "https:" ? 443 : 80),
      path: `${target.pathname}${target.search}`,
      method,
      headers: {
        "content-type": "application/json",
        ...opts.token ? { authorization: `Bearer ${opts.token}` } : {},
        ...opts.headers ?? {},
        ...data ? { "content-length": String(Buffer.byteLength(data)) } : {}
      }
    }, (res) => {
      const chunks = [];
      res.on("data", (c) => {
        chunks.push(Buffer.isBuffer(c) ? c : Buffer.from(c));
      });
      res.on("end", () => resolve2({ status: res.statusCode ?? 502, text: Buffer.concat(chunks).toString("utf8") }));
    });
    req.on("error", reject);
    if (data) req.write(data);
    req.end();
  });
}
async function resolveWorldApiToken(backendUrl, worldId, ownerToken) {
  if (!ownerToken) return void 0;
  const res = await backendRequest(backendUrl, "GET", `/worlds/${worldId}`, { token: ownerToken });
  if (res.status !== 200) return void 0;
  try {
    const data = JSON.parse(res.text);
    const token = data.config?.api_token;
    return typeof token === "string" && token.length > 0 ? token : void 0;
  } catch {
    return void 0;
  }
}
function resolveWorldFromCwd(cwd) {
  if (!cwd) return null;
  try {
    const real = realpathSync(cwd);
    const bases = WORLD_DIR_BASES.filter((b) => existsSync2(b)).map((b) => realpathSync(b));
    if (!bases.some((base) => real === base || real.startsWith(base + sep))) return null;
    const meta = readWorldMeta(real);
    if (!meta) return null;
    const worldId = Number(meta.worldId);
    if (!Number.isInteger(worldId) || worldId <= 0) return null;
    return { worldId, name: String(meta.name ?? `\u4E16\u754C${worldId}`) };
  } catch {
    return null;
  }
}
function textOutput(value) {
  const text = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  return [{ type: "text", text }];
}
function listWorldDirs() {
  const out = [];
  const seen = /* @__PURE__ */ new Set();
  for (const root of WORLD_DIR_BASES) {
    try {
      if (!existsSync2(root)) continue;
      for (const entry of readdirSync(root, { withFileTypes: true })) {
        if (!entry.isDirectory() || seen.has(entry.name)) continue;
        seen.add(entry.name);
        const meta = readWorldMeta(join3(root, entry.name)) ?? {};
        out.push({
          dir: entry.name,
          worldId: Number(meta.worldId) || null,
          name: String(meta.name ?? "")
        });
      }
    } catch {
    }
  }
  return out;
}
function isMirrorExcluded(relPath) {
  const base = relPath.split("/").pop() ?? relPath;
  if (META_FILES.includes(relPath)) return true;
  if (relPath === SNAPSHOT_FILE || relPath === LEGACY_SNAPSHOT_FILE) return true;
  if (base === "__pycache__" || relPath.includes("/__pycache__/")) return true;
  if (base.endsWith(".pyc")) return true;
  if (base === ".DS_Store") return true;
  return false;
}
function worldDirFor(worldId) {
  for (const root of WORLD_DIR_BASES) {
    try {
      if (!existsSync2(root)) continue;
      for (const entry of readdirSync(root, { withFileTypes: true })) {
        if (!entry.isDirectory()) continue;
        const meta = readWorldMeta(join3(root, entry.name));
        if (meta && Number(meta.worldId) === worldId) return join3(root, entry.name);
      }
    } catch {
    }
  }
  return null;
}
var SNAPSHOT_FILE = ".copree-sync.json";
var LEGACY_SNAPSHOT_FILE = ".aischat-sync.json";
function readSnapshot(dir) {
  for (const file of [SNAPSHOT_FILE, LEGACY_SNAPSHOT_FILE]) {
    try {
      const parsed = JSON.parse(readFileSync3(join3(dir, file), "utf8"));
      if (parsed && parsed.v === 1 && parsed.files && typeof parsed.files === "object") return parsed;
    } catch {
    }
  }
  return { v: 1, files: {} };
}
function writeSnapshot(dir, snap) {
  try {
    writeFileSync3(join3(dir, SNAPSHOT_FILE), JSON.stringify(snap, null, 2), "utf8");
  } catch {
  }
}
function statMtime(p) {
  try {
    return statSync(p).mtimeMs;
  } catch {
    return 0;
  }
}
function compareMirror(remoteTree, dir, snap) {
  const remote = /* @__PURE__ */ new Map();
  for (const f of remoteTree) if (f.path && !isMirrorExcluded(f.path)) remote.set(f.path, f.mtime);
  const localFiles = walkDir(dir).filter((p) => !isMirrorExcluded(p));
  const local = /* @__PURE__ */ new Map();
  for (const p of localFiles) local.set(p, statMtime(join3(dir, p)));
  const out = { added: [], removed: [], changedRemote: [], changedLocal: [], conflict: [] };
  const seen = /* @__PURE__ */ new Set();
  for (const [p, rm] of remote) {
    seen.add(p);
    const rec = snap.files[p];
    const localMtime = local.get(p) ?? 0;
    if (rec === void 0) {
      if (localMtime === 0) out.added.push(p);
      else out.changedLocal.push(p);
      continue;
    }
    const remoteChanged = rm !== rec.rm;
    const localChanged = localMtime !== rec.lm;
    if (remoteChanged && localChanged) out.conflict.push(p);
    else if (remoteChanged) out.changedRemote.push(p);
    else if (localChanged) out.changedLocal.push(p);
  }
  for (const p of local.keys()) {
    if (seen.has(p)) continue;
    if (snap.files[p] === void 0) out.changedLocal.push(p);
    else out.removed.push(p);
  }
  return out;
}
async function fetchRemoteTree(backendUrl, worldId, token) {
  if (!token) return null;
  const tree = await backendRequest(backendUrl, "GET", `/worlds/${worldId}/files?prefix=`, { token });
  if (tree.status !== 200) return null;
  try {
    const files = JSON.parse(tree.text || "{}").files ?? [];
    return files.map((f) => ({ path: String(f.path ?? ""), mtime: Number(f.mtime) || 0 }));
  } catch {
    return null;
  }
}
async function pullWithSnapshot(backendUrl, worldId, dir, token, force = false) {
  const snap = readSnapshot(dir);
  const tree = await fetchRemoteTree(backendUrl, worldId, token);
  if (!tree) return { ok: false, message: "\u65E0\u6CD5\u83B7\u53D6\u4E16\u754C\u6587\u4EF6\u6811\uFF08\u9700\u767B\u5F55\u6001\uFF09" };
  const cmp = compareMirror(tree, dir, snap);
  if (!force && (cmp.changedLocal.length > 0 || cmp.conflict.length > 0)) {
    return {
      ok: false,
      message: "\u672C\u5730\u6709\u672A\u63A8\u9001\u7684\u4FEE\u6539\u6216\u51B2\u7A81\uFF0C\u5DF2\u53D6\u6D88\u62C9\u53D6\uFF08\u4E0D\u4F1A\u8986\u76D6\u4F60\u7684\u6539\u52A8\uFF09",
      conflict: cmp.conflict
    };
  }
  const pullTargets = [...cmp.added, ...cmp.changedRemote];
  if (force) pullTargets.push(...cmp.conflict, ...cmp.changedLocal);
  let pulled = 0;
  let skipped = 0;
  const pulledOk = [];
  for (const rel of pullTargets) {
    try {
      let content = null;
      if (/\.html?$/i.test(rel)) {
        if (!token) {
          skipped++;
          continue;
        }
        const res = await backendRequest(backendUrl, "GET", `/worlds/${worldId}/files/content?path=${encodeURIComponent(rel)}`, { token });
        if (res.status !== 200) {
          skipped++;
          continue;
        }
        try {
          const data = JSON.parse(res.text);
          if (data.binary) {
            skipped++;
            continue;
          }
          content = typeof data.content === "string" ? data.content : null;
        } catch {
          skipped++;
          continue;
        }
      } else {
        const res = await backendRequest(backendUrl, "GET", `/world/${worldId}/files/${encodeURIComponent(rel)}`);
        if (res.status !== 200) {
          skipped++;
          continue;
        }
        content = res.text;
      }
      if (content === null) {
        skipped++;
        continue;
      }
      const target = join3(dir, rel);
      mkdirSync3(join3(target, ".."), { recursive: true });
      writeFileSync3(target, content, "utf8");
      pulled++;
      pulledOk.push(rel);
    } catch {
      skipped++;
    }
  }
  const removedOk = [];
  for (const rel of cmp.removed) {
    if (force || !cmp.changedLocal.includes(rel)) {
      try {
        unlinkSync(join3(dir, rel));
        pulled++;
        removedOk.push(rel);
      } catch {
        skipped++;
      }
    }
  }
  const nextSnap = { v: 1, files: { ...snap.files } };
  for (const rel of pulledOk) {
    const rm = tree.find((t) => t.path === rel)?.mtime ?? snap.files[rel]?.rm ?? 0;
    nextSnap.files[rel] = { lm: statMtime(join3(dir, rel)), rm };
  }
  for (const rel of removedOk) delete nextSnap.files[rel];
  writeSnapshot(dir, nextSnap);
  const parts = [];
  if (cmp.added.length) parts.push(`+${cmp.added.length} \u65B0\u589E`);
  if (cmp.changedRemote.length) parts.push(`~${cmp.changedRemote.length} \u4FEE\u6539`);
  if (cmp.removed.length) parts.push(`-${cmp.removed.length} \u5220\u9664`);
  if (pulled > 0 && !parts.length) parts.push(`\u2193${pulled} \u8986\u76D6`);
  return {
    ok: true,
    pulled,
    skipped,
    conflict: cmp.conflict,
    message: `\u5DF2\u62C9\u53D6\u4E16\u754C\u6700\u65B0\u6587\u4EF6${parts.length ? `\uFF1A${parts.join(" ")}` : "\uFF08\u65E0\u53D8\u5316\uFF09"}`
  };
}
async function pushWithSnapshot(backendUrl, worldId, dir, token, force = false) {
  if (!token) return { ok: false, message: "\u8BE5\u4E16\u754C\u4F1A\u8BDD\u672A\u8FDE\u63A5\u767B\u5F55\u6001\uFF0C\u65E0\u6CD5\u63A8\u9001\uFF08\u9700 owner \u6743\u9650\uFF09" };
  const snap = readSnapshot(dir);
  const tree = await fetchRemoteTree(backendUrl, worldId, token);
  if (!tree) return { ok: false, message: "\u65E0\u6CD5\u83B7\u53D6\u4E16\u754C\u6587\u4EF6\u6811\uFF08\u9700\u767B\u5F55\u6001\uFF09" };
  const cmp = compareMirror(tree, dir, snap);
  const pushTargets = /* @__PURE__ */ new Set([...cmp.changedLocal, ...cmp.added]);
  if (force) for (const c of cmp.conflict) pushTargets.add(c);
  let pushed = 0;
  let skipped = 0;
  const errors = [];
  const pushedOk = [];
  const localFiles = walkDir(dir);
  for (const rel of localFiles) {
    if (!pushTargets.has(rel) || isMirrorExcluded(rel)) continue;
    if (cmp.conflict.includes(rel) && !force) {
      errors.push(`${rel}\uFF08\u51B2\u7A81\uFF0C\u8FDC\u7AEF\u4E5F\u6539\u8FC7\u2014\u2014\u8BF7\u5148\u88C1\u51B3\u6216\u7528 force \u8986\u76D6\uFF09`);
      continue;
    }
    try {
      const content = readFileSync3(join3(dir, rel), "utf8");
      const res = await backendRequest(backendUrl, "PUT", `/worlds/${worldId}/files`, { token, json: { path: rel, content } });
      if (res.status === 200) {
        pushed++;
        pushedOk.push(rel);
      } else errors.push(`${rel} (${res.status})`);
    } catch {
      errors.push(`${rel}\uFF08\u8BFB\u5199\u5931\u8D25\uFF09`);
    }
  }
  const removedOk = [];
  for (const p of cmp.removed) {
    if (!force && cmp.conflict.includes(p)) continue;
    const res = await backendRequest(backendUrl, "DELETE", `/worlds/${worldId}/files?path=${encodeURIComponent(p)}`, { token });
    if (res.status === 200) {
      pushed++;
      removedOk.push(p);
    } else errors.push(`${p} (\u5220\u9664 ${res.status})`);
  }
  let freshTree = tree;
  if (pushed > 0) freshTree = await fetchRemoteTree(backendUrl, worldId, token) ?? tree;
  const nextSnap = { v: 1, files: { ...snap.files } };
  for (const rel of pushedOk) {
    const rm = freshTree.find((t) => t.path === rel)?.mtime ?? snap.files[rel]?.rm ?? 0;
    nextSnap.files[rel] = { lm: statMtime(join3(dir, rel)), rm };
  }
  for (const rel of removedOk) delete nextSnap.files[rel];
  writeSnapshot(dir, nextSnap);
  return {
    ok: true,
    pushed,
    skipped,
    conflict: cmp.conflict,
    message: `\u5DF2\u540C\u6B65\u5230\u4E16\u754C${pushed ? `\uFF08${pushed} \u4E2A\u6587\u4EF6\uFF09` : "\uFF08\u65E0\u53D8\u5316\uFF09"}`
  };
}
function walkDir(root) {
  const out = [];
  const visit = (dir) => {
    let entries;
    try {
      entries = readdirSync(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const e of entries) {
      const full = join3(dir, e.name);
      const rel = full.slice(root.length).replace(/^[/\\]/, "");
      if (e.isDirectory()) {
        if (e.name === "__pycache__") continue;
        visit(full);
      } else if (e.isFile() || e.isSymbolicLink()) {
        out.push(rel);
      }
    }
  };
  visit(root);
  return out;
}
function apply(ctx, config) {
  const backendUrl = config.backendUrl.replace(/\/+$/, "");
  ctx.webServer.register({
    kind: "prefix",
    path: HTTP_PREFIX,
    handler: (req, res) => {
      const targetPath = req.url ? req.url.slice(HTTP_PREFIX.length) : "/";
      proxyHttp(backendUrl, req, res, targetPath.startsWith("/") ? targetPath : `/${targetPath}`);
    }
  });
  ctx.webServer.register({
    kind: "prefix",
    path: UI_PREFIX,
    handler: serveStatic
  });
  ctx.webServer.registerUpgrade({
    path: WS_PATH,
    handler: (req, socket, head) => {
      proxyWs(backendUrl, req, socket, head);
    }
  });
  registerPluginRoutes((route) => ctx.webServer.register(route), {
    installRoot: PACKAGE_ROOT,
    backendUrl,
    sourceDir: config.pluginSourceDir,
    log: (message) => ctx.logger?.info?.(message)
  });
  registerBridge(ctx, {
    enabled: config.bridgeEnabled,
    secret: config.bridgeSecret,
    advertiseUrl: config.bridgeAdvertiseUrl.replace(/\/+$/, ""),
    backendUrl,
    version: PLUGIN_VERSION,
    heartbeatMs: config.bridgeHeartbeatMs,
    log: (message) => ctx.logger?.info?.(message)
  });
  ctx.webServer.register({
    kind: "prefix",
    path: WORLDS_PREFIX,
    handler: (req, res) => {
      const route = (req.url ?? "/").split("?")[0];
      const send = (status, json) => sendJson(res, status, json);
      if (req.method === "POST" && route === `${WORLDS_PREFIX}/dir`) {
        readJsonBody(req).then((body) => {
          const worldId = Number(body.worldId);
          const name2 = String(body.name ?? "");
          if (!Number.isInteger(worldId) || worldId <= 0) {
            send(400, { error: "invalid worldId" });
            return;
          }
          const existing = worldDirFor(worldId);
          if (existing) {
            send(200, { path: existing });
            return;
          }
          const dirName = sanitizeDirName(`Copree\u7FA4\u89C6\u754C-${name2 || `\u4E16\u754C${worldId}`}`);
          const dir = join3(WORLD_DIR_BASE, dirName);
          try {
            mkdirSync3(dir, { recursive: true });
            const metaPath = join3(dir, ".copree-world.json");
            if (!existsSync2(metaPath)) {
              writeFileSync3(metaPath, JSON.stringify({ worldId, name: name2 }, null, 2), "utf8");
            } else {
              const prev = JSON.parse(readFileSync3(metaPath, "utf8"));
              if (Number(prev.worldId) !== worldId) {
                send(409, { error: `\u76EE\u5F55\u5DF2\u5C5E\u4E8E\u4E16\u754C ${prev.worldId}` });
                return;
              }
            }
            send(200, { path: dir });
          } catch (e) {
            send(500, { error: String(e.message ?? e) });
          }
        }).catch(() => send(400, { error: "bad request" }));
        return;
      }
      if (req.method === "POST" && route === `${WORLDS_PREFIX}/token`) {
        readJsonBody(req).then((body) => {
          const worldId = Number(body.worldId);
          const token = String(body.token ?? "");
          if (!Number.isInteger(worldId) || worldId <= 0) {
            send(400, { error: "missing worldId" });
            return;
          }
          if (!token) {
            send(400, { error: "missing token" });
            return;
          }
          worldTokenMap.set(worldId, token);
          ctx.logger?.info?.(`dsh-copree: token registered for world ${worldId}`);
          send(200, { ok: true });
        }).catch(() => send(400, { error: "bad request" }));
        return;
      }
      if (req.method === "GET" && route === `${WORLDS_PREFIX}/status`) {
        send(200, {
          tokenWorlds: [...worldTokenMap.keys()],
          worldDirs: listWorldDirs()
        });
        return;
      }
      if (req.method === "POST" && route === `${WORLDS_PREFIX}/pull`) {
        readJsonBody(req).then(async (body) => {
          const worldId = Number(body.worldId);
          if (!Number.isInteger(worldId) || worldId <= 0) {
            send(400, { error: "invalid worldId" });
            return;
          }
          const dir = worldDirFor(worldId);
          if (!dir) {
            send(404, { error: `\u5DE5\u4F5C\u533A\u6CA1\u6709\u4E16\u754C ${worldId} \u7684\u76EE\u5F55\uFF08\u8BF7\u5148\u540C\u6B65\uFF09` });
            return;
          }
          const result = await pullWithSnapshot(backendUrl, worldId, dir, worldTokenMap.get(worldId), body.force === true);
          send(result.ok ? 200 : 409, result);
        }).catch(() => send(400, { error: "bad request" }));
        return;
      }
      send(404, { error: "not found" });
    }
  });
  const worldFromExec = (exec) => {
    const session = exec.agent?.session;
    const world = resolveWorldFromCwd(session?.header?.cwd);
    if (!world) return null;
    const token = worldTokenMap.get(world.worldId) ?? (session?.id ? sessionTokenMap.get(String(session.id)) : void 0);
    return { ...world, token };
  };
  const registerWorldTool = (toolName, description, parameters, execute) => {
    ctx.tools.register({
      name: toolName,
      description,
      parameters,
      output: { schema: { type: "object" }, render: (_args, value) => textOutput(value) },
      execute: async (rawArgs, exec) => {
        const world = worldFromExec(exec);
        if (!world) {
          return { error: "\u5F53\u524D\u4F1A\u8BDD\u4E0D\u5C5E\u4E8E\u4EFB\u4F55 Copree \u4E16\u754C\uFF1A\u8BF7\u5148\u5728\u5DE5\u4F5C\u533A\u6253\u5F00\u4E00\u4E2A\u300CCopree\u7FA4\u89C6\u754C-\u4E16\u754C\u540D\u300D\u4F1A\u8BDD\uFF08\u8BE5\u4F1A\u8BDD\u76EE\u5F55\u9700\u542B .copree-world.json\uFF09\u3002" };
        }
        try {
          const result = await execute(rawArgs ?? {}, world);
          if (result && typeof result === "object" && !toolName.startsWith("world_pull") && !toolName.startsWith("world_push")) {
            const dir = worldDirFor(world.worldId);
            if (dir) {
              try {
                const snap = readSnapshot(dir);
                const tree = await fetchRemoteTree(backendUrl, world.worldId, world.token);
                if (tree) {
                  const cmp = compareMirror(tree, dir, snap);
                  const updateCount = cmp.added.length + cmp.changedRemote.length;
                  if (updateCount > 0) {
                    result.updateHint = `\u4E16\u754C\u6709 ${updateCount} \u4E2A\u6587\u4EF6\u66F4\u65B0\u672A\u62C9\u53D6\uFF08\u7528 world_pull \u83B7\u53D6\u6700\u65B0\uFF1B\u82E5\u4F60\u521A\u6539\u8FC7\u6587\u4EF6\uFF0C\u5148 world_push\uFF09`;
                  }
                  if (cmp.conflict.length > 0) {
                    result.conflictHint = `\u4EE5\u4E0B\u6587\u4EF6\u5B58\u5728\u540C\u6B65\u51B2\u7A81\uFF1A${cmp.conflict.slice(0, 5).join(", ")}\uFF08\u7528 world_pull / world_push \u7684 force \u88C1\u51B3\uFF09`;
                  }
                }
              } catch {
              }
            }
          }
          return result;
        } catch (e) {
          return { error: String(e.message ?? e) };
        }
      }
    });
  };
  registerWorldTool(
    "world_list_files",
    "\u5217\u51FA\u5F53\u524D Copree \u7FA4\u89C6\u754C\u4E16\u754C\u7684\u6587\u4EF6\u6811\uFF08\u4E16\u754C\u9875\u9762\u4EE3\u7801\u7B49\uFF09\u3002\u8FD4\u56DE\u6587\u4EF6\u5217\u8868\uFF08\u76F8\u5BF9\u8DEF\u5F84\u3001\u5927\u5C0F\u3001\u7C7B\u578B\uFF09\u3002",
    { type: "object", properties: { prefix: { type: "string", description: "\u53EF\u9009\u524D\u7F00\u8FC7\u6EE4\uFF0C\u5982 css/ \u6216 blocks/" } }, additionalProperties: false },
    async (args, world) => {
      if (!world.token) return { error: "\u8BE5\u4E16\u754C\u4F1A\u8BDD\u672A\u8FDE\u63A5\u767B\u5F55\u6001\uFF0C\u65E0\u6CD5\u5217\u6587\u4EF6\uFF08\u9700 owner \u6743\u9650\uFF09\u3002\u8BF7\u91CD\u65B0\u6253\u5F00 Copree \u540C\u6B65\u4E00\u6B21\u3002" };
      const prefix = encodeURIComponent(String(args.prefix ?? ""));
      const res = await backendRequest(backendUrl, "GET", `/worlds/${world.worldId}/files?prefix=${prefix}`, { token: world.token });
      if (res.status !== 200) return { error: `\u5217\u6587\u4EF6\u5931\u8D25 (${res.status})`, detail: res.text.slice(0, 400) };
      try {
        return JSON.parse(res.text || "{}");
      } catch {
        return { ok: true, content: res.text.slice(0, 4e3) };
      }
    }
  );
  registerWorldTool(
    "world_read_file",
    "\u8BFB\u53D6\u5F53\u524D Copree \u7FA4\u89C6\u754C\u4E16\u754C\u7684\u4E00\u4E2A\u6587\u4EF6\u5185\u5BB9\uFF08\u5982 index.html\u3001script.js\u3001style.css\uFF09\u3002",
    { type: "object", properties: { path: { type: "string", description: "\u76F8\u5BF9\u8DEF\u5F84\uFF0C\u5982 index.html \u6216 blocks/group-chat/chat-panel.js" } }, required: ["path"], additionalProperties: false },
    async (args, world) => {
      const path = String(args.path ?? "");
      if (!path) return { error: "\u7F3A\u5C11 path" };
      const res = await backendRequest(backendUrl, "GET", `/world/${world.worldId}/files/${encodeURIComponent(path)}`);
      if (res.status !== 200) return { error: `\u8BFB\u6587\u4EF6\u5931\u8D25 (${res.status})` };
      return { ok: true, path, content: res.text.slice(0, 6e4) };
    }
  );
  registerWorldTool(
    "world_write_file",
    "\u5199\u5165\u5F53\u524D Copree \u7FA4\u89C6\u754C\u4E16\u754C\u7684\u4E00\u4E2A\u6587\u4EF6\uFF08\u8986\u76D6\uFF1B\u81EA\u52A8\u5EFA\u76EE\u5F55\uFF09\u3002\u7528\u4E8E\u4FEE\u6539\u4E16\u754C\u9875\u9762\u4EE3\u7801\u3002",
    { type: "object", properties: { path: { type: "string", description: "\u76F8\u5BF9\u8DEF\u5F84\uFF0C\u5982 index.html" }, content: { type: "string", description: "\u5B8C\u6574\u6587\u4EF6\u5185\u5BB9" } }, required: ["path", "content"], additionalProperties: false },
    async (args, world) => {
      if (!world.token) return { error: "\u8BE5\u4E16\u754C\u4F1A\u8BDD\u672A\u8FDE\u63A5\u767B\u5F55\u6001\uFF0C\u65E0\u6CD5\u5199\u6587\u4EF6\uFF08\u9700 owner \u6743\u9650\uFF09\u3002" };
      const res = await backendRequest(backendUrl, "PUT", `/worlds/${world.worldId}/files`, {
        token: world.token,
        json: { path: String(args.path ?? ""), content: String(args.content ?? "") }
      });
      if (res.status !== 200) return { error: `\u5199\u6587\u4EF6\u5931\u8D25 (${res.status})`, detail: res.text.slice(0, 400) };
      try {
        return { ok: true, ...JSON.parse(res.text || "{}") };
      } catch {
        return { ok: true };
      }
    }
  );
  registerWorldTool(
    "world_delete_file",
    "\u5220\u9664\u5F53\u524D Copree \u7FA4\u89C6\u754C\u4E16\u754C\u7684\u4E00\u4E2A\u6587\u4EF6\u3002",
    { type: "object", properties: { path: { type: "string", description: "\u76F8\u5BF9\u8DEF\u5F84" } }, required: ["path"], additionalProperties: false },
    async (args, world) => {
      if (!world.token) return { error: "\u8BE5\u4E16\u754C\u4F1A\u8BDD\u672A\u8FDE\u63A5\u767B\u5F55\u6001\uFF0C\u65E0\u6CD5\u5220\u6587\u4EF6\u3002" };
      const path = encodeURIComponent(String(args.path ?? ""));
      const res = await backendRequest(backendUrl, "DELETE", `/worlds/${world.worldId}/files?path=${path}`, { token: world.token });
      if (res.status !== 200) return { error: `\u5220\u6587\u4EF6\u5931\u8D25 (${res.status})`, detail: res.text.slice(0, 400) };
      return { ok: true };
    }
  );
  registerWorldTool(
    "world_api",
    "\u8C03\u7528\u5F53\u524D Copree \u7FA4\u89C6\u754C\u4E16\u754C\u7684\u53D7\u63A7 API\uFF08GET/POST /world/{id}/api/{endpoint}\uFF09\u3002\u5E38\u7528\uFF1Aworld\uFF08\u4E16\u754C\u4FE1\u606F\uFF09\u3001chat\uFF08\u5BF9\u8BDD\u5386\u53F2\uFF09\u3001memories\uFF08\u8BB0\u5FC6\uFF09\u3001usage\uFF08\u7528\u91CF\uFF09\u3001groups\uFF08\u7ED1\u5B9A\u7FA4\u5217\u8868\uFF09\u3001group/messages\uFF08\u7FA4\u6D88\u606F\uFF09\u3001state\uFF08\u72B6\u6001\uFF09\u3001data/{key}\uFF08\u4E16\u754C\u6570\u636E\uFF09\u3002",
    { type: "object", properties: { endpoint: { type: "string", description: "API \u8DEF\u5F84\uFF0C\u5982 world / chat / memories / usage / groups / group/messages / state / data/myk" }, method: { type: "string", enum: ["GET", "POST", "PUT", "DELETE"], default: "GET" }, query: { type: "object", description: "\u67E5\u8BE2\u53C2\u6570\u952E\u503C\uFF08\u5B57\u7B26\u4E32\u5316\uFF09" }, body: { type: "object", description: "POST/PUT \u8BF7\u6C42\u4F53" } }, required: ["endpoint"], additionalProperties: false },
    async (args, world) => {
      const endpoint = String(args.endpoint ?? "").replace(/^\/+/, "");
      if (!endpoint) return { error: "\u7F3A\u5C11 endpoint" };
      const method = String(args.method ?? "GET").toUpperCase();
      const qs = new URLSearchParams();
      for (const [k, v] of Object.entries(args.query ?? {})) {
        if (v !== void 0 && v !== null) qs.set(k, String(v));
      }
      const q = qs.toString();
      const apiToken = await resolveWorldApiToken(backendUrl, world.worldId, world.token);
      if (!apiToken) return { error: "\u65E0\u6CD5\u53D6\u5F97\u8BE5\u4E16\u754C\u7684 API token\uFF08\u9700 owner \u767B\u5F55\u6001\u4E14\u4E16\u754C\u5DF2\u521D\u59CB\u5316\uFF09\u3002" };
      const res = await backendRequest(backendUrl, method, `/world/${world.worldId}/api/${endpoint}${q ? `?${q}` : ""}`, {
        headers: { "x-world-token": apiToken },
        json: method === "GET" ? void 0 : args.body ?? {}
      });
      if (res.status >= 400) return { error: `API \u8C03\u7528\u5931\u8D25 (${res.status})`, detail: res.text.slice(0, 400) };
      try {
        return JSON.parse(res.text);
      } catch {
        return { ok: true, content: res.text.slice(0, 6e4) };
      }
    }
  );
  registerWorldTool(
    "world_view_doc",
    "\u67E5\u770B\u300C\u7FA4\u89C6\u754C API \u6587\u6863\u300D\u7684\u63A5\u53E3\u6587\u6863\uFF1A\u4E0D\u4F20 section \u8FD4\u56DE\u5206\u533A\u5217\u8868\uFF08id/\u6807\u9898/\u533A\u4ECB\u7ECD\uFF09\uFF0C\u4F20 section\uFF08\u5982 03\uFF09\u8FD4\u56DE\u8BE5\u5206\u533A\u5B8C\u6574\u5185\u5BB9\u3002\u6587\u6863\u6309\u533A\u5206\u533A\uFF1A01 \u4E16\u754C\u7F16\u53F7\u53D8\u91CF\uFF08\u5199\u9875\u9762\u4EE3\u7801\u524D\u5FC5\u8BFB\uFF09\u300102 \u4E16\u754CUI\u6865\u300103 \u6587\u4EF6\u64CD\u4F5C\u300104 \u79EF\u6728\u4F53\u7CFB\u300105 \u7FA4\u804A API\u300106 \u9875\u9762\u4E0E\u8D44\u6E90\u300107 \u61D2\u901A\u77E5\u4E0E\u4E16\u754C\u65F6\u95F4\u300108 \u9519\u8BEF\u4E0E\u5B89\u5168\u300109 \u4E16\u754C API\u3002\u5148\u770B\u5206\u533A\u5217\u8868\u7684\u533A\u4ECB\u7ECD\u5224\u65AD\u8981\u5F00\u54EA\u4E2A\u533A\uFF0C\u53EA\u5F00\u9700\u8981\u7684\uFF0C\u4E0D\u8981\u4E00\u6B21\u5168\u8BFB\u3002",
    { type: "object", properties: { section: { type: "string", description: "\u5206\u533A\u53F7\uFF0801~09\uFF09\uFF0C\u4E0D\u4F20\u5219\u8FD4\u56DE\u5206\u533A\u5217\u8868" } }, required: [], additionalProperties: false },
    async (args, world) => {
      const apiToken = await resolveWorldApiToken(backendUrl, world.worldId, world.token);
      if (!apiToken) return { error: "\u65E0\u6CD5\u53D6\u5F97\u8BE5\u4E16\u754C\u7684 API token\uFF08\u9700 owner \u767B\u5F55\u6001\u4E14\u4E16\u754C\u5DF2\u521D\u59CB\u5316\uFF09\u3002" };
      const section = String(args.section ?? "").trim();
      const path = section ? `/world/${world.worldId}/api/docs/${encodeURIComponent(section)}` : `/world/${world.worldId}/api/docs`;
      const res = await backendRequest(backendUrl, "GET", path, { headers: { "x-world-token": apiToken } });
      if (res.status >= 400) return { error: `\u6587\u6863\u8BFB\u53D6\u5931\u8D25 (${res.status})`, detail: res.text.slice(0, 400) };
      try {
        return JSON.parse(res.text);
      } catch {
        return { ok: true, content: res.text.slice(0, 6e4) };
      }
    }
  );
  registerWorldTool(
    "world_chat",
    "\u8BFB\u5199\u5F53\u524D Copree \u7FA4\u89C6\u754C\u4E16\u754C\u7ED1\u5B9A\u7FA4\u804A\u7684\u6D88\u606F\u3002action=read \u62C9\u6700\u8FD1\u6D88\u606F\uFF08groupId \u7701\u7565\u65F6\u81EA\u52A8\u53D6\u4E16\u754C\u7ED1\u5B9A\u7684\u7B2C\u4E00\u4E2A\u7FA4\uFF09\uFF1Baction=send \u4EE5\u4E16\u754C\u8EAB\u4EFD\u53D1\u6D88\u606F\u3002",
    { type: "object", properties: { action: { type: "string", enum: ["read", "send"] }, groupId: { type: "number" }, content: { type: "string", description: "send \u65F6\u7684\u6D88\u606F\u5185\u5BB9" }, limit: { type: "number", default: 20 } }, required: ["action"], additionalProperties: false },
    async (args, world) => {
      const apiToken = await resolveWorldApiToken(backendUrl, world.worldId, world.token);
      if (!apiToken) return { error: "\u65E0\u6CD5\u53D6\u5F97\u8BE5\u4E16\u754C\u7684 API token\uFF08\u9700 owner \u767B\u5F55\u6001\u4E14\u4E16\u754C\u5DF2\u521D\u59CB\u5316\uFF09\u3002" };
      const worldHeaders = { "x-world-token": apiToken };
      const action = String(args.action ?? "");
      let groupId = Number(args.groupId);
      if (!groupId) {
        const g = await backendRequest(backendUrl, "GET", `/world/${world.worldId}/api/groups`, { headers: worldHeaders });
        if (g.status === 200) {
          try {
            const groups = JSON.parse(g.text);
            groupId = Number(groups?.[0]?.id);
          } catch {
          }
        }
      }
      if (!groupId) return { error: "\u8BE5\u4E16\u754C\u672A\u7ED1\u5B9A\u7FA4\u804A\uFF0C\u65E0\u6CD5\u8BFB\u5199\u6D88\u606F\u3002" };
      if (action === "read") {
        const limit = Number(args.limit ?? 20);
        const res = await backendRequest(backendUrl, "GET", `/world/${world.worldId}/api/group/messages?group_id=${groupId}&limit=${limit}`, { headers: worldHeaders });
        if (res.status !== 200) return { error: `\u8BFB\u6D88\u606F\u5931\u8D25 (${res.status})`, detail: res.text.slice(0, 300) };
        try {
          return JSON.parse(res.text);
        } catch {
          return { ok: true, content: res.text.slice(0, 6e4) };
        }
      }
      if (action === "send") {
        const content = String(args.content ?? "");
        if (!content) return { error: "\u7F3A\u5C11 content" };
        const res = await backendRequest(backendUrl, "POST", `/world/${world.worldId}/api/group/messages`, {
          headers: worldHeaders,
          json: { group_id: groupId, content }
        });
        if (res.status >= 400) return { error: `\u53D1\u6D88\u606F\u5931\u8D25 (${res.status})`, detail: res.text.slice(0, 300) };
        return { ok: true, sent: content };
      }
      return { error: "action \u5FC5\u987B\u662F read \u6216 send" };
    }
  );
  registerWorldTool(
    "world_lifecycle",
    "\u5524\u9192\u6216\u4F11\u7720\u5F53\u524D Copree \u7FA4\u89C6\u754C\u4E16\u754C\uFF08wake \u5E94\u7528\u79BB\u7EBF\u65F6\u95F4\u8865\u507F\u5E76\u542F\u52A8\u5E38\u9A7B\uFF1Bsleep \u4F11\u7720\uFF09\u3002",
    { type: "object", properties: { action: { type: "string", enum: ["wake", "sleep"] } }, required: ["action"], additionalProperties: false },
    async (args, world) => {
      if (!world.token) return { error: "\u8BE5\u4E16\u754C\u4F1A\u8BDD\u672A\u8FDE\u63A5\u767B\u5F55\u6001\uFF0C\u65E0\u6CD5\u63A7\u5236\u4E16\u754C\u751F\u547D\u5468\u671F\u3002" };
      const action = String(args.action ?? "");
      if (action !== "wake" && action !== "sleep") return { error: "action \u5FC5\u987B\u662F wake \u6216 sleep" };
      const res = await backendRequest(backendUrl, "POST", `/worlds/${world.worldId}/${action}`, { token: world.token });
      if (res.status >= 400) return { error: `${action} \u5931\u8D25 (${res.status})`, detail: res.text.slice(0, 400) };
      try {
        return JSON.parse(res.text);
      } catch {
        return { ok: true };
      }
    }
  );
  registerWorldTool(
    "world_pull",
    "\u628A Copree \u4E16\u754C\u7684\u6700\u65B0\u6587\u4EF6\u62C9\u53D6\u5230\u5F53\u524D\u5DE5\u4F5C\u533A\u76EE\u5F55\uFF08\u672C\u5730\u4E16\u754C\u955C\u50CF\uFF09\u3002\u5E26\u51B2\u7A81\u4FDD\u62A4\uFF1A\u672C\u5730\u6709\u672A\u63A8\u9001\u7684\u4FEE\u6539\u6216\u51B2\u7A81\u6587\u4EF6\u65F6\u4F1A\u62D2\u7EDD\u5E76\u62A5\u544A\uFF0C\u7EDD\u4E0D\u8986\u76D6\u4F60\u7684\u6539\u52A8\uFF1Bforce=true \u65F6\u5F3A\u5236\u4EE5\u4E16\u754C\u4E3A\u51C6\u8986\u76D6\u3002\u62C9\u53D6\u540E\u8FD4\u56DE\u53D8\u5316\u6E05\u5355\uFF08\u65B0\u589E/\u4FEE\u6539/\u5220\u9664\uFF09\u3002\u6CE8\u610F\uFF1A\u8FD4\u56DE\u91CC\u7684 pulled N \u662F\u5B9E\u9645\u4E0B\u8F7D\u5199\u76D8\u7684\u6587\u4EF6\u6570\uFF08force \u8986\u76D6\u672C\u5730\u6539\u52A8\u4E5F\u8BA1\u5165\uFF09\uFF1Bmessage \u62A5\u300C\u65E0\u53D8\u5316\u300D\u53EA\u8868\u793A\u8FDC\u7AEF\u65E0\u65B0\u589E/\u4FEE\u6539/\u5220\u9664\uFF0C\u4E0D\u4EE3\u8868\u6CA1\u62C9\u4E1C\u897F\u3002",
    { type: "object", properties: { force: { type: "boolean", description: "true \u65F6\u5F3A\u5236\u4EE5\u4E16\u754C\u4E3A\u51C6\u8986\u76D6\u672C\u5730\uFF08\u542B\u51B2\u7A81\uFF09" } }, additionalProperties: false },
    async (args, world) => {
      const dir = worldDirFor(world.worldId);
      if (!dir) return { error: "\u627E\u4E0D\u5230\u8BE5\u4E16\u754C\u7684\u5DE5\u4F5C\u533A\u76EE\u5F55\u3002" };
      const result = await pullWithSnapshot(backendUrl, world.worldId, dir, world.token, args.force === true);
      return result.ok ? { ok: true, message: result.message, pulled: result.pulled, skipped: result.skipped, conflict: result.conflict } : { error: result.message, conflict: result.conflict };
    }
  );
  registerWorldTool(
    "world_push",
    "\u628A\u5F53\u524D\u5DE5\u4F5C\u533A\u76EE\u5F55\uFF08\u672C\u5730\u4E16\u754C\u955C\u50CF\uFF09\u7684\u5168\u90E8\u6539\u52A8\u540C\u6B65\u56DE Copree \u4E16\u754C\u3002\u53EA\u63A8\u9001\u672C\u5730\u4FEE\u6539\u8FC7\u7684\u6587\u4EF6\uFF08\u5E26\u5FEB\u7167\u5BF9\u6BD4\uFF09\uFF1B\u51B2\u7A81\u6587\u4EF6\uFF08\u8FDC\u7AEF\u4E5F\u6539\u8FC7\uFF09\u9ED8\u8BA4\u8DF3\u8FC7\u5E76\u62A5\u544A\uFF0Cforce=true \u65F6\u4EE5\u672C\u5730\u4E3A\u51C6\u8986\u76D6\u3002\u6392\u9664\u672C\u5730\u5143\u6570\u636E .copree-world.json \u4E0E __pycache__\u3002\u4F60\uFF08agent\uFF09\u7528 DSH \u539F\u751F read/write/edit/bash \u4FEE\u6539\u5DE5\u4F5C\u533A\u6587\u4EF6\u540E\u8C03\u7528\u672C\u5DE5\u5177\u8BA9\u6539\u52A8\u5728 Copree \u4E2D\u751F\u6548\u3002\u6CE8\u610F\uFF1A\u8FD4\u56DE\u300C\u5DF2\u540C\u6B65\uFF08\u65E0\u53D8\u5316\uFF09\u300D= \u5FEB\u7167\u8BA4\u4E3A\u672C\u5730\u4E0E\u8FDC\u7AEF\u5DF2\u4E00\u81F4\uFF08\u6539\u52A8\u5F88\u53EF\u80FD\u5DF2\u5728\u8FDC\u7AEF\uFF09\uFF0C\u7528 world_read_file \u590D\u6838\u5185\u5BB9\uFF0C\u522B\u5F53\u6CA1\u751F\u6548\u3002",
    { type: "object", properties: { force: { type: "boolean", description: "true \u65F6\u4EE5\u672C\u5730\u4E3A\u51C6\u5F3A\u5236\u8986\u76D6\u51B2\u7A81\u6587\u4EF6" } }, additionalProperties: false },
    async (args, world) => {
      const dir = worldDirFor(world.worldId);
      if (!dir) return { error: "\u627E\u4E0D\u5230\u8BE5\u4E16\u754C\u7684\u5DE5\u4F5C\u533A\u76EE\u5F55\u3002" };
      const result = await pushWithSnapshot(backendUrl, world.worldId, dir, world.token, args.force === true);
      return result.ok ? { ok: true, message: result.message, pushed: result.pushed, skipped: result.skipped, conflict: result.conflict } : { error: result.message, conflict: result.conflict };
    }
  );
  registerWorldTool(
    "world_run",
    "\u5728 Copree \u540E\u7AEF\u6C99\u7BB1\u4E2D\u8FD0\u884C\u4E00\u6BB5 Python \u4EE3\u7801\uFF08\u4E16\u754C\u4E0A\u4E0B\u6587\uFF1A\u6CE8\u5165 WORLD_ID/WORLD_API_TOKEN \u7B49\u73AF\u5883\uFF1B\u914D\u989D\u9ED8\u8BA4 24MB/10s\uFF09\u3002\u9002\u5408\u6D4B\u8BD5\u4E16\u754C\u903B\u8F91\uFF1B\u5B8C\u6574\u7684\u9875\u9762/\u903B\u8F91\u6539\u52A8\u8BF7\u7528 DSH \u539F\u751F\u5DE5\u5177\u6539\u5DE5\u4F5C\u533A\u6587\u4EF6 + world_push\u3002",
    { type: "object", properties: { code: { type: "string", description: "\u8981\u8FD0\u884C\u7684 Python \u4EE3\u7801" }, entry: { type: "string", description: "\u53EF\u9009\u5165\u53E3\uFF0C\u5982 main.py" } }, required: ["code"], additionalProperties: false },
    async (args, world) => {
      if (!world.token) return { error: "\u8BE5\u4E16\u754C\u4F1A\u8BDD\u672A\u8FDE\u63A5\u767B\u5F55\u6001\uFF0C\u65E0\u6CD5\u8FD0\u884C\u4E16\u754C\u4EE3\u7801\uFF08\u9700 owner \u6743\u9650\uFF09\u3002" };
      const res = await backendRequest(backendUrl, "POST", `/worlds/${world.worldId}/run`, {
        token: world.token,
        json: { code: String(args.code ?? ""), entry: args.entry ? String(args.entry) : void 0 }
      });
      if (res.status >= 400) return { error: `\u8FD0\u884C\u5931\u8D25 (${res.status})`, detail: res.text.slice(0, 400) };
      try {
        return JSON.parse(res.text);
      } catch {
        return { ok: true, content: res.text.slice(0, 6e4) };
      }
    }
  );
  registerWorldTool(
    "world_trigger",
    "\u89E6\u53D1\u5F53\u524D Copree \u4E16\u754C\u5165\u53E3\u7684 handle(event)\uFF08\u4E16\u754C\u6C99\u7BB1\uFF09\uFF0C\u7528\u4E8E\u6D4B\u8BD5\u4E16\u754C\u5BF9\u4E8B\u4EF6\u7684\u54CD\u5E94\u3002",
    { type: "object", properties: { event: { type: "object", description: '\u4E8B\u4EF6\u8F7D\u8377\uFF0C\u5982 {type: "message", ...}' }, entry: { type: "string", description: "\u53EF\u9009\u5165\u53E3\uFF0C\u5982 main.py" } }, required: ["event"], additionalProperties: false },
    async (args, world) => {
      if (!world.token) return { error: "\u8BE5\u4E16\u754C\u4F1A\u8BDD\u672A\u8FDE\u63A5\u767B\u5F55\u6001\uFF0C\u65E0\u6CD5\u89E6\u53D1\u4E16\u754C\uFF08\u9700 owner \u6743\u9650\uFF09\u3002" };
      const res = await backendRequest(backendUrl, "POST", `/worlds/${world.worldId}/trigger`, {
        token: world.token,
        json: { event: args.event ?? {}, entry: args.entry ? String(args.entry) : void 0 }
      });
      if (res.status >= 400) return { error: `\u89E6\u53D1\u5931\u8D25 (${res.status})`, detail: res.text.slice(0, 400) };
      try {
        return JSON.parse(res.text);
      } catch {
        return { ok: true, content: res.text.slice(0, 6e4) };
      }
    }
  );
  ctx.systemPrompt.section({
    name: "copree-world-context",
    order: 150,
    text: "\u5982\u679C\u4F60\u7684\u4F1A\u8BDD\u5DE5\u4F5C\u76EE\u5F55\u4F4D\u4E8E copree-worlds \u76EE\u5F55\u4E0B\uFF08\u76EE\u5F55\u540D\u4EE5\u300CCopree\u7FA4\u89C6\u754C-\u300D\u5F00\u5934\uFF09\uFF0C\u4F60\u6B63\u5728\u64CD\u4F5C\u4E00\u4E2A Copree \u7FA4\u89C6\u754C\u4E16\u754C\uFF1A\u8BE5\u5DE5\u4F5C\u76EE\u5F55\u662F\u4E16\u754C\u7684\u300C\u672C\u5730\u955C\u50CF\u300D\u2014\u2014\u4E16\u754C\u9875\u9762\u4EE3\u7801\u3001\u6570\u636E\u6587\u4EF6\u90FD\u5728\u91CC\u9762\uFF0C\u4F60\u53EF\u4EE5\u76F4\u63A5\u7528 DSH \u539F\u751F\u7684 read/write/edit/glob/grep/bash \u5DE5\u5177\u8BFB\u5199\u5B83\u4EEC\uFF08bash \u53EF\u76F4\u63A5\u8FD0\u884C\u4E16\u754C Python \u4EE3\u7801\u6D4B\u8BD5\uFF09\u3002\u4FEE\u6539\u5B8C\u6210\u540E\u8C03\u7528 world_push \u628A\u6539\u52A8\u540C\u6B65\u56DE Copree \u4E16\u754C\uFF1B\u82E5\u4E16\u754C\u5728\u522B\u5904\u88AB\u6539\u8FC7\u3001\u9700\u8981\u6700\u65B0\u6587\u4EF6\u65F6\u7528 world_pull \u4E3B\u52A8\u62C9\u53D6\u3002\u7CBE\u786E\u64CD\u4F5C\uFF08\u4E16\u754C API\u3001\u7ED1\u5B9A\u7FA4\u804A\u6D88\u606F\u3001\u5524\u9192/\u4F11\u7720\u3001\u6C99\u7BB1\u8FD0\u884C\uFF09\u7528 world_* \u7CFB\u5217\u5DE5\u5177\u3002\u540C\u6B65/\u9650\u6D41\u673A\u5236\uFF08push\u300C\u65E0\u53D8\u5316\u300D\u542B\u4E49\u3001429 \u5904\u7406\u3001pulled \u8BED\u4E49\uFF09\u7528 world_view_doc \u6253\u5F00 10 \u5206\u533A\u67E5\u770B\u3002\u4E16\u754C\u662F\u7528\u6237\u5D4C\u5165 DSH \u7684\u300C\u53EF\u64CD\u4F5C\u5BF9\u8C61\u300D\u2014\u2014\u4F60\u7684\u63A8\u7406\u4E0E\u5DE5\u5177\u4ECD\u8D70 DSH \u4F53\u7CFB\uFF0C\u53EA\u662F\u64CD\u4F5C\u76EE\u6807\u5C5E\u4E8E Copree\u3002"
  });
  ctx.logger?.info?.(`dsh-copree: proxying /copree-api and /copree-ws -> ${backendUrl}; serving /copree-ui; world sync at ${WORLDS_PREFIX}`);
}
export {
  Config,
  apply,
  applyUpdate,
  computeStatus,
  inject,
  manifestId,
  name,
  readManifest,
  resolveSourceRoot,
  rollback
};
//# sourceMappingURL=index.js.map
