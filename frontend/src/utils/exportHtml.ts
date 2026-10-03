/**
 * 把页面上已经渲染好的 DOM 导成一个自包含的 HTML 文件。
 *
 * 为什么在前端导、而不是像 json / md 那样在后端拼：日志正文里是 markdown、公式、代码高亮、
 * 工具调用块，只有前端渲染器认得（后端没有这套渲染，拼出来打开是一堆星号和反引号）。
 * 样式也从当前页面收集，导出件因此跟界面同一份来源，不必另写一套 CSS 跟着改。
 */
import { saveText } from './download'

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

/** 收集当前页面的样式规则：同源样式表读得到，跨域的（外链字体）读不到就跳过 */
function collectCss(): string {
  const parts: string[] = []
  for (const sheet of Array.from(document.styleSheets)) {
    let rules: CSSRuleList | null = null
    try { rules = sheet.cssRules } catch { continue }
    if (!rules) continue
    for (const rule of Array.from(rules)) {
      // @import 在导出件里按文件所在目录解析，带过去只会 404
      if (rule.type === CSSRule.IMPORT_RULE) continue
      parts.push(rule.cssText)
    }
  }
  return parts.join('\n')
}

/**
 * 站内的折叠与视图切换是 React 状态，导出件里只剩 DOM：补一段脚本按属性把开关接回来。
 * 动画与显示规则都在页面样式表里（.collapse-body / .switch），这里不另写一套。
 */
const EXPORT_SCRIPT = [
  '<script>',
  'document.querySelectorAll("[data-collapsible]").forEach(function (button) {',
  '  button.addEventListener("click", function () {',
  '    var body = document.getElementById(button.getAttribute("data-collapsible"))',
  '    if (!body) return',
  '    var open = button.getAttribute("aria-expanded") === "true"',
  '    button.setAttribute("aria-expanded", open ? "false" : "true")',
  '    body.setAttribute("data-open", open ? "false" : "true")',
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
  '    group.setAttribute("data-value", next)',
  '    button.setAttribute("data-value", next)',
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
  // 页面上这块靠布局限高滚动（max-h-[70vh] overflow-y-auto），导出件要自然展开
  clone.className = 'exported-log-body'
  absolutizeUrls(clone)
  const root = document.documentElement
  const html = [
    '<!DOCTYPE html>',
    '<html lang="' + (root.lang || 'zh-CN') + '" class="' + root.className + '">',
    '<head>',
    '<meta charset="utf-8">',
    '<meta name="viewport" content="width=device-width, initial-scale=1">',
    '<title>' + escapeHtml(title) + '</title>',
    '<style>',
    collectCss(),
    '</style>',
    '<style>',
    '/* 导出件自用：页面里这块的限高与滚动由布局给，这里换成整页留白 */',
    '.exported-log-body{max-width:56rem;margin:0 auto;padding:2rem 1rem}',
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
