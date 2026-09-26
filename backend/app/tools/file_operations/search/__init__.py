"""搜索编排（工具侧唯一入口）。

对外三件事：plan_queries 把原话机械改写成检索式，merge_queries 合并调用方给的检索式，
search 按检索式并行扇出、过相关性闸门、去重重排并回退。工具壳只做参数校验与结果包装。
"""
from app.tools.file_operations.search.orchestrator import search
from app.tools.file_operations.search.plan import merge_queries, plan_queries

__all__ = ["search", "plan_queries", "merge_queries"]
