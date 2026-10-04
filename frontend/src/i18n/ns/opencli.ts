/**
 * opencli 命名空间字典（i18n 分区）
 *
 * 从原 translations.ts 主字典按 key 首个点分段拆出；本文件内 key 不带命名空间前缀，
 * 调用点写 t('opencli:key')。字段内容与拆分前逐字节一致。
 */

import type { TranslationDict } from '../types'

export const opencliZh: TranslationDict = {

  // ======================== OpenCLI 预设 / OpenCLI Presets ========================
  'category.fileOps': '文件操作',
  'category.browser': '浏览器自动化',
  'category.cliBridge': '外部 CLI 桥接',
  'preset.fileRead': '读取文件 — 在自己文件空间里读取文本文件内容',
  'preset.fileWrite': '写入文件 — 创建或覆盖自己文件空间里的文件（自动建子目录）',
  'preset.fileList': '列出文件 — 浏览自己文件空间里的文件和子目录',
  'preset.fileDelete': '删除文件 — 删除自己文件空间里不需要的文件',
  'preset.fileInfo': '文件信息 — 查看文件大小、修改时间等元信息',
  'preset.createDir': '创建目录 — 在自己文件空间里创建新文件夹',
  'preset.browser': '浏览器操作 — AI 能打开网页、截图、点击、填表、抓取内容',
  'preset.listCmds': '列出命令 — AI 查看当前可用的所有 OpenCLI 命令',
  'preset.ghCli': 'GitHub CLI — 浏览仓库、PR、Issue、搜索代码（需 gh CLI 已登录）',
  'preset.dockerCli': 'Docker — 管理容器、镜像、查看运行状态',
  'preset.obsidianCli': 'Obsidian — 读写笔记、搜索知识库',
  'preset.vercelCli': 'Vercel — 部署网站、查看项目、管理域名',
  'preset.tgCli': 'Telegram CLI — 收发消息、管理频道',
  'preset.discordCli': 'Discord CLI — 发消息、管理服务器',
  'preset.wxCli': '微信 CLI — 下载公众号文章、管理消息',
  'status.notAdded': '未添加',
}

export const opencliEn: TranslationDict = {
  'category.fileOps': 'File Operations',
  'category.browser': 'Browser Automation',
  'category.cliBridge': 'External CLI Bridge',
  'preset.fileRead': 'Read File — read text files in your workspace',
  'preset.fileWrite': 'Write File — create or overwrite files in your workspace (auto create subdirs)',
  'preset.fileList': 'List Files — browse files and subdirectories in your workspace',
  'preset.fileDelete': 'Delete File — remove unwanted files from your workspace',
  'preset.fileInfo': 'File Info — view metadata such as file size and modification time',
  'preset.createDir': 'Create Directory — create new folders in your workspace',
  'preset.browser': 'Browser — AI can open webpages, screenshot, click, fill forms, scrape content',
  'preset.listCmds': 'List Commands — AI views all available OpenCLI commands',
  'preset.ghCli': 'GitHub CLI — browse repos, PRs, Issues, search code (requires gh CLI logged in)',
  'preset.dockerCli': 'Docker — manage containers, images, view running status',
  'preset.obsidianCli': 'Obsidian — read/write notes, search knowledge base',
  'preset.vercelCli': 'Vercel — deploy sites, view projects, manage domains',
  'preset.tgCli': 'Telegram CLI — send/receive messages, manage channels',
  'preset.discordCli': 'Discord CLI — send messages, manage servers',
  'preset.wxCli': 'WeChat CLI — download articles, manage messages',
  'status.notAdded': 'Not Added',
}

export const opencliJa: TranslationDict = {
  'category.fileOps': 'ファイル操作',
  'category.browser': 'ブラウザ自動化',
  'category.cliBridge': '外部CLIブリッジ',
  'preset.fileRead': 'ファイル読み取り — ワークスペースのテキストファイルを読む',
  'preset.fileWrite': 'ファイル書き込み — ワークスペースにファイルを作成・上書き（サブディレクトリ自動作成）',
  'preset.fileList': 'ファイル一覧 — ワークスペースのファイルとサブディレクトリを閲覧',
  'preset.fileDelete': 'ファイル削除 — 不要なファイルをワークスペースから削除',
  'preset.fileInfo': 'ファイル情報 — ファイルサイズや更新日時などのメタデータを表示',
  'preset.createDir': 'ディレクトリ作成 — ワークスペースに新しいフォルダを作成',
  'preset.browser': 'ブラウザ — AIがWebページを開き、スクリーンショット、クリック、フォーム入力、スクレイピング',
  'preset.listCmds': 'コマンド一覧 — AIが利用可能な全OpenCLIコマンドを表示',
  'preset.ghCli': 'GitHub CLI — リポジトリ、PR、Issueの閲覧、コード検索（gh CLI ログインが必要）',
  'preset.dockerCli': 'Docker — コンテナ、イメージの管理、実行状態の表示',
  'preset.obsidianCli': 'Obsidian — ノートの読み書き、ナレッジベース検索',
  'preset.vercelCli': 'Vercel — サイトのデプロイ、プロジェクト表示、ドメイン管理',
  'preset.tgCli': 'Telegram CLI — メッセージ送受信、チャンネル管理',
  'preset.discordCli': 'Discord CLI — メッセージ送信、サーバー管理',
  'preset.wxCli': 'WeChat CLI — 記事のダウンロード、メッセージ管理',
  'status.notAdded': '未追加',
}
