"""
路径命名（纯函数，无 IO）— 存储层与一次性迁移共用一份实现

「撞名让后缀」这条判据只能有一份：线上收文件（file_service._adopt_into）和迁移脚本
（scripts/migrate_owner_paths）都靠它保证「绝不覆盖已有的字节」。两边各写一份，
迟早在边界上分叉（无扩展名、点开头的文件名、目录名里带点）。
"""


def unique_sibling(relative_path: str, suffix: int) -> str:
    """同一目录里撞名时给这次让一个后缀，返回新路径（绝不覆盖已有的字节）

    `photos/a.tar.gz` → `photos/a.tar.57.gz`：只换最后一段扩展名——
    `.tar.gz` 这种双扩展名按最后一段算，原来那个点还留在名字里。
    `README` → `README.57`、`.env` → `.env.57`：rpartition 在没有点时 head 为空，
    统一往后追加，不会拼出 `.57.env` 这种看不懂的名字。
    """
    head, _dot, ext = str(relative_path).rpartition(".")
    if not head or "/" in ext:
        return f"{relative_path}.{suffix}"
    return f"{head}.{suffix}.{ext}"
