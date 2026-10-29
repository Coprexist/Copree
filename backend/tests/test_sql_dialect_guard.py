"""裸 SQL 的方言守卫。

同一份后端要跑两种库：容器是 PostgreSQL，安装包是 SQLite。历史事故清一色是
「PG 能过、SQLite 报错」——`::type` 转换（unrecognized token ":"）、`->>` 取值、
`now()`（no such function）、`current_date - interval 'N days'`、`GROUP BY 1`（静默算错）。
这类写法只允许出现在 db_providers/（方言层）；业务层要 JSON 取值、向量相似度、
类型转换，一律走 SQLAlchemy 表达式或 provider 提供的 SQL 片段。

用 AST 而不是 grep：只有真正的 SQL 字符串才检查，注释与 docstring 里提到这些写法不算。
"""

import ast
import re
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"

#: 方言层：PG / SQLite 各自的语法只能写在这里
DIALECT_LAYER = APP_DIR / "db_providers"

#: 单个文件例外：值是「靠什么保证 SQLite 不会走到这段 SQL」，不是「以后再说」。
GUARDED_FILES = {
    "routers/memories.py": "向量 SQL 只在 provider.supports_sql_vector_search 分支里拼",
    "tools/memory/recall_memory.py": "同上，PG 分支才拼 <=>",
    "tools/world/recall_memory.py": "同上，PG 分支才拼 <=>",
    "services/memory/memory_service.py": "同上，provider.supports_sql_vector_search 门内",
    "services/memory/vector_pipeline.py": "同上，provider.supports_sql_vector_search 门内",
    "services/content/file_service.py": "JSONB 的 @> 只在 provider.name == 'postgres' 分支里拼",
    "services/infrastructure/embedding_config_service.py": "pg_attribute 自检，调用前判过 provider.name",
}

#: (正则, 说明)。命中即失败。
RULES = [
    (re.compile(r"::\s*(int|integer|bigint|text|date|jsonb|numeric|varchar|uuid|timestamp|float)\b"),
     "PG 的 :: 类型转换；用 CAST(... AS T) 或 SQLAlchemy cast()"),
    (re.compile(r"->>?"), "PG 的 ->> JSON 取值；用 JSON 列下标或 provider.json_text_expr()"),
    (re.compile(r"\bnow\(\)", re.I), "PG 的 now()；用 CURRENT_TIMESTAMP 或 func.now()"),
    (re.compile(r"\binterval\s+'", re.I), "PG 的 interval 字面量；时间窗在 Python 里算好传参"),
    (re.compile(r"\b(GROUP BY|ORDER BY)\s+\d"), "按序号分组/排序；SQLite 当常量，静默算错"),
    (re.compile(r"\bnextval\b|\bsetval\b"), "PG 序列函数；SQLite 没有序列"),
    (re.compile(r"advisory_lock"), "PG 咨询锁；SQLite 无此能力"),
    (re.compile(r"<=>"), "pgvector 余弦算子；放 provider.vector_similarity_expr()"),
    (re.compile(r"FILTER\s*\("), "聚合的 FILTER；改用 CASE WHEN，两种方言都认"),
    (re.compile(r"to_tsvector|tsquery|ts_rank"), "PG 全文检索；只有 PG provider 能用"),
]

_SQL_HINT = re.compile(r"\b(SELECT|INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM)\b", re.I)


def _docstring_nodes(tree: ast.AST) -> set[int]:
    nodes: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", [])
        if not body:
            continue
        first = body[0]
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            nodes.add(id(first.value))
    return nodes


def _sql_strings(tree: ast.AST):
    """产出 (行号, 字符串) 的 SQL 字面量（跳过 docstring）。"""
    skip = _docstring_nodes(tree)
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in skip and _SQL_HINT.search(node.value)):
            yield node.lineno, node.value


def scan() -> list[str]:
    problems: list[str] = []
    for path in sorted(APP_DIR.rglob("*.py")):
        rel = path.relative_to(APP_DIR).as_posix()
        if DIALECT_LAYER in path.parents or rel in GUARDED_FILES:
            continue
        # utf-8-sig：仓库里有文件带 BOM，ast.parse 不接受裸 BOM
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for lineno, sql in _sql_strings(tree):
            for pattern, reason in RULES:
                hit = pattern.search(sql)
                if hit:
                    problems.append(f"{rel}:{lineno} ← {hit.group(0)!r}：{reason}")
    return problems


def test_no_pg_only_sql_outside_provider_layer():
    problems = scan()
    assert not problems, (
        "裸 SQL 里出现只有 PG 认的写法（放到 db_providers/ 里，或改成 SQLAlchemy 表达式）：\n"
        + "\n".join(problems)
    )


def test_dialect_fragments_are_provider_specific():
    """方言层确实按库给出不同 SQL：JSON 取值两种写法都要对。"""
    from app.db_providers.postgres import postgres_provider
    from app.db_providers.sqlite import sqlite_provider

    assert postgres_provider.json_text_expr("details", "kind") == "details ->> 'kind'"
    assert sqlite_provider.json_text_expr("details", "kind") == "json_extract(details, '$.kind')"
    # 向量检索能力必须显式声明：业务层靠它决定要不要降级
    assert postgres_provider.supports_sql_vector_search is True
    assert sqlite_provider.supports_sql_vector_search is False
