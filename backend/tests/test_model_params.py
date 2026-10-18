"""采样参数的契约：创建助手不许把值拉满，自改配置也要收敛

背景（2026-10-18）：创建助手把 temperature/topP/presencePenalty/frequencyPenalty 全填到上限
（2 / 1 / 2 / 2，草稿 id=15），建出来的 AI 第一句话就是无意义字串，用户以为平台坏了。
根因不是模型，是字段契约只给了"API 允许 0~2"，没写"填到哪里会出事"。
"""


def test_specs_are_a_safe_subset_of_the_api_range():
    from app.utils.pure import model_params

    for name, spec in model_params.PARAM_SPECS.items():
        api_lo, api_hi = spec["api"]
        safe_lo, safe_hi = spec["safe"]
        assert api_lo <= safe_lo < safe_hi <= api_hi, f"{name}: 安全区间必须落在 API 区间内"
        assert safe_lo <= spec["default"] <= safe_hi, f"{name}: 默认值必须在安全区间内"


def test_clamp_pulls_extremes_back_and_keeps_reasonable_values():
    from app.utils.pure import model_params

    assert model_params.clamp("temperature", 2.0) == 1.5, "1.5 是官方建议的上限，2.0 才该被拉回来"
    assert model_params.clamp("temperature", 0.95) == 0.95, "正常值原样返回"
    assert model_params.clamp("temperature", -1) == 0.2
    assert model_params.clamp("presencePenalty", 2.0) == 1.0
    assert model_params.clamp("frequencyPenalty", -2.0) == 0.0
    assert model_params.clamp("topP", 1.0) == 1.0, "top_p=1 合法，但页面会提示它等于不裁剪"


def test_field_description_says_why_not_to_max_it():
    from app.utils.pure import model_params

    text = model_params.describe("temperature")
    for part in ("建议", "1.5", "跑题"):
        assert part in text, part


def test_creator_form_clamps_the_same_way():
    """创建助手写的表单值也要收敛：同一个契约，不能只在某个入口生效"""
    from app.services.creator.agent_creator_service import clean_fields

    cleaned = clean_fields({
        "temperature": 2.0, "topP": 1.0,
        "presencePenalty": 2.0, "frequencyPenalty": 2.0,
    })
    assert cleaned["temperature"] == 1.5
    assert cleaned["presencePenalty"] == 1.0 and cleaned["frequencyPenalty"] == 1.0
    assert cleaned["topP"] == 1.0
