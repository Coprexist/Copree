# 表情

内置表情包。每个表情是一张图 + 一个中文名字 + 一个全局短码，站内按名字或短码都能认，
发出去是图，不受本机 emoji 字体影响。

## 目录

- `plugin.json`：插件声明（`category: emojipack`）
- `emoji.json`：表情清单（`id` / `name` / `file`）
- `assets/<id>.png`：表情图，128×128 透明底 PNG

## 显示尺寸（可选）

输入框的表情面板按包分页显示，每张图可以挑一档大小：

- `default_size`：整包默认档，`s` / `m` / `l`；没写就是 `m`
- `size`：单张覆盖，同上

档位只影响**输入框面板里挑图时**的显示（`s` 20px、`m` 56px、`l` 84px）；消息里发出去的表情尺寸不变。
本包是一组线条简洁的圆脸，按最小档（`s`）就够辨认，所以声明了 `"default_size": "s"`；
细致的插画类表情（如大肥鱼）用默认的 `m` 更清楚。

## 写法

- 短码：`:smile:`（全局命名空间，跨包不能重名）
- 兼容写法：`[表情:微笑]`，按名字匹配

两处都只认这一张表：站内渲染画图，通道投递按图片附件发，所以不同设备看到的永远是同一张图。

## 图的来源

[Noto Color Emoji](https://github.com/googlefonts/noto-emoji)，Apache License 2.0，可商用。
图片由这套字体的 109px 位图字形逐字渲染、居中裁到 128×128 透明底，不依赖联网。

重新生成（需要 NotoColorEmoji.ttf 与 Pillow）：

```bash
docker cp /usr/share/fonts/truetype/noto/NotoColorEmoji.ttf <backend容器>:/tmp/
docker exec <backend容器> python scripts/render_emoji.py
```

## 与 IM 通道的关系

QQ 那边的「名字 ↔ 平台表情」靠**名字**对齐：通道解析出 `[表情:名字]` 后，站内按这个名字找图。
QQ 自己的 `faceId` 编号与 QQ 原生表情的发送能力属于 QQ 通道插件，不在这里维护。
