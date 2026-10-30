/**
 * 上游依赖的 Safari 兼容补丁（postinstall 自动执行，幂等）。
 *
 * 问题：mdast-util-gfm-autolink-literal@2.0.1（npm 上的 latest，上游至今没修）用了一句
 * **lookbehind 正则**匹配纯文本里的邮箱：
 *
 *     /(?<=^|\s|\p{P}|\p{S})([-.\w+]+)@([-\w]+(?:\.[-\w]+)+)/gu
 *
 * Safari / WebKit 16.4（2023-03）之前的版本不支持 lookbehind，正则字面量直接抛
 * `SyntaxError: Invalid regular expression: invalid group specifier name`。
 * 这个转换 remark-gfm 每次渲染 markdown 都会跑，于是**旧 iPad/iPhone 上打开任何含文本的
 * 消息都会整页报错**（React 错误边界接管），桌面 Chrome 完全正常——很容易被误判成设备问题。
 *
 * 修法：去掉那段 lookbehind。它本来就是**冗余**的——同一文件的 findEmail() 会用
 * `previous(match, true)` 逐字符再校验一遍"前一个字符必须是空白/标点、且不能是斜杠"，
 * 判据与 lookbehind 相同，去掉后输出一致。
 *
 * 为什么用 postinstall 而不是 pnpm patchedDependencies：发布流水线用的是 `npm ci`
 * （见发布仓 build.yml），npm 没有补丁机制；postinstall 对 npm / pnpm 都生效。
 */
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const FRONTEND_DIR = dirname(dirname(fileURLToPath(import.meta.url)));
const TARGET = join(FRONTEND_DIR, "node_modules/mdast-util-gfm-autolink-literal/lib/index.js");
const LOOKBEHIND = "(?<=^|\\s|\\p{P}|\\p{S})";

if (!existsSync(TARGET)) {
  console.log("[patch-mdast-autolink] 未安装 mdast-util-gfm-autolink-literal，跳过");
  process.exit(0);
}

const source = readFileSync(TARGET, "utf8");

if (!source.includes(LOOKBEHIND)) {
  // 上游哪天自己修了，这里就什么都不做——"没东西可改"不该让安装失败
  console.log("[patch-mdast-autolink] 无需修补（已打补丁或上游已修）");
  process.exit(0);
}

const patched = source.split(LOOKBEHIND).join("");

if (patched.includes("(?<=") || patched.includes("(?<!")) {
  console.error("[patch-mdast-autolink] 修补后文件里仍有 lookbehind，可能改错位置，放弃写入");
  process.exit(1);
}

writeFileSync(TARGET, patched);
console.log("[patch-mdast-autolink] 已去掉邮箱自动链接的 lookbehind（兼容 Safari < 16.4），" + (source.length - patched.length) + " 字节");