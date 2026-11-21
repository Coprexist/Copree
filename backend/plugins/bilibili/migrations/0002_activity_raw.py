"""使用轨迹只存机器数据：动作 + 调用参数原文。

原先 `summary` 一列混着两件事：工具参数 JSON、推送/登录的中文说明。翻译一旦写进库，
换语言、改文案、加工具都得回填——都不该是数据层的事。改成：

- `action` 工具 id（或 push / login），机器标识，永不翻译
- `raw`    这次调用实际带的参数（JSON），界面点「看原文」才原样显示

动作名（搜用户 / 点赞 / 推送…）由插件声明三语，界面按当前语言取——所以不落库。
老行的 summary 整体挪进 raw；label/summary 两列随后删除。
"""
import sqlalchemy as sa


def upgrade(conn, t) -> None:
    table = t("activity")
    conn.execute(sa.text(
        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS raw TEXT NOT NULL DEFAULT ''"
    ))
    # 老行：summary 里是参数 JSON（工具行）或一句中文（推送/登录行），统一挪进 raw
    conn.execute(sa.text(
        f"UPDATE {table} SET raw = summary WHERE raw = '' AND summary <> ''"
    ))
    # 上一版试验加的 label 列：动作名已改为插件声明，不再落库
    conn.execute(sa.text(f"ALTER TABLE {table} DROP COLUMN IF EXISTS label"))
    conn.execute(sa.text(f"ALTER TABLE {table} DROP COLUMN IF EXISTS summary"))
