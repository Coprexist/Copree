"""焦段：两条轴上的记忆适用范围

设计见 docs/memory_system/design/focus_and_memory_reach.md。锁住三件事：
- 预置「所有聊天」由纯函数兜底注入，任何存量数据里都不该出现它；
- 会话焦段要显式 join 才生效，不按对话类型自动归档；
- 合并把元素并起来、删掉被并入的那个，且预置焦段动不得。
"""


def test_all_chats_is_always_present_without_being_stored():
    from app.utils.pure.focus import ALL_CHATS_ID, normalize, storable

    assert [f["id"] for f in normalize(None)] == [ALL_CHATS_ID]
    assert storable(normalize(None)) == [], "预置焦段不落库"


def test_session_focus_needs_an_explicit_join():
    from app.utils.pure.focus import SESSION, add, join, of_session

    foci, focus, _ = add([], "学生群", SESSION)
    assert [f["name"] for f in of_session(foci, "group:64")] == ["所有聊天"], "没 join 就只算当前会话"

    foci, msg = join(foci, focus["id"], "group:64")
    assert "归入" in msg
    assert sorted(f["name"] for f in of_session(foci, "group:64")) == ["学生群", "所有聊天"]
    assert [f["name"] for f in of_session(foci, "group:99")] == ["所有聊天"], "别的会话不受影响"


def test_same_name_is_not_created_twice():
    from app.utils.pure.focus import SEMANTIC, add

    foci, first, _ = add([], "化学教学", SEMANTIC)
    foci, second, msg = add(foci, "化学教学", SEMANTIC)

    assert second["id"] == first["id"] and "已经有" in msg


def test_merge_keeps_elements_and_drops_the_other():
    from app.utils.pure.focus import SESSION, add, find, join, merge

    foci, a, _ = add([], "学生群", SESSION)
    foci, b, _ = add(foci, "补课群", SESSION)
    foci, _ = join(foci, b["id"], "group:99")
    foci, msg = merge(foci, a["id"], b["id"])

    assert find(foci, b["id"]) is None
    assert find(foci, a["id"])["elements"] == ["group:99"]
    assert "已并入" in msg


def test_builtin_cannot_be_renamed_merged_or_joined():
    from app.utils.pure.focus import ALL_CHATS_ID, SEMANTIC, add, join, merge, rename

    foci, temp, _ = add([], "临时", SEMANTIC)
    assert "预置" in rename(foci, ALL_CHATS_ID, "全部")[1]
    assert "预置" in merge(foci, temp["id"], ALL_CHATS_ID)[1]
    assert "预置" in join(foci, ALL_CHATS_ID, "group:1")[1]


def test_semantic_focus_is_not_a_session_anchor():
    from app.utils.pure.focus import SEMANTIC, add, join

    foci, focus, _ = add([], "化学教学", SEMANTIC)

    assert "语义焦段" in join(foci, focus["id"], "group:64")[1]


def test_describe_lists_both_axes():
    from app.utils.pure.focus import SESSION, SEMANTIC, add, describe, join

    foci, group, _ = add([], "学生群", SESSION)
    foci, topic, _ = add(foci, "化学教学", SEMANTIC)
    foci, _ = join(foci, group["id"], "group:64")

    line = describe(foci, "group:64", topic["id"])
    assert "会话焦段：" in line and "学生群" in line and "所有聊天" in line
    assert "语义焦段：化学教学" in line
    assert "语义焦段" not in describe(foci, "group:64", ""), "没切焦点就不念"
