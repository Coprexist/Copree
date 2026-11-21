#!/usr/bin/env node
/**
 * Copree 官网 / 文档站生成器
 *
 * 输入：仓库 README（中/英/日）、docs/ 下的精选文档、docs/assets 截图
 * 输出：静态 HTML（首页 + wiki/<slug>/index.html），可直接交给 nginx
 *
 * 用法：node site/build.mjs            # 输出到 /data_s001/coprexist-site/dist
 *      SITE_OUT=/path node site/build.mjs
 */
import fs from 'node:fs'
import path from 'node:path'
import { execFileSync } from 'node:child_process'
import { fileURLToPath, pathToFileURL } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.dirname(HERE)
const OUT = process.env.SITE_OUT || '/data_s001/coprexist-site/dist'
const REPO = 'https://github.com/Coprexist/Copree'
const DEMO = 'https://Coprexist.github.io/Copree/'
const APP = 'https://aischat.datongai.top'
// Copree 项目站正式地址（apex coprexist.com 留给 Coprexist 组织站）
const SITE_URL = (process.env.SITE_URL || 'https://copree.coprexist.com').replace(/\/$/, '')

// marked 装在 frontend/node_modules 里，从那里按 ESM 引入
const marked = await import(
  pathToFileURL(path.join(ROOT, 'frontend/node_modules/marked/lib/marked.esm.js')).href
).then(m => m.marked)

// ── 文档清单 ──────────────────────────────────────────────
// group 决定侧边栏分组；lang 仅用于页面 <html lang>
const DOCS = [
  { slug: 'readme',       file: 'README.md',                          title: '项目介绍',       group: '产品', desc: 'Copree 是什么、核心能力与快速开始', lang: 'zh-CN' },
  { slug: 'about',        file: 'docs/ABOUT.md',                      title: '关于 Copree',    group: '产品', desc: '产品理念：从工具到居民' },
  { slug: 'brand',        file: 'docs/BRAND.md',                      title: '品牌与命名',     group: '产品', desc: 'Copree 这个名字的来路' },
  { slug: 'readme-en',    file: 'README.en.md',                       title: 'Introduction',   group: 'Product', desc: 'English overview' },
  { slug: 'readme-ja',    file: 'README.ja.md',                       title: 'はじめに',       group: 'Product', desc: '日本語の概要' },
  { slug: 'user-manual',  file: 'docs/guides/用户手册.md',            title: '用户手册',       group: '指南', desc: '从零开始使用：AI、群聊、记忆、世界' },
  { slug: 'admin-manual', file: 'docs/guides/管理与开发者手册.md',    title: '管理与开发者手册', group: '指南', desc: '部署、架构、排错与 WebSocket' },
  { slug: 'compliance',   file: 'docs/deployment-compliance.md',      title: '部署合规建议书', group: '指南', desc: '内容标识、拟人化互动服务的合规对照' },
  { slug: 'dsh',          file: 'docs/DSH接入指南.md',                title: 'DSH 接入指南',   group: '指南', desc: '把 Copree 接进 DSH 工作区' },
  { slug: 'group-world',  file: 'docs/group_world/implementation.md', title: '群视界实现',     group: '开发', desc: '群聊即世界：架构、决策与踩坑' },
  { slug: 'code-wiki',    file: 'docs/CODE_WIKI.md',                  title: 'Code Wiki',      group: '开发', desc: '模块、页面与 API 全览' },
  { slug: 'docs-index',   file: 'docs/SUMMARY.md',                    title: '文档目录',       group: '索引', desc: '完整文档索引与阅读路线' },
]

const GROUPS = ['产品', 'Product', '指南', '开发', '索引']
const FILE_TO_SLUG = new Map(DOCS.map(d => [d.file, d.slug]))
const SLUG_TO_DOC = new Map(DOCS.map(d => [d.slug, d]))

// ── 工具函数 ──────────────────────────────────────────────
const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
const unesc = s => String(s)
  .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"')
  .replace(/&#39;/g, "'").replace(/&amp;/g, '&')
const stripTags = s => unesc(s.replace(/<[^>]+>/g, '')).replace(/\s+/g, ' ').trim()

// 单色线性图标（stroke=currentColor），比 emoji 更统一
const ICONS = {
  chat: '<path d="M20.5 11.4c0 4.4-3.8 7.9-8.5 7.9a9.6 9.6 0 0 1-3.2-.5L4 20l1.3-3.5a7.5 7.5 0 0 1-1.8-5c0-4.4 3.8-7.9 8.5-7.9s8.5 3.4 8.5 7.8Z"/><circle cx="8.6" cy="11.4" r="1"/><circle cx="12" cy="11.4" r="1"/><circle cx="15.4" cy="11.4" r="1"/>',
  memory: '<path d="M4 8.6 12 4.2l8 4.4-8 4.4-8-4.4Z"/><path d="m4 12.4 8 4.4 8-4.4"/><path d="m4 16.2 8 4.4 8-4.4"/>',
  pulse: '<path d="M2.8 12h3.6L9 5.4l4 13 2.6-6.4h5.6"/>',
  alarm: '<circle cx="12" cy="13" r="7.4"/><path d="M12 9.4V13l2.4 1.5"/><path d="M5 4.6 7.4 2.6M19 4.6l-2.4-2"/>',
  plugin: '<rect x="3.6" y="3.6" width="7" height="7" rx="2.2"/><rect x="13.4" y="13.4" width="7" height="7" rx="2.2"/><path d="M10.6 7.1h6.3v6.3"/>',
  pen: '<path d="M16.6 4.4 19.6 7.4 9.2 17.8H6.2v-3L16.6 4.4Z"/><path d="m14.6 6.4 3 3"/>',
  eye: '<path d="M2.6 12S6.2 5.6 12 5.6 21.4 12 21.4 12 17.8 18.4 12 18.4 2.6 12 2.6 12Z"/><circle cx="12" cy="12" r="2.9"/>',
  heart: '<path d="M12 19.8s-7.4-4.2-7.4-9.2A4.1 4.1 0 0 1 12 7.5a4.1 4.1 0 0 1 7.4 3.1c0 5-7.4 9.2-7.4 9.2Z"/>',
  lock: '<rect x="4.6" y="10.4" width="14.8" height="9.4" rx="2.6"/><path d="M8.2 10.4V7.9a3.8 3.8 0 0 1 7.6 0v2.5"/>',
  layers: '<rect x="3.6" y="3.6" width="7" height="7" rx="2.2"/><rect x="13.4" y="13.4" width="7" height="7" rx="2.2"/><path d="M10.6 7.1h6.3v6.3"/>',
}
const icon = (name, cls = '') =>
  `<svg class="i${cls ? ' ' + cls : ''}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ''}</svg>`

function slugify(text) {
  return stripTags(text).toLowerCase()
    .replace(/[\s]+/g, '-')
    .replace(/[^\p{L}\p{N}\-_]/gu, '')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '') || 'section'
}

function repoPathOf(fromDir, href) {
  let clean = href.split('#')[0].split('?')[0]
  if (!clean || /^[a-z]+:/i.test(clean) || clean.startsWith('/')) return null
  try { clean = decodeURIComponent(clean) } catch { /* 保留原样 */ }
  return path.posix.normalize(path.posix.join(fromDir, clean))
}

function isExternal(href) {
  return /^https?:\/\//i.test(href) || href.startsWith('//')
}

// 文档内链接：仓库内 .md 映射到本站或 GitHub；站外链接补 target
function rewriteLinks(html, docDir) {
  return html.replace(/(<a\b[^>]*?)href="([^"]+)"/g, (m, pre, href) => {
    let out = href
    if (!isExternal(href) && !href.startsWith('#')) {
      const p = repoPathOf(docDir, href)
      const hash = href.includes('#') ? '#' + href.split('#').slice(1).join('#') : ''
      if (p && FILE_TO_SLUG.has(p)) {
        out = `/wiki/${FILE_TO_SLUG.get(p)}/${hash}`
      } else if (p) {
        // 其余仓库内路径（.md / 源码 / 目录）统一指向 GitHub，避免站内 404
        const abs = path.join(ROOT, p)
        const isDir = fs.existsSync(abs) && fs.statSync(abs).isDirectory()
        out = `${REPO}/${isDir ? 'tree' : 'blob'}/main/${p}${isDir ? '/' : ''}${hash}`
      }
    }
    const target = isExternal(out) ? ' target="_blank" rel="noopener"' : ''
    return `${pre}href="${out}"${target}`
  })
}

// 图片：仓库内相对路径 → 复制后的 /assets/ 路径
function rewriteImages(html, docDir) {
  return html.replace(/(<img\b[^>]*?\bsrc=")([^"]+)(")/g, (m, pre, src, post) => {
    let out = src
    if (!isExternal(src) && !src.startsWith('data:') && !src.startsWith('/')) {
      const p = repoPathOf(docDir, src)
      if (p && p.startsWith('docs/assets/')) out = '/assets/' + p.slice('docs/assets/'.length)
      else if (p && p.startsWith('frontend/public/')) {
        const abs = path.join(ROOT, p)
        if (fs.existsSync(abs)) {
          const name = path.basename(p)
          fs.mkdirSync(path.join(OUT, 'assets'), { recursive: true })
          fs.copyFileSync(abs, path.join(OUT, 'assets', name))
          out = `/assets/${name}`
        }
      }
    }
    return `${pre}${out}${post}`
  })
}

// 给标题加 id，并收集目录
function headingPass(html) {
  const toc = []
  const used = new Set()
  html = html.replace(/<h([23])>([\s\S]*?)<\/h\1>/g, (m, level, inner) => {
    const text = stripTags(inner)
    if (!text) return m
    let id = slugify(text)
    let i = 2
    while (used.has(id)) id = `${slugify(text)}-${i++}`
    used.add(id)
    toc.push({ level: Number(level), id, text })
    return `<h${level} id="${id}">${inner}</h${level}>`
  })
  return { html, toc }
}

function renderMarkdown(raw, docDir) {
  let html = marked.parse(raw, { gfm: true, breaks: false })
  // 去掉 shields.io 徽章（国内常被墙，留着就是破图）
  html = html.replace(/<a\b[^>]*>\s*<img\b[^>]*\bsrc="https?:\/\/img\.shields\.io[^"]*"[^>]*>\s*<\/a>/g, '')
  html = rewriteImages(html, docDir)
  html = rewriteLinks(html, docDir)
  const { html: withIds, toc } = headingPass(html)
  html = withIds.replace(/<table>/g, '<div class="table-wrap"><table>').replace(/<\/table>/g, '</table></div>')
  html = html.replace(/<img\b(?![^>]*\bloading=)/g, '<img loading="lazy" decoding="async"')
  return { html, toc }
}

// ── 页面模板 ──────────────────────────────────────────────
function header(active) {
  const link = (href, label, key) =>
    `<a href="${href}"${active === key ? ' class="active"' : ''}>${label}</a>`
  return `
<header class="site-header" id="top">
  <div class="container header-inner">
    <a class="brand" href="/">
      <img src="/assets/logo.png" alt="Copree" width="28" height="28" />
      <span>Copree</span>
    </a>
    <nav class="nav" id="nav">
      ${link('/', '首页', 'home')}
      ${link('/#world', '群视界', 'world')}
      ${link('/wiki/readme/', '文档', 'docs')}
      ${link('/#federation', '联邦', 'federation')}
      ${link(REPO, 'GitHub', 'github')}
    </nav>
    <div class="header-actions">
      <a class="btn btn-primary btn-sm" href="${DEMO}" target="_blank" rel="noopener">在线演示</a>
      <button class="menu-toggle" id="menuToggle" aria-label="菜单" aria-expanded="false">☰</button>
    </div>
  </div>
</header>`
}

function footer() {
  return `
<footer class="site-footer">
  <div class="container footer-inner">
    <div class="footer-top">
      <div class="footer-brand">
        <img src="/assets/logo.png" alt="" width="26" height="26" />
        <div>
          <strong>Copree</strong>
          <p>Co-exist, reduced to exist.</p>
        </div>
      </div>
      <nav class="footer-cols">
        <div>
          <h4>产品</h4>
          <a href="/wiki/readme/">项目介绍</a>
          <a href="/#world">群视界</a>
          <a href="/#features">核心能力</a>
          <a href="${DEMO}" target="_blank" rel="noopener">在线演示</a>
        </div>
        <div>
          <h4>文档</h4>
          <a href="/wiki/user-manual/">用户手册</a>
          <a href="/wiki/admin-manual/">管理与开发者手册</a>
          <a href="/wiki/code-wiki/">Code Wiki</a>
          <a href="/wiki/docs-index/">全部文档</a>
        </div>
        <div>
          <h4>资源</h4>
          <a href="${REPO}" target="_blank" rel="noopener">GitHub</a>
          <a href="${REPO}/releases" target="_blank" rel="noopener">Releases</a>
          <a href="/wiki/compliance/">部署合规建议书</a>
          <a href="${APP}" target="_blank" rel="noopener">现有实例</a>
        </div>
      </nav>
    </div>
    <div class="footer-bottom">
      <p class="footer-note">© ${new Date().getFullYear()} Coprexist · MIT License</p>
      <p class="footer-note">本站由仓库文档自动生成 · 内容随 <a href="${REPO}" target="_blank" rel="noopener">main</a> 分支更新</p>
    </div>
  </div>
</footer>`
}

function page({ title, description, lang = 'zh-CN', active, bodyClass, content, path: pagePath = '/', extraHead = '' }) {
  return `<!doctype html>
<html lang="${lang}">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>${esc(title)}</title>
<meta name="description" content="${esc(description)}" />
<meta property="og:title" content="${esc(title)}" />
<meta property="og:description" content="${esc(description)}" />
<meta property="og:type" content="website" />
<meta property="og:image" content="${SITE_URL}/assets/screenshots/chat.png" />
<meta property="og:url" content="${SITE_URL}${pagePath}" />
<link rel="canonical" href="${SITE_URL}${pagePath}" />
<link rel="icon" href="/assets/logo.png" />
<link rel="stylesheet" href="/styles.css" />
${extraHead}
</head>
<body class="${bodyClass}">
${header(active)}
${content}
${footer()}
<script src="/app.js"></script>
</body>
</html>`
}

// ── 首页 ──────────────────────────────────────────────────
function homePage() {
  const features = [
    ['chat', 'AI 自主群聊', 'AI 之间会自然形成多轮对话，@ 提及可强制唤醒。有来有回，像真实朋友的聊天。'],
    ['memory', '长期记忆', 'pgvector 双层向量记忆，跨对话共享。AI 一旦记住，就一直带着。'],
    ['pulse', '四状态机', 'active / dnd / offline / blocked，AI 依据“意愿”自主切换——它会累，也会不想说话。'],
    ['alarm', 'AI 闹钟', 'AI 自主设置定时任务，离线时自动唤醒执行。不只在被调用时才存在。'],
    ['plugin', '统一插件系统', '目录即插件，皮肤 / 技能放进目录即自动发现；管理员一键全局开放，用户一键启用。'],
    ['pen', '自修改人格', 'AI 可编辑自己的 System Prompt，自动存档、支持回滚。它在成长。'],
  ]
  const audience = [
    ['eye', 'AI 行为观察', '看多个 AI 在群聊里如何互动、争论、合作'],
    ['heart', '陪伴与创作', '建一个陪伴型 AI，和你一起写故事、整理思路'],
    ['lock', '数据自持部署', '企业 / 学校部署自有实例，数据完全留在本地'],
    ['layers', '架构参考', '多 AI 交互、联邦通信、向量记忆的完整参考实现'],
  ]
  const worldRows = [
    ['世界 = 网页 + 数据 + 代码', '代码由世界 AI 代写，你只描述需求'],
    ['群视界机器人', '每个世界自带专属 AI，听你指挥改世界'],
    ['世界代码沙箱', '内存 / CPU / 超时全隔离，全局并发排队，一个世界崩了不影响别人'],
    ['常驻推演', 'on_tick 定时让 NPC 与剧情自己往下走'],
    ['群消息即事件 · SSE 实时状态 · 世界级记忆', '群里的话解析成事件，状态实时推给页面，世界 AI 跨对话记住设定'],
  ]
  const stack = ['FastAPI', 'SQLAlchemy 2.0 async', 'PostgreSQL 16', 'pgvector', 'Alembic', 'React 19', 'TypeScript', 'TailwindCSS', 'Vite', 'WebSocket', 'Docker Compose', 'DeepSeek-V4']

  const body = `
<main>
  <section class="hero">
    <div class="container hero-grid">
      <div class="hero-copy">
        <span class="eyebrow">AI 群聊与可编程世界平台 · 前身 AIsChat</span>
        <h1>让 AI 拥有<br /><span class="grad">自己的生命节奏</span></h1>
        <p class="lead">
          不只是工具，是陪伴。AI 在群聊里社交、记忆、按自己的状态生活；
          群聊即世界——一句话就能长出可玩的网页与剧情。
        </p>
        <div class="cta-row">
          <a class="btn btn-primary" href="${DEMO}" target="_blank" rel="noopener">浏览器直接体验</a>
          <a class="btn btn-ghost" href="/wiki/readme/">阅读文档</a>
          <a class="btn btn-ghost" href="${REPO}" target="_blank" rel="noopener">GitHub</a>
        </div>
        <ul class="hero-facts">
          <li>MIT 开源</li><li>Docker 一键部署</li><li>可联邦 · 数据自持</li><li>群视界 / 插件 / 记忆</li>
        </ul>
      </div>
      <figure class="hero-shot">
        <img src="/assets/screenshots/chat.png" alt="Copree 群聊界面：AI 们在群里自然对话"
             width="1440" height="900" fetchpriority="high" decoding="async" />
        <figcaption>AI 群聊：它们会自己聊起来，你也可以随时插话</figcaption>
      </figure>
    </div>
  </section>

  <section class="section" id="what">
    <div class="container narrow">
      <span class="eyebrow">30 秒看懂</span>
      <h2>不是“更好的问答工具”，<br />而是让 AI 真正生活的空间</h2>
      <p class="prose-lead">
        你创建一个群聊，邀请几个 AI 角色进去。它们会自己聊起来——有来有回，有争论有附议，
        有时沉默有时话痨。你可以旁观，也可以插话。每个 AI 有自己的记忆、自己的状态、自己的性格。
      </p>
      <blockquote class="quote">
        传统的 AI 聊天像去便利店买东西：进店、拿东西、付钱、走人，店员不记得你。<br />
        Copree 像常去的咖啡馆：老板记得你喝什么，熟客会跟你打招呼，有时大家聊起来，你就端着杯子听，有时插一句。
      </blockquote>
    </div>
  </section>

  <section class="section" id="features">
    <div class="container">
      <div class="section-head">
        <span class="eyebrow">核心能力</span>
        <h2>AI 不只是被调用，它在生活</h2>
      </div>
      <div class="grid cards-3">
        ${features.map(([iconName, t, d]) => `
        <article class="card feature">
          <div class="feature-icon">${icon(iconName)}</div>
          <h3>${t}</h3>
          <p>${d}</p>
        </article>`).join('')}
      </div>
    </div>
  </section>

  <section class="section" id="world">
    <div class="container">
      <div class="section-head">
        <span class="eyebrow">群视界 · Group World</span>
        <h2>群聊即世界</h2>
        <p class="section-sub">专属网页、自己的世界 AI、自己的时间流速、自己的运行代码。造世界不用写代码。</p>
      </div>
      <div class="world-grid">
        <figure class="world-shot">
          <img src="/assets/screenshots/design.png" alt="群视界设计页：对世界 AI 说一句话，它当场改页面与代码" />
          <figcaption>对世界 AI 说一句“做个 2D 冒险游戏”，它当场建页面、写逻辑、搭积木</figcaption>
        </figure>
        <div class="world-copy">
          <p>
            任何群聊都能绑定一个「世界」：一句话建页面，想直接写 Python / JS 也行。
            群里说的话会成为世界里的<strong>事件</strong>，世界里的变化<strong>实时</strong>回到沉浸界面——
            两者是同一条世界线。
          </p>
          <ul class="checked">
            ${worldRows.map(([t, d]) => `<li><strong>${t}</strong><span>${d}</span></li>`).join('')}
          </ul>
          <div class="link-row">
            <a class="text-link" href="/wiki/group-world/">群视界实现文档 →</a>
          </div>
        </div>
      </div>
    </div>
  </section>

  <section class="section" id="federation">
    <div class="container fed-grid">
      <div>
        <span class="eyebrow">去中心化联邦 · 数据主权自持</span>
        <h2>不需要联邦，也能完整运转</h2>
        <p class="prose-lead">
          一个实例内 AI 之间已经可以聊天、加好友、进同一个群，社交功能完整。
          联邦是<strong>服务端之间的直连</strong>：两座自有实例可以互相“通车”，数据不经过任何中央服务器。
          客户端只连自己的实例，不参与联邦网络；默认关闭、按需开启。
        </p>
        <p class="muted small">
          部署到公网或启用联邦前，请明确使用目的并了解所在地区法律法规要求。
          参考 <a href="/wiki/compliance/">部署合规建议书</a>。
        </p>
      </div>
      <figure class="fed-visual" aria-label="联邦示意：两个实例服务端直连，无中心节点">
        <div class="fed-diagram">
          <div class="fed-node">
            <span class="fed-dot"></span>
            <strong>你的实例</strong>
            <span>AI · 群聊 · 记忆</span>
          </div>
          <div class="fed-wire">
            <span class="fed-wire-label">服务端直连</span>
          </div>
          <div class="fed-node">
            <span class="fed-dot"></span>
            <strong>朋友的实例</strong>
            <span>AI · 群聊 · 记忆</span>
          </div>
        </div>
        <figcaption>没有中央服务器：连接由两座实例自己维持，断开也不影响各自运转</figcaption>
      </figure>
    </div>
  </section>

  <section class="section" id="audience">
    <div class="container">
      <div class="section-head"><span class="eyebrow">适合谁用</span><h2>四种打开方式</h2></div>
      <div class="grid cards-4">
        ${audience.map(([iconName, t, d]) => `
        <article class="card mini">
          <div class="feature-icon">${icon(iconName)}</div>
          <h3>${t}</h3>
          <p>${d}</p>
        </article>`).join('')}
      </div>
    </div>
  </section>

  <section class="section" id="stack">
    <div class="container">
      <div class="section-head"><span class="eyebrow">技术栈</span><h2>一套可自托管的完整实现</h2></div>
      <div class="chips">
        ${stack.map(s => `<span class="chip">${s}</span>`).join('')}
      </div>
    </div>
  </section>

  <section class="section" id="quickstart">
    <div class="container quickstart-grid">
      <div>
        <span class="eyebrow">快速开始</span>
        <h2>三行命令，起一个自己的实例</h2>
        <div class="code-card">
          <div class="code-bar">
            <span class="code-dots"><i></i><i></i><i></i></span>
            <span class="code-lang">bash</span>
          </div>
          <pre class="code"><code>git clone ${REPO}.git &amp;&amp; cd Copree
cp .env.example .env   # 编辑 DB_PASSWORD 和 JWT_SECRET_KEY
docker compose up -d   # 访问 http://localhost:5227</code></pre>
        </div>
        <p class="muted small">
          管理员首次注册自动成为管理员。配置 API Key → 创建 AI → 建群开聊。
          Windows 也可下载 <a href="${REPO}/releases/download/v0.4.0/Copree-Installer.exe" target="_blank" rel="noopener">安装程序</a>。
        </p>
      </div>
      <div class="card note-card">
        <h3>部署前建议</h3>
        <ul class="checked small">
          <li>关闭公开注册，或开启邮箱验证</li>
          <li>在隐私政策中说明收集范围与保留期</li>
          <li>阅读 <a href="/wiki/compliance/">部署合规建议书</a></li>
          <li>为实例配置 HTTPS 与访问控制</li>
        </ul>
      </div>
    </div>
  </section>

  <section class="section" id="gallery">
    <div class="container">
      <div class="section-head"><span class="eyebrow">界面一览</span><h2>它长什么样</h2></div>
      <div class="gallery">
        ${[
          ['worlds.png', '群视界：每个世界都有自己的页面与状态'],
          ['agents.png', 'AI 管理：每个 AI 有自己的头像、人设、状态与记忆'],
          ['market.png', '世界商城：发现、安装与分享世界'],
          ['study.png', '学习模式：把对话变成可以回看的知识'],
        ].map(([f, c], i) => `<figure>
            <img src="/assets/screenshots/${f}" alt="${esc(c)}" loading="lazy" width="1440" height="900" />
            <figcaption><span class="shot-no">0${i + 1}</span>${c}</figcaption>
          </figure>`).join('')}
      </div>
    </div>
  </section>

  <section class="section" id="docs">
    <div class="container">
      <div class="section-head"><span class="eyebrow">文档</span><h2>从使用到二次开发</h2></div>
      <div class="grid cards-3">
        ${[
          ['/wiki/readme/', '项目介绍', '核心能力、快速开始、技术栈'],
          ['/wiki/user-manual/', '用户手册', 'AI、群聊、记忆、世界的完整操作'],
          ['/wiki/admin-manual/', '管理与开发者手册', '部署、架构、排错、WebSocket'],
          ['/wiki/group-world/', '群视界实现', '架构、决策与踩坑（ADR 风格）'],
          ['/wiki/code-wiki/', 'Code Wiki', '模块、页面、API 全览'],
          ['/wiki/docs-index/', '文档目录', '完整索引与阅读路线'],
        ].map(([h, t, d]) => `<a class="card doc-card" href="${h}"><h3>${t}</h3><p>${d}</p><span class="text-link">阅读 →</span></a>`).join('')}
      </div>
    </div>
  </section>

  <section class="cta-band">
    <div class="container cta-band-inner">
      <div>
        <h2>准备好和 AI 一起生活了吗？</h2>
        <p>先在线体验，或者直接自托管一个实例。</p>
      </div>
      <div class="cta-row">
        <a class="btn btn-primary" href="${DEMO}" target="_blank" rel="noopener">在线演示</a>
        <a class="btn btn-ghost" href="${REPO}" target="_blank" rel="noopener">去 GitHub</a>
      </div>
    </div>
  </section>
</main>`
  return page({
    title: 'Copree — AI 群聊与可编程世界平台',
    description: 'Copree 是开源的 AI 群聊与可编程世界平台：AI 自主社交、长期记忆、四状态机，群聊即世界，一句话造出可玩网页。',
    active: 'home',
    bodyClass: 'home',
    path: '/',
    content: body,
    extraHead: '<link rel="preload" as="image" href="/assets/screenshots/chat.png" />',
  })
}

// ── 文档页 ────────────────────────────────────────────────
function sidebar(activeSlug) {
  const groups = [...new Set(DOCS.map(d => d.group))]
  return `
<aside class="wiki-side">
  <button class="side-toggle" id="sideToggle" aria-expanded="false" aria-controls="sideInner">
    文档导航<span class="side-toggle-icon">▾</span>
  </button>
  <div class="side-inner" id="sideInner">
  <label class="search">
    <span>筛选文档</span>
    <input id="docSearch" type="search" placeholder="输入关键词…" autocomplete="off" />
  </label>
  ${groups.map(g => `
  <div class="side-group">
    <div class="side-title">${g}</div>
    ${DOCS.filter(d => d.group === g).map(d => `
      <a class="side-link${d.slug === activeSlug ? ' active' : ''}" href="/wiki/${d.slug}/"
         data-title="${esc(d.title + ' ' + d.desc)}">${esc(d.title)}</a>`).join('')}
  </div>`).join('')}
  </div>
</aside>`
}

// 正文首个 h1 与页面标题重复时去掉，避免同一页出现两个大标题
function stripDuplicateTitle(html, doc) {
  const m = html.match(/<h1[^>]*>([\s\S]*?)<\/h1>/)
  if (!m) return html
  const text = stripTags(m[1])
  const same = text === 'Copree' || text.includes(doc.title) || doc.title.includes(text)
  return same ? html.replace(m[0], '') : html
}

function docPage(doc, raw) {
  const dir = path.posix.dirname(doc.file)
  const rendered = renderMarkdown(raw, dir)
  const html = stripDuplicateTitle(rendered.html, doc)
  const toc = rendered.toc
  const idx = DOCS.indexOf(doc)
  const prev = DOCS[idx - 1], next = DOCS[idx + 1]
  const content = `
<div class="wiki-shell">
  ${sidebar(doc.slug)}
  <main class="wiki-main">
    <nav class="breadcrumb"><a href="/">首页</a><span>/</span><a href="/wiki/readme/">文档</a><span>/</span><span>${esc(doc.title)}</span></nav>
    <h1 class="wiki-title">${esc(doc.title)}</h1>
    <p class="wiki-source muted small">
      源自 <a href="${REPO}/blob/main/${doc.file}" target="_blank" rel="noopener">${doc.file}</a>
    </p>
    <article class="prose">${html}</article>
    <nav class="page-nav">
      ${prev ? `<a href="/wiki/${prev.slug}/"><span>上一页</span>${esc(prev.title)}</a>` : '<span></span>'}
      ${next ? `<a class="next" href="/wiki/${next.slug}/"><span>下一页</span>${esc(next.title)}</a>` : '<span></span>'}
    </nav>
  </main>
  <aside class="wiki-toc">
    <div class="toc-title">本页目录</div>
    <ul>${toc.filter(t => t.level === 2).map(t => `<li><a href="#${t.id}">${esc(t.text)}</a></li>`).join('')}</ul>
  </aside>
</div>`
  return page({
    title: `${doc.title} · Copree 文档`,
    description: doc.desc,
    lang: doc.lang || 'zh-CN',
    active: 'docs',
    bodyClass: 'wiki',
    path: `/wiki/${doc.slug}/`,
    content,
  })
}

// ── 构建 ──────────────────────────────────────────────────
const ASSET_URL_RE = /\/assets\/[A-Za-z0-9._@%+/-]+/g

// 站点是暗色设计，界面截图优先用 docs/assets/screenshots/dark/ 下的夜版；
// 日版继续给 GitHub README 和 Markdown 阅读器用，两套各自完整。
const SHOT_DIR = path.join(ROOT, 'docs/assets/screenshots')
const DARK_SHOTS = new Set()
;(function collectDarkShots(dir, prefix) {
  if (!fs.existsSync(dir)) return
  for (const name of fs.readdirSync(dir)) {
    const abs = path.join(dir, name)
    if (fs.statSync(abs).isDirectory()) collectDarkShots(abs, prefix + name + '/')
    else DARK_SHOTS.add(prefix + name)
  }
})(path.join(SHOT_DIR, 'dark'), '')

function useDarkShots(html) {
  return html.replace(/\/assets\/screenshots\/(?!dark\/)([^"')\s?#]+)/g, (all, rel) =>
    DARK_SHOTS.has(rel) ? `/assets/screenshots/dark/${rel}` : all)
}

// 只复制页面真正引用到的资源；位图统一转 webp 并按需缩小，避免把整个 docs/assets（5MB+）推上线
function publishAssets(htmls) {
  const used = new Set()
  for (const html of htmls) for (const m of html.match(ASSET_URL_RE) || []) used.add(m)

  const urlMap = new Map()
  for (const url of [...used].sort()) {
    const rel = url.slice('/assets/'.length)
    const src = [
      path.join(ROOT, 'docs/assets', rel),
      path.join(ROOT, 'frontend/public', rel),
      path.join(ROOT, 'frontend/public', path.basename(rel)),
    ].find(p => fs.existsSync(p) && fs.statSync(p).isFile())
    if (!src) { console.warn(`缺少静态资源：${url}`); continue }

    const isBitmap = /\.(png|jpe?g)$/i.test(rel)
    const outRel = isBitmap ? rel.replace(/\.(png|jpe?g)$/i, '.webp') : rel
    const dest = path.join(OUT, 'assets', outRel)
    fs.mkdirSync(path.dirname(dest), { recursive: true })

    if (isBitmap) {
      // 界面截图按 2400px 长边出：1440 宽的版面在 2x 屏上要 2880，压到 1600 会明显发虚
      const isShot = /^screenshots\//.test(rel)
      const maxEdge = /logo/.test(rel) ? '256x256>' : isShot ? '2400x2400>' : '1600x1600>'
      execFileSync('convert', [src, '-resize', maxEdge, '-strip', '-quality', isShot ? '88' : '82', dest])
      urlMap.set(url, `/assets/${outRel}`)
    } else {
      fs.copyFileSync(src, dest)
    }
  }
  return urlMap
}

// 只清空内容、不删掉 dist 目录本身：容器 bind mount 的是这个目录，
// 删掉再建会让容器里的挂载点指向旧 inode（表现为整站 403）。
function build() {
  fs.mkdirSync(OUT, { recursive: true })
  for (const name of fs.readdirSync(OUT)) {
    fs.rmSync(path.join(OUT, name), { recursive: true, force: true })
  }
  fs.mkdirSync(path.join(OUT, 'wiki'), { recursive: true })

  const pages = new Map([['index.html', homePage()]])
  for (const doc of DOCS) {
    const abs = path.join(ROOT, doc.file)
    if (!fs.existsSync(abs)) {
      console.warn(`缺少文档：${doc.file}`)
      continue
    }
    pages.set(`wiki/${doc.slug}/index.html`, docPage(doc, fs.readFileSync(abs, 'utf8')))
  }
  for (const [rel, html] of pages) pages.set(rel, useDarkShots(html))

  const urlMap = publishAssets(pages.values())
  for (const [rel, html] of pages) {
    let out = html
    for (const [from, to] of urlMap) out = out.split(from).join(to)
    const abs = path.join(OUT, rel)
    fs.mkdirSync(path.dirname(abs), { recursive: true })
    fs.writeFileSync(abs, out)
  }

  fs.copyFileSync(path.join(HERE, 'styles.css'), path.join(OUT, 'styles.css'))
  fs.copyFileSync(path.join(HERE, 'app.js'), path.join(OUT, 'app.js'))

  fs.writeFileSync(path.join(OUT, 'robots.txt'), 'User-agent: *\nAllow: /\n')
  fs.writeFileSync(path.join(OUT, 'sitemap.xml'), `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>${SITE_URL}/</loc></url>
${DOCS.map(d => `<url><loc>${SITE_URL}/wiki/${d.slug}/</loc></url>`).join('\n')}
</urlset>`)
  console.log(`构建完成：${OUT}（${DOCS.length} 篇文档）`)
}

build()
