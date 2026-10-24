# 02 世界UI桥 WorldUI
> 控制宿主外壳：侧边栏/悬浮图标的显隐。实现了自己的导航时用 `hideFloatingIcon` 避免两套 UI 重复；全屏不走桥，直接调浏览器 Fullscreen API（见第 6 节）。

## 1. 是什么

`window.WorldUI` 是宿主（平台外壳）暴露给世界页面的桥接对象。世界代码可通过它控制**宿主外壳**（侧边栏、悬浮图标），只影响外壳，不影响世界内部页面。

## 2. API 一览

| API | 说明 |
|-----|------|
| `WorldUI.toggleSidebar()` | 切换宿主侧边栏（覆盖式，不挤压世界画面） |
| `WorldUI.showSidebar()` | 显示宿主侧边栏 |
| `WorldUI.hideSidebar()` | 隐藏宿主侧边栏 |
| `WorldUI.hideFloatingIcon()` | 隐藏宿主悬浮图标（侧边栏开关按钮） |
| `WorldUI.showFloatingIcon()` | 显示宿主悬浮图标 |

## 3. 使用约定（重要）

- **如果你实现了自己的侧边栏/菜单/导航**：调用 `WorldUI.hideFloatingIcon()` 隐藏平台悬浮图标，避免两套 UI 重复；同时你的侧边栏必须**保留平台基础菜单**，并**适配手机版**（见 04 分区「侧边栏约定」：保留平台入口 + 窄屏默认收拢/可收拢——硬性要求）。
- **未实现自己的导航时不要调用**：让平台悬浮图标保持可用，用户靠它打开宿主菜单。
- `platform-sidebar` 积木**已内置** `hideFloatingIcon` 行为，应用后自动隐藏悬浮图标，无需手动调用。

## 4. 示例

```js
// 世界页实现了完整导航后，隐藏平台悬浮图标
if (window.WorldUI && window.WorldUI.hideFloatingIcon) {
  window.WorldUI.hideFloatingIcon();
}
```

## 5. 注意事项

- 桥对象不存在时（非沉浸环境预览），调用前先判空，避免抛错。
- 侧边栏约定是**硬性要求**：平台基础菜单（首页/聊天/世界列表/设置）可以折叠成「平台」项，但**绝不能缺失**——否则用户无法回到主应用。

## 6. 全屏

全屏不经过 WorldUI 桥，世界页面里直接调浏览器的 Fullscreen API：

```js
document.documentElement.requestFullscreen();  // 进全屏
document.exitFullscreen();                     // 退全屏
```

平台 iframe 已授权 `fullscreen`，按钮点击本身就是用户手势，浏览器不会拦。沉浸页顶栏另有一个平台的全屏按钮，用户自己也能点。棋盘/卡牌/地图/游戏这类沉浸内容建议提供全屏按钮——手机全屏后横过来才是完整画面（横屏适配见 01 分区「界面硬性要求」）。
