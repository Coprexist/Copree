/**
 * 把页面上已经渲染好的 DOM 导成一个自包含的 HTML 文件。
 *
 * 为什么在前端导、而不是像 json / md 那样在后端拼：日志正文里是 markdown、公式、代码高亮、
 * 工具调用块，只有前端渲染器认得（后端没有这套渲染，拼出来打开是一堆星号和反引号）。
 * 样式也从当前页面收集，导出件因此跟界面同一份来源，不必另写一套 CSS 跟着改。
 */
import { saveText } from './download'
import { FOLD_TRANSITION } from '../components/shared/collapseMotion'

/** 相对地址在导出文件里会按文件所在目录解析，统一换成绝对地址（图片、附件、字体） */
function absolutizeUrls(root: HTMLElement): void {
  const abs = (value: string) => {
    try { return new URL(value, document.baseURI).href } catch { return value }
  }
  root.querySelectorAll('[src], [href]').forEach(node => {
    for (const attr of ['src', 'href']) {
      const value = node.getAttribute(attr)
      if (value && !/^(https?:|data:|blob:|mailto:|#)/.test(value)) node.setAttribute(attr, abs(value))
    }
  })
  root.querySelectorAll('[srcset]').forEach(node => {
    const value = node.getAttribute('srcset') || ''
    node.setAttribute('srcset', value.split(',').map(part => {
      const [url, ...rest] = part.trim().split(/\s+/)
      return [abs(url), ...rest].join(' ')
    }).join(', '))
  })
}

/**
 * 伪类、伪元素剥掉之后再看命中：规则本身要留着（:hover 的样式在导出件里照样有用），
 * 只是不能拿带伪类的选择器去 querySelector。
 */
const PSEUDO = /::?(hover|focus|focus-visible|focus-within|active|disabled|checked|placeholder-shown|placeholder|before|after|visited|selection|marker|file|backdrop)\b(\([^)]*\))?/g

/** 以根元素开头的选择器（.dark .prose 这种）在子树里永远判不出命中，直接留 */
const ROOTED = /^\s*(html|body|:root|\.dark)[\s>+~]/

/** 判断不了就留：丢样式比多带几 KB 难受得多 */
function selectorHits(rawSelector: string, scope: HTMLElement): boolean {
  const selector = rawSelector
    // 状态伪类（:hover 这类）代表交互后才有的样子，判断命中时先剥掉
    .replace(PSEUDO, '')
    // 状态属性（data-open / data-value / aria-*）同理：收起的块、没显示的那份视图在当前
    // DOM 里根本不存在，不剥掉就会把「切换之后才生效」的规则整条丢掉（折叠动画就是这么丢的）
    .replace(/\[[^\]]*\]/g, '')
    .replace(/[>+~]\s*$/, '')
    .trim()
  if (!selector || ROOTED.test(selector)) return true
  try {
    if (scope.querySelector(selector)) return true
    // body / html / :root 上的基础规则不在导出内容的子树里，得单独看一眼
    return document.body.matches(selector) || document.documentElement.matches(selector)
  } catch {
    return true
  }
}

/** 逗号分组里有一个命中就整条保留 */
function ruleHits(selectorText: string, scope: HTMLElement): boolean {
  return selectorText.split(',').some(selector => selectorHits(selector, scope))
}

/**
 * 收集当前页面的样式规则，只留导出内容用得上的那些。
 * 全量抄一遍是文件里最大的一笔（整站样式表一两百 KB，日志正文用到的只是其中一小撮）；
 * 判不出来的一律保留，宁可多带也不能丢样式。
 */
function collectCss(scope: HTMLElement): string {
  const kept = new Set<string>()
  let total = 0
  const walk = (rules: CSSRuleList): void => {
    for (const rule of Array.from(rules)) {
      // @import 在导出件里按文件所在目录解析，带过去只会 404
      if (rule.type === CSSRule.IMPORT_RULE) continue
      total++
      if (rule.type === CSSRule.STYLE_RULE) {
        if (ruleHits((rule as CSSStyleRule).selectorText, scope)) kept.add(rule.cssText)
        continue
      }
      // 媒体/支持查询：只留命中的那几条，整块搬会带上没命中的
      if (rule.type === CSSRule.MEDIA_RULE || rule.type === CSSRule.SUPPORTS_RULE) {
        const hits = Array.from((rule as CSSMediaRule).cssRules)
          .filter(one => one.type === CSSRule.STYLE_RULE && ruleHits((one as CSSStyleRule).selectorText, scope))
          .map(one => '  ' + one.cssText)
        if (hits.length) {
          const at = rule.type === CSSRule.MEDIA_RULE ? '@media ' : '@supports '
          kept.add(at + (rule as CSSMediaRule).conditionText + ' {\n' + hits.join('\n') + '\n}')
        }
        continue
      }
      // @keyframes / @font-face 没法按选择器判断，体积也小，整条留
      kept.add(rule.cssText)
    }
  }
  for (const sheet of Array.from(document.styleSheets)) {
    let rules: CSSRuleList | null = null
    try { rules = sheet.cssRules } catch { continue }
    if (rules) walk(rules)
  }
  const css = Array.from(kept).join('\n')
  console.log('[export] 样式规则 ' + total + ' 条，保留 ' + kept.size + ' 条 / ' + css.length + ' 字符')
  return css
}

/**
 * 站内的折叠与视图切换是 React 状态，导出件里只剩 DOM：补一段脚本按属性把开关接回来。
 * 动画与显示规则都在页面样式表里（.collapse-body / .switch），这里不另写一套。
 */
const EXPORT_SCRIPT = [
  '<script>',
  '/* 文件里只存一份紧凑 JSON：「原始 JSON」和每条的「原文」都在打开时现算 */',
  'var dataEl = document.querySelector("script[data-log-json]")',
  'if (dataEl) {',
  '  try {',
  '    var msgs = JSON.parse(dataEl.textContent || "[]")',
  '    document.querySelectorAll("[data-lazy-json]").forEach(function (pre) {',
  '      var key = pre.getAttribute("data-lazy-json")',
  '      var value = key === "raw" ? msgs : msgs[Number(key.slice(4))]',
  '      pre.textContent = JSON.stringify(value, null, 2) || ""',
  '    })',
  '  } catch (e) { /* 数据坏了就当没有，折叠与切换不受影响 */ }',
  '}',
  '/* 顶部两行都粘住：整页滚动时它们在同一个滚动上下文里，图例要让开按钮行的高度 */',
  'var stickHead = document.querySelector("[data-export-stick-head]")',
  'if (stickHead) {',
  '  var syncGap = function () {',
  '    document.documentElement.style.setProperty("--export-head-h", stickHead.offsetHeight + "px")',
  '  }',
  '  syncGap()',
  '  window.addEventListener("resize", syncGap)',
  '}',
  '/* 高度变化统一走这一条：折叠与切视图都调它，不各写一套；',
  '   收起的目标是 0（量不出来），所以由调用方说清楚变化后的状态 */',
  'function transitionHeight(el, collapsed, mutate) {',
  '  var from = el.offsetHeight',
  '  /* 展开时先松开「收到底」的标记，否则网格还停在 0 行高、量出来的目标高度是 0 */',
  '  if (!collapsed) el.removeAttribute("data-collapsed")',
  '  mutate()',
  '  var to = collapsed ? 0 : el.offsetHeight',
  '  if (from === to) return',
  '  el.style.overflow = "hidden"',
  '  el.style.height = from + "px"',
  '  void el.offsetHeight',
  '  el.style.transition = ' + JSON.stringify(FOLD_TRANSITION),
  '  el.style.height = to + "px"',
  '  /* 等过渡真的走完再收尾：用定时器猜时间，猜早了会在动画没结束时就交还高度、看起来弹一下 */',
  '  var finish = function () {',
  '    el.removeEventListener("transitionend", onEnd)',
  '    clearTimeout(fallback)',
  '    /* 先落「收到底」的标记再交还高度：反过来的话网格会先弹回一行、再跳回 0 */',
  '    if (collapsed) el.setAttribute("data-collapsed", "true")',
  '    el.style.transition = ""',
  '    el.style.height = ""',
  '    /* overflow 留着不放：它建了 BFC，交还自动高度时才不会因外边距塌陷变一下 */',
  '  }',
  '  var onEnd = function (event) { if (event.propertyName === "height") finish() }',
  '  el.addEventListener("transitionend", onEnd)',
  '  var fallback = setTimeout(finish, 600)',
  '}',
  'document.querySelectorAll("[data-collapsible]").forEach(function (button) {',
  '  button.addEventListener("click", function () {',
  '    var body = document.getElementById(button.getAttribute("data-collapsible"))',
  '    if (!body) return',
  '    var open = button.getAttribute("aria-expanded") === "true"',
  '    transitionHeight(body, open, function () {',
  '      button.setAttribute("aria-expanded", open ? "false" : "true")',
  '      body.setAttribute("data-open", open ? "false" : "true")',
  '    })',
  '  })',
  '})',
  '/* 一组视图（原文/渲染、分段/原始 JSON）之间轮换：值取自组内的 data-case，按钮文案跟着同步 */',
  'document.querySelectorAll("[data-switch]").forEach(function (button) {',
  '  button.addEventListener("click", function () {',
  '    var group = document.getElementById(button.getAttribute("data-switch"))',
  '    if (!group) return',
  '    var cases = group.querySelectorAll(":scope > [data-case]")',
  '    if (!cases.length) return',
  '    var values = Array.prototype.map.call(cases, function (el) { return el.getAttribute("data-case") })',
  '    var next = values[(values.indexOf(group.getAttribute("data-value")) + 1) % values.length]',
  '    /* 换视图也会改高度（两份内容不一样高），照样交给同一条高度过渡 */',
  '    transitionHeight(group, false, function () {',
  '      group.setAttribute("data-value", next)',
  '      button.setAttribute("data-value", next)',
  '      /* 页面上还有跟着这组状态走的元素（比如图例），一并同步 */',
  '      document.querySelectorAll("[data-switch-mirror]").forEach(function (el) {',
  '        if (el.getAttribute("data-switch-mirror") === group.id) el.setAttribute("data-value", next)',
  '      })',
  '    })',
  '  })',
  '})',

  '</script>',
].join('\n')

const HTML_ESCAPES: Record<string, string> = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }
const escapeHtml = (text: string) => text.replace(/[&<>"]/g, c => HTML_ESCAPES[c])

/**
 * el 传页面上那块已经渲染好的正文，filename 是落盘名，title 写进 <title>。
 * 主题、字体、颜色跟着当前页面一起带走（连 html/body 的 class 都复制），
 * 所以在深色主题下导出的文件也是深色的。
 */
export function saveElementAsHtml(el: HTMLElement, filename: string, title: string): void {
  const clone = el.cloneNode(true) as HTMLElement
  // 导出件是一整页，不再受页面里那块的高度限制
  clone.className = 'exported-log-body'
  // 站内专有的按钮（下载 JSON / Markdown / HTML）在导出件里点了也没用，整块摘掉
  clone.querySelectorAll('[data-export-skip]').forEach(node => node.remove())
  // 页面里正文那层自己限高滚动（两栏各自滑），导出件是一整页：这一层要平铺开，
  // 否则整段内容被切在 70vh 里还多一条滚动条（块内代码/JSON 的小滚动条保留，跟站内一致）
  clone.querySelectorAll('[data-export-flat]').forEach(node => { node.className = '' })
  // 页面里已经折叠着的块，导出件打开时也该是收起的（0 行高现在只认这个标记）
  clone.querySelectorAll('.collapse-body[data-open="false"]').forEach(node => node.setAttribute('data-collapsed', 'true'))
  absolutizeUrls(clone)

  // 瘦身：每条的「原文」和整段「原始 JSON」不在文件里存第二遍——清空，打开时按紧凑数据现算
  // （见 EXPORT_SCRIPT 的填充段），省下的正是同一份 JSON 的第二遍
  let lazyBytes = 0
  clone.querySelectorAll('[data-lazy-json]').forEach(node => {
    lazyBytes += (node.textContent || '').length
    node.textContent = ''
  })
  if (lazyBytes) console.log('[export] 懒生成 JSON 少写 ' + lazyBytes + ' 字符')

  const root = document.documentElement
  const html = [
    '<!DOCTYPE html>',
    '<html lang="' + (root.lang || 'zh-CN') + '" class="' + root.className + '">',
    '<head>',
    '<meta charset="utf-8">',
    '<meta name="viewport" content="width=device-width, initial-scale=1">',
    '<title>' + escapeHtml(title) + '</title>',
    '<style>',
    collectCss(el),
    '</style>',
    '<style>',
    '/* 导出件自用：页面里这块的限高与滚动由布局给，这里换成整页留白 */',
    '/* 页面里日志正文是白卡，导出件也铺白的，粘住的那两行才不会跟底色打架 */',
    '.exported-log-body{max-width:56rem;margin:0 auto;padding:2rem 1rem;background:rgb(var(--tw-surface))}',
    '/* 导出件是整页滚动，按钮行与图例在同一层：图例的 top 让开按钮行的高度（打开时量一次） */',
    '.exported-log-body [data-export-stick-legend]{top:var(--export-head-h,0)}',
    '</style>',
    '</head>',
    '<body class="' + document.body.className + '">',
    clone.outerHTML,
    EXPORT_SCRIPT,
    '</body>',
    '</html>',
  ].join('\n')
  saveText(html, filename, 'text/html;charset=utf-8')
}
