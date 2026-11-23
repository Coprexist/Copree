# AGENTS.md

> 给在本仓库工作的一切 AI 编码代理（DSH、Claude Code 等）的项目说明。
> **第一条优先级最高：动手之前先读它。**

## 🥇 用中文思考

**推理与回复一律用中文。** 工具调用前的计划、todo 项、过程解释、结论与总结，全部用中文写。
代码、标识符、命令与既有英文文案沿用仓库原样，不因为这条规则翻译它们。

## ✅ 改完要过的检查

```bash
# 后端：必须在容器里跑（宿主机 python 3.10 缺 3.11 语法）
docker exec -e TEST_DATABASE_URL="postgresql+asyncpg://<用户>:<密码>@postgres:5432/ai_group_chat_test" \
  ai_group_backend python -m pytest tests -q --timeout=90

# 前端：宿主机没有 npx，用本地 bin
cd frontend
./node_modules/.bin/tsc -p tsconfig.json --noEmit   # 已开 noUnusedLocals / noUnusedParameters
node scripts/check-ui.mjs
node scripts/check-i18n.mjs && node scripts/check-i18n-keys.mjs
```

测试套件里有少量**既有失败**（人格版本、QQ 通道环境、时间戳契约、世界技能文档等），
先看基线：只对「这次改动引入的失败」负责，顺手修既有的也可以，但别把基线当成自己弄坏的。

改动要同步 `CHANGELOG.md`（走 `changelog` 工具，它会校验分节名与结构）。

改了 LLM 相关代码（system 布局、凭证解析、世界对话）时，再用本地小模型真跑一遍——
单元测试里的 client 都是替身，它们证明不了「Qwen 的 Jinja 会不会因为 system 不在开头而 500」：

```bash
docker exec -w /app ai_group_backend python scripts/selfcheck_local_llm.py
```
