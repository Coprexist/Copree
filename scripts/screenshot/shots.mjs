/**
 * 截图清单 —— 顺序即 README / 文档里的阅读顺序。
 *
 * 每条：
 *   name     输出文件名（不含扩展名）
 *   path     站内路由
 *   settle   等页面稳定（毫秒）
 *   prepare  截图前的页面操作（点击文件树、滚到底、隐藏调试信息）
 */

export const VIEWPORT = { width: 1440, height: 900 }

/** 点击文件树/列表里文本完全匹配的叶子节点 */
const clickText = (text) => '(() => {' +
  'const hits = [...document.querySelectorAll("button, li, a, span, div")]' +
  '.filter(e => e.textContent.trim() === ' + JSON.stringify(text) + ');' +
  'const el = hits[hits.length - 1];' +
  'if (!el) return false;' +
  '(el.closest("button") || el).click(); return true })()'

/** 把所有可滚动容器拉到底（聊天记录默认停在最新一条） */
const scrollBottom = '(() => {' +
  'const els = [...document.querySelectorAll("*")].filter(e => e.scrollHeight > e.clientHeight + 40);' +
  'els.forEach(e => { e.scrollTop = e.scrollHeight }); return els.length })()'

/** 世界页面会带上内部调试标记，截图里不出现 */
const hideWorldDebug = '(() => {' +
  'const kill = (doc) => { doc.querySelectorAll("*").forEach(el => {' +
  'if (el.children.length === 0 && /^WORLD_ID/.test((el.textContent || "").trim())) el.style.visibility = "hidden" }) };' +
  'kill(document); document.querySelectorAll("iframe").forEach(f => { try { kill(f.contentDocument) } catch (e) {} });' +
  'return true })()'

export const SHOTS = [
  { name: 'chat', path: '/chat/gm/1', settle: 4500, prepare: [clickText('在此标准界面打开'), scrollBottom] },
  { name: 'worlds', path: '/worlds', settle: 4500 },
  { name: 'design', path: '/worlds/34/design', settle: 6000, prepare: [clickText('main.py'), scrollBottom] },
  { name: 'world', path: '/world-view/34', settle: 6500, prepare: hideWorldDebug, format: 'jpeg', quality: 86 },
  { name: 'market', path: '/market', settle: 5000 },
  { name: 'agents', path: '/agents', settle: 4500 },
  { name: 'study', path: '/study', settle: 4000 },
  { name: 'me', path: '/me', settle: 4500 },
];

/* ── 组件规范配图 ────────────────────────────────────────────────
   配 docs/dev/ui_system.md，单独放 docs/assets/screenshots/ui/（out 字段）。
   只跑这几张不会动 README 那 8 张：

     node scripts/screenshot/run.mjs --only ui-components,ui-convlog
   ──────────────────────────────────────────────────────────────── */

const ICON_PLUS = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>'
const ICON_X = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>'
const ICON_DOTS = '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><circle cx="5" cy="12" r="1.7"/><circle cx="12" cy="12" r="1.7"/><circle cx="19" cy="12" r="1.7"/></svg>'

/**
 * 控件速览：用**真实的 index.css 语义类**当场渲染一屏（不是某个业务页面）。
 * 覆盖 docs/dev/ui_system.md 第 3 节里所有语义类与状态，CSS 一改这张图就跟着变。
 */
const UI_GALLERY =
  '<div id="ui-gallery-box" style="width:900px;padding-bottom:8px" class="space-y-6">' +
    '<div class="space-y-2">' +
      '<div class="text-xs text-textMuted">按钮 · 四档尺寸（固定高度 24 / 32 / 40 / 48）</div>' +
      '<div class="flex items-center gap-3">' +
        '<button class="btn btn-xs btn-outline">btn-xs</button>' +
        '<button class="btn btn-sm btn-outline">btn-sm</button>' +
        '<button class="btn btn-md btn-outline">btn-md</button>' +
        '<button class="btn btn-lg btn-outline">btn-lg</button>' +
      '</div>' +
    '</div>' +
    '<div class="space-y-2">' +
      '<div class="text-xs text-textMuted">按钮 · 变体与状态（is-active ＝ 开关/分段控件的选中态）</div>' +
      '<div class="flex flex-wrap items-center gap-3">' +
        '<button class="btn btn-sm btn-primary">btn-primary</button>' +
        '<button class="btn btn-sm btn-secondary">btn-secondary</button>' +
        '<button class="btn btn-sm btn-outline">btn-outline</button>' +
        '<button class="btn btn-sm btn-outline is-active">btn-outline.is-active</button>' +
        '<button class="btn btn-sm btn-ghost">btn-ghost</button>' +
        '<button class="btn btn-sm btn-danger">btn-danger</button>' +
        '<button class="btn btn-sm btn-outline-danger">btn-outline-danger</button>' +
        '<button class="btn btn-sm btn-outline" disabled>disabled</button>' +
      '</div>' +
    '</div>' +
    '<div class="space-y-2">' +
      '<div class="text-xs text-textMuted">图标按钮 · 表单控件</div>' +
      '<div class="flex flex-wrap items-center gap-3">' +
        '<button class="icon-btn icon-btn-sm">' + ICON_PLUS + '</button>' +
        '<button class="icon-btn">' + ICON_DOTS + '</button>' +
        '<button class="icon-btn icon-btn-lg">' + ICON_X + '</button>' +
        '<input class="field" style="width:200px" placeholder="field（40px）" />' +
        '<input class="field field-sm" style="width:160px" placeholder="field-sm（32px）" />' +
        '<input class="field" style="width:150px" value="禁用" disabled />' +
      '</div>' +
    '</div>' +
    '<div class="space-y-2">' +
      '<div class="text-xs text-textMuted">徽标 · chip 家族</div>' +
      '<div class="flex flex-wrap items-center gap-2">' +
        '<span class="chip chip-primary">chip-primary</span>' +
        '<span class="chip chip-mint">chip-mint</span>' +
        '<span class="chip chip-accent">chip-accent</span>' +
        '<span class="chip chip-rose">chip-rose</span>' +
        '<span class="chip chip-muted">chip-muted</span>' +
      '</div>' +
    '</div>' +
    '<div class="space-y-2">' +
      '<div class="text-xs text-textMuted">滑杆 · 左侧填充到滑钮（--slider-pct，颜色 --slider-c）：单按按位移改值，双击才跳到该处</div>' +
      '<div class="card card-pad-lg space-y-4">' +
        '<div>' +
          '<div class="flex justify-between mb-1"><label class="text-xs text-textSecondary">上下文压缩阈值</label>' +
          '<span class="text-xs font-mono text-textPrimary">60%</span></div>' +
          '<input type="range" min="5" max="100" step="5" value="55" style="--slider-pct:57.9%" />' +
        '</div>' +
        '<div>' +
          '<div class="flex justify-between mb-1"><label class="text-xs text-textSecondary">金色档（tone="accent"）</label>' +
          '<span class="text-xs font-mono text-textPrimary">35</span></div>' +
          '<input type="range" min="0" max="100" value="35" style="--slider-c:var(--tw-accent-400);--slider-pct:35%" />' +
        '</div>' +
      '</div>' +
    '</div>' +
    '<div class="space-y-2">' +
      '<div class="text-xs text-textMuted">卡片 · 可选卡片（.card-interactive，白底 → hover 落灰 → 选中紫）</div>' +
      '<div class="grid grid-cols-2 gap-3">' +
        '<div class="card card-interactive p-4 text-sm text-textSecondary">未选：白底 + 描边</div>' +
        '<div class="card card-interactive is-active p-4 text-sm text-primary-400">选中：is-active</div>' +
      '</div>' +
    '</div>' +
  '</div>'

/** 把速览叠在页面上（不动业务页面本身，React 仍在底下跑） */
const showGallery = '(() => {' +
  'const host = document.createElement("div");' +
  'host.id = "ui-gallery";' +
  'host.className = "bg-canvas text-textPrimary";' +
  'host.setAttribute("style", "position:fixed;inset:0;z-index:9999;overflow:auto;padding:32px");' +
  'host.innerHTML = ' + JSON.stringify(UI_GALLERY) + ';' +
  'document.body.appendChild(host); return true })()'

export const UI_SHOTS = [
  { name: 'ui-components', path: '/market', settle: 3500, prepare: showGallery, clip: '#ui-gallery-box', out: 'ui' },
  { name: 'ui-convlog', path: '/admin?tab=convlog', settle: 5000, clip: '.mx-auto.w-full', out: 'ui', rawApi: true },
];
