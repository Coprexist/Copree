# 前端界面统一规范（单一来源）

> 版本：v1.0 · 更新：2026-09-15 · 适用：`frontend/`
>
> 目标：**改一处，全站生效**；新代码不用想样式，照抄下面的模式即可（"无脑"）。
> 全站视觉只有一个来源——`index.css` 里的语义类 + `tailwind.config.js` 里的尺度令牌；
> React 组件只是它们的壳。

---

## 1. 三层结构（记住这张图就够了）

```
tailwind.config.js      尺度令牌：圆角 / 字号 / 层级        ← 数值只在这里定义
        ↓
src/index.css          语义类：.btn .icon-btn .field .card .chip   ← 视觉规则只在这里写
        ↓
src/components/ui/     React 壳：PageShell PageHeader Button IconButton
                                 Input Select Card Badge Modal Dialog EmptyState
        ↓
页面 / 业务组件         只写布局（flex / grid / gap / 宽度），不写颜色、圆角、阴影
```

**判断标准**：如果一个页面里出现了 `bg-primary-500`、`rounded-2xl`、`px-3 py-1.5` 这类
"自己拼出来的控件外观"，它就是漏网的旧代码。

---

## 2. 尺度令牌（唯一的数值来源）

| 类别 | 令牌 | 值 | 用在哪 |
|------|------|----|--------|
| 圆角 | `rounded-control` | 8px | 按钮、输入框、小卡片、列表项 |
| | `rounded-card` | 12px | 卡片 / 面板 |
| | `rounded-dialog` | 16px | 弹窗 / 抽屉 |
| | `rounded-full` | 胶囊 | 徽标、头像、圆点 |
| 字号 | `text-3xs` | 10px | 徽标、脚注 |
| | `text-2xs` | 11px | 次级说明 |
| | `text-xs` | 12px | 辅助文字（Tailwind 默认） |
| | `text-sm` | 14px | 正文（Tailwind 默认） |
| 层级 | `z-overlay` | 30 | 页面内局部遮罩、拖拽把手 |
| | `z-drawer` | 40 | 侧边抽屉、移动端导航 |
| | `z-modal` | 50 | 弹窗 |
| | `z-toast` | 60 | 全局通知、嵌套弹窗、维护横幅 |
| | `z-max` | 100 | 阻断式提示（余额不足等） |

改全局观感 = 改这里一处。**不要再写 `rounded-xl` / `text-[10px]` / `z-[65]` 这种裸数值。**

---

## 3. 语义类（写标签时直接用）

### 按钮

```html
<!-- 实心主按钮 / 描边次级 / 危险 / 无底色 -->
<button class="btn btn-md btn-primary">保存</button>
<button class="btn btn-sm btn-outline">取消</button>
<button class="btn btn-md btn-danger">删除</button>
<button class="btn btn-xs btn-ghost">更多</button>
```

- 尺寸：`btn-xs`=24px · `btn-sm`=32px · `btn-md`=40px · `btn-lg`=48px（**固定高度，同行严格等高**）
- 变体：`btn-primary` / `btn-accent` / `btn-secondary` / `btn-outline` / `btn-ghost` / `btn-danger`
- 布局类只加在需要的地方：`flex-1`、`w-full`、`shrink-0`、`mt-3`

### 图标按钮

```html
<button class="icon-btn icon-btn-sm" aria-label="返回">…</button>
```

尺寸：`icon-btn-sm`=28px · `icon-btn`=32px · `icon-btn-lg`=40px。
**图标按钮必须有 `aria-label`**（同时作为悬浮提示）。React 里用 `<IconButton label="…" icon={…} />`。

### 表单控件

```html
<input class="field" placeholder="用户名" />
<select class="field">…</select>
<textarea class="field" rows="3"></textarea>
<input class="field field-sm" />   <!-- 32px 紧凑版 -->
```

### 卡片 / 徽标

```html
<section class="card card-pad">…</section>
<section class="card card-pad-lg">…</section>
<span class="chip chip-mint shrink-0">在线</span>
```

徽标色：`chip-primary` 紫 · `chip-mint` 绿 · `chip-accent` 琥珀 · `chip-rose` 危险 · `chip-muted` 中性。

---

## 4. 组件（写页面时优先用）

```tsx
import { PageShell, Button, IconButton, Input, Select, Card, Badge, Modal, Dialog, EmptyState, ListPanel, ExpandPanel, confirmAsync } from '../components/ui'

// 页面骨架：根容器 + 标题栏 + 滚动 + 居中等宽，一次搞定
<PageShell
  title="群视界"
  subtitle="给群聊一个可编程的世界"
  onBack={() => navigate(-1)}
  width="content"                      // narrow | content | wide | full
  contentClassName="space-y-5"
  actions={<Button size="sm" icon={<Plus size={14} />} onClick={create}>创建</Button>}
>
  …
</PageShell>

// 按钮 / 图标按钮
<Button variant="primary" size="sm" loading={saving} onClick={save}>保存</Button>
<IconButton icon={<X size={16} />} label="关闭" onClick={close} />

// 弹窗：带标准卡片用 Modal，只有遮罩+自定义卡片用 Dialog
<Modal open={open} onClose={close} title="编辑资料" size="md" footer={<Button onClick={save}>保存</Button>}>…</Modal>
<Dialog onClose={close} className="flex items-center justify-center p-4">…</Dialog>

// 空状态 / 徽标 / 确认框
<EmptyState icon={Inbox} title="暂无内容" description="…" />
<Badge tone="mint">在线</Badge>
if (await confirmAsync({ title: '删除？', message: '不可恢复', danger: true })) …

// 「紧凑列表 + 详情按需出现」= 管理页的统一模式（插件页 / 命令白名单 / 技能背包）
<ListPanel
  title="命令白名单"
  columns={[{ key: 'pattern', label: '命令' }, { key: 'status', label: '状态' }]}
  toolbar={<><Input fieldSize="sm" value={q} onChange={…} /><Button size="sm">重扫</Button></>}
  empty={rows.length === 0 ? <EmptyState icon={Inbox} title="暂无内容" /> : undefined}
>
  <tr className="border-b border-border/50">
    <td className="py-2 px-3">…</td>
    <td className="py-2 px-3">…</td>
  </tr>
</ListPanel>

// 展开的那块详情：标题栏与「收起」由它统一给，内容自己写
<ExpandPanel title="QQ 通道" collapseLabel={t('tool:ui.collapse')} onCollapse={close}>
  …表单 / 实例列表…
</ExpandPanel>
```

### 宽度档位（页面留白唯一来源）

| 档位 | 宽度 | 适合 |
|------|------|------|
| `narrow` | `max-w-xl` | 表单、发布页 |
| `content` | `max-w-3xl` | 单列内容（我的、用量、群视界列表） |
| `wide` | `max-w-4xl` | 卡片网格、表格（AI、商城、后台） |
| `full` | 不限 | 双栏页面（聊天、好友、设置、后台） |

内容内边距由 PageShell 统一给（`px-4 py-4 md:px-6 md:py-6`，移动端自动留导航栏高度）。

---

## 5. 常见模式（照抄）

| 场景 | 写法 |
|------|------|
| 页面 | `<PageShell title=… width=…>`，不要在页面里自己写 `h-full flex flex-col bg-canvas` |
| 页面顶端 | 用 PageHeader（PageShell 已含），不要手写 `h-14 border-b` 的 div |
| 弹窗 | Modal / Dialog，不要手写 `fixed inset-0` 遮罩（ESC、锁滚动、点遮罩关闭由 Dialog 统一给） |
| 按钮 | `<Button>` 或 `.btn .btn-md .btn-* ` |
| 控制台（/admin） | 进 /admin 收起应用侧边栏（Layout 按路径判断），宽度全给管理界面；控制台自带可折叠导航栏（收起=只剩图标，`localStorage` 记状态）与底部「返回应用」出口——侧边栏收起了就必须在这里留出口 |
| 管理面板导航 | 分组/顺序/图标/文案/组件**只写在 `pages/admin/tabs.tsx`**；分组按"管理对象"分桶（通用 / 用户与内容 / 能力与扩展 / 监控与日志 / 数据与维护），每桶 2~6 项、桶名要能盖住桶内所有条目。页面里不许再写第二份标签表（桌面端与移动端会漂移） |
| 图标按钮 | `<IconButton>` 或 `.icon-btn` |
| 输入框 | `<Input>` / `<Select>` 或 `.field` |
| 卡片 | `.card .card-pad` 或 `<Card>` |
| 徽标 | `<Badge tone=…>` 或 `.chip .chip-* ` |
| 空状态 | `<EmptyState>`，不要自己拼居中提示 |
| 管理页列表 | `<ListPanel>`（表格壳 + 标题 + 工具条 + 空态）；行用 `border-b border-border/50`，单元格 `py-2 px-3` |
| 实体列表（插件/AI/应用） | **两行式**：上排「名称 + 类型 + 版本」，下排**一行截断的说明**（`truncate` + `title` 悬停看全文）。**完整说明与元信息（作者/来源）住详情，不住列表** —— 列表负责"认出它"，详情负责"讲清楚它" |
| 列表里的状态 | **圆点 + 文案**（颜色即语义），别只给彩色文字 |
| 列表里的启停 | **开关**（`<Toggle size="sm">`），不要用会变字的文字链接：位置固定、状态靠形态表达 |
| 列表里的详情 | **只读内容**用 `<ExpandPanel>` 就地展开（技能背包）；**带表单/凭据的配置**用右侧抽屉（`<Dialog layer="drawer" className="flex justify-end">` + `h-full w-full max-w-xl bg-surface border-l shadow-2xl overflow-y-auto`，见 `PluginDetailPanel`）——**先列表给状态与动作，详情按需展开** |
| 为什么不用弹窗/悬窗放配置 | 悬窗（popover）鼠标一移开就没了，装不下表单；弹窗遮全屏，看不清"我在改列表里的哪一个"。抽屉两头都避开：列表留在原地当上下文 |
| 确认框 | `confirmAsync()`，不要用 `window.confirm` |
| 危险色 | `rose`；成功/在线 `mint`；强调/通知 `accent`；品牌主色 `primary`（紫） |
| 主题色 | 一律走 `rgb(var(--tw-*))` 对应的 Tailwind 名（`bg-surface` / `text-textSecondary`…），
不要写死 `#fff` / `bg-gray-100`（深色主题会瞎） |

---

## 6. 新增一个视觉变体怎么做

1. 先想清楚它是不是已有变体的组合——八成是。
2. 需要新变体时，**只改 `index.css` 的 `@layer components`**（例如加 `.btn-tinted`），
   再在 `components/ui/Button.tsx` 的 `VARIANT_CLASS` 里登记名字。
3. 不要在页面里就地拼一套新外观；那样下一个人又得改一遍全站。

## 7. 自检（提交前跑一遍）

```bash
# 前端在容器里跑，容器内不装依赖
docker exec -w /app ai_group_frontend node_modules/.bin/tsc --noEmit
docker exec -w /app ai_group_frontend node scripts/check-i18n.mjs

# 别让旧的裸写法回流（数字应逐步下降，不应上升）
cd frontend/src
grep -rho "rounded-\(lg\|xl\|2xl\|md\)\b" --include=*.tsx . | wc -l
grep -rho "z-\[[0-9]*\]\|z-50\b" --include=*.tsx . | wc -l
grep -rho "text-\[1[0-9]px\]" --include=*.tsx . | wc -l
```

界面改完还要**截图对比**（`node scripts/screenshot/run.mjs`），别只靠类型检查。
