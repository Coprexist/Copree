"""配置链的机械保证（docs/dev/api_config_chain.md §5.3）。

不靠 review：这些规则一旦破，界面显示的与实际请求用的就会分叉，
而且往往只在线上以「401 / 模型不存在 / 填了不生效」的形式出现。
"""
import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"

# 两个裸 Key 列 + 两个全局模型列：已从解析链删除，只允许模型定义本身提到它们
_BARE_RE = re.compile(
    r"\b(?:user|agent|chatter)\.(?:api_base_url|api_key_encrypted"
    r"|global_chat_model|global_work_model)\b"
)
_BARE_ALLOWED = {
    "models/user.py",
    "models/agent.py",
}


def _py_files():
    return [p for p in APP.rglob("*.py") if "__pycache__" not in p.parts]


def test_bare_key_columns_are_not_read():
    """任何地方都不许再拿 users/agents 的裸 Key/地址去请求。

    它们是 0123/0124 迁移留下的僵尸值：解绑配置档后会用「迁移前那一刻的快照」
    把请求打出去，而界面上显示的是另一条配置。要改的写入口已翻译成配置档
    （services/agent/legacy_config_bridge.py）。
    """
    offenders = []
    for path in _py_files():
        rel = path.relative_to(APP).as_posix()
        if rel in _BARE_ALLOWED:
            continue
        for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _BARE_RE.search(line):
                offenders.append(f"{rel}:{no}: {line.strip()}")
    assert not offenders, (
        "这些地方还在读裸 Key/地址，改为 resolve_llm()/resolve_user_llm()：\n"
        + "\n".join(offenders)
    )


def test_pool_selection_has_one_entry():
    """「分到哪个池 Key」只有解析器一处判断（管理端探测除外）。"""
    allowed = {
        "services/agent/api_config_resolver.py",
        "services/infrastructure/quota_service.py",  # 定义
        "routers/admin.py",                          # 管理台「测这把 Key 通不通」
    }
    offenders = []
    for path in _py_files():
        rel = path.relative_to(APP).as_posix()
        if rel in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        if "find_best_pool_key" in text:
            offenders.append(rel)
    assert not offenders, f"池 Key 选择出现了第二处实现：{offenders}"


def test_legacy_credential_modules_are_gone():
    """两份自称「唯一入口」的旧模块已合并进 api_config_resolver。

    留着它们，下一个人会照着其中一份再写第二种回退链（这就是 1.0 之前的状态：
    四份实现、三份文档都写着唯一入口）。
    """
    assert not (APP / "services/agent/llm_credentials.py").exists()
    assert not (APP / "services/infrastructure/user_credentials.py").exists()


def test_messages_do_not_switch_frames():
    """消息到达不许自动切帧（docs/dev/frame_turn_lifecycle.md §7.3）。

    系统只把帧摆进状态链、记一条未读；去不去由 AI 自己 pop。这条一旦破，世界帧又会
    被群消息拖走——而单测跑在「直接调 ensure_active_frame」上，测不出调用方错了。
    """
    offenders = []
    for rel in ("ai/response_worker.py", "routers/dm.py", "tools/chat_social/send_dm.py"):
        text = (APP / rel).read_text(encoding="utf-8")
        if "ensure_active_frame" in text:
            offenders.append(f"{rel}: 消息路径还在调 ensure_active_frame")
        if "attach_stimulus" not in text:
            offenders.append(f"{rel}: 消息路径没走 attach_stimulus")
    assert not offenders, chr(10).join(offenders)
