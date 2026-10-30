/**
 * 文件预览里 HTML 的沙箱口径（唯一出处）
 *
 * 预览用 `<iframe sandbox>` 渲染，之前只给 `allow-scripts`。这等于给页面一个**不透明源**：
 * 脚本能跑，但 `localStorage` / `sessionStorage` 一碰就抛 SecurityError、`alert`/`confirm`
 * 不弹、表单提交被拦。后果不是"少个功能"，而是脚本在入口就抛错，后面那些点击监听根本
 * 没挂上——用户看到的就是"点不动"；同一个文件下载下来用浏览器打开（有真源）却一切正常。
 *
 * 所以这里做两件事，都不放宽沙箱：
 *   1) 在文档最前面注入一段 boot，把两个存储换成内存版（页面照原来的写法就能用）；
 *   2) 沙箱补 `allow-forms` / `allow-modals`（表单与 alert 不该被静默吞掉）。
 *
 * `allow-same-origin` **不给**：给了之后页面就能读本站的 token 与 cookie——预览的是
 * 别人上传的文件，那等于把整站交给它。
 *
 * 仍然做不到的：页面里指向本站的相对接口（`fetch('/api/...')`）在不透明源下是跨域请求，
 * 拿不到响应。那种页面只能走"下载后用浏览器打开"，这一点写在预览界面的提示里，别让它
 * 看起来像又坏了。
 */

/** 预览 iframe 的 sandbox：脚本 + 表单 + 弹窗，没有 allow-same-origin。 */
export const PREVIEW_SANDBOX = 'allow-scripts allow-forms allow-modals'

/**
 * 在不透明源里顶替 localStorage / sessionStorage。
 *
 * 只在原生实现**抛错**时装（能用的环境下不接管，避免改变语义）；同一个页面拿到的是同一个
 * 实例，所以先写后读仍然成立。数据只活在这份文档里——刷新即丢，这正是"预览"该有的边界。
 */
const BOOT = `(function () {
  function memoryStore() {
    var data = Object.create(null);
    return {
      getItem: function (key) {
        var k = String(key);
        return Object.prototype.hasOwnProperty.call(data, k) ? data[k] : null;
      },
      setItem: function (key, value) { data[String(key)] = String(value); },
      removeItem: function (key) { delete data[String(key)]; },
      clear: function () { data = Object.create(null); },
      key: function (index) {
        var keys = Object.keys(data);
        return index >= 0 && index < keys.length ? keys[index] : null;
      },
      get length() { return Object.keys(data).length; }
    };
  }
  function install(name) {
    try { window[name].getItem('__probe__'); return; } catch (error) { /* 不透明源：需要顶替 */ }
    var impl = null;
    try {
      Object.defineProperty(window, name, {
        configurable: true,
        get: function () { if (!impl) impl = memoryStore(); return impl; }
      });
    } catch (error) { /* 顶不上就让它照旧抛，至少不掩盖问题 */ }
  }
  install('localStorage');
  install('sessionStorage');
})();`

/** 把 boot 插到文档最前面：必须早于页面自己的脚本，否则它们已经抛过了。 */
export const buildPreviewDocument = (html: string): string => {
  const source = html ?? ''
  const boot = `<script>${BOOT}</script>`
  const head = /<head\b[^>]*>/i.exec(source)
  if (head) {
    const at = head.index + head[0].length
    return source.slice(0, at) + boot + source.slice(at)
  }
  const doctype = /^\uFEFF?\s*<!doctype[^>]*>/i.exec(source)
  if (doctype) {
    const at = doctype.index + doctype[0].length
    return source.slice(0, at) + boot + source.slice(at)
  }
  return boot + source
}
