"""上下文窗口：本地端点按部署保守值，其余按模型名。"""
from app.utils.pure.model_window import (
    DEFAULT_CONTEXT_WINDOW, LOCAL_CONTEXT_WINDOW, context_window_for,
)


def test_cloud_model_uses_name_table():
    assert context_window_for("deepseek-chat") == 128_000
    assert context_window_for("claude-3-7-sonnet") == 200_000
    assert context_window_for("gemini-2.5-pro") == 1_000_000


def test_local_endpoint_ignores_model_name():
    """名字撞上 deepseek 也不能给 128K：ollama 默认 num_ctx 只有 4K，按大的算会被静默截断。"""
    assert context_window_for("deepseek-r1:7b", "http://172.17.0.1:11434/v1") == LOCAL_CONTEXT_WINDOW
    assert context_window_for("qwen3:4b", "http://127.0.0.1:11434") == LOCAL_CONTEXT_WINDOW


def test_local_endpoint_without_model_still_conservative():
    assert context_window_for(None, "http://192.168.1.7:8080/v1") == LOCAL_CONTEXT_WINDOW


def test_unknown_model_falls_back_to_default():
    assert context_window_for("some-new-model") == DEFAULT_CONTEXT_WINDOW
    assert context_window_for(None) == DEFAULT_CONTEXT_WINDOW


def test_cloud_endpoint_with_local_looking_name_still_conservative():
    """端点指的是云端服务时，即便模型名看着像本地量化版也按名字表走。"""
    assert context_window_for("deepseek-r1:7b", "https://api.deepseek.com/v1") == 128_000
