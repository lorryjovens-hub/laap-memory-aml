"""LAAP — AML (Agent Memory Leaderboard) 接入包

实现 Agent Memory Leaderboard Cycle 2 要求的 Add / Search 记忆契约。

- :mod:`laap.aml.service` — 记忆服务（SQLite + BM25 检索）
- :mod:`laap.aml.server`  — HTTP 端点（Starlette）

赛事红线：Search 只返回**记忆证据**，绝不生成最终答案；所有读写按
``user_id`` 严格隔离；无结果返回 ``[]``。
"""

from laap.aml.service import AMLMemoryService, AddResult, MemoryChunk

#: AML 提交版本号（独立于主仓库 LAAP 版本，健康检查与榜单口径一致）
__version__ = "1.0.3"

__all__ = ["AMLMemoryService", "AddResult", "MemoryChunk", "__version__"]
