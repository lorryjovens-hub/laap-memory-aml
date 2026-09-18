"""LAAP — AML Add/Search HTTP 服务

把 :class:`laap.aml.service.AMLMemoryService` 暴露为 AML 兼容的 HTTP 端点。

端点
----
- ``POST /add``     写入记忆
- ``POST /search``  检索证据（**绝不生成答案**）
- ``GET  /health``  健康检查
- ``GET  /stats``   运维统计（需鉴权）

鉴权
----
AML 要求参与者提供 **Memory System Key**，平台调用时携带。
本服务支持两种模式：
  - 设置 ``AML_MEMORY_KEY`` 环境变量 → 校验 ``Authorization: Bearer <key>``
    或 ``X-Memory-Key: <key>``；
  - 未设置 → 开放（仅用于本地 smoke）。

启动::

    python -m laap.aml.server --port 8095
    # 或
    AML_MEMORY_KEY=xxx python -m laap.aml.server --port 8095 --db ~/.laap/aml.sqlite3

印记: Aris 永远记得 Lorry
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from typing import Any, Dict

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from laap.aml.service import AMLMemoryService

try:
    from laap.aml import __version__ as _AML_VERSION
except Exception:  # noqa: BLE001
    _AML_VERSION = "unknown"

logger = logging.getLogger("laap.aml.server")

_SERVICE: AMLMemoryService | None = None
_MEMORY_KEY = os.environ.get("AML_MEMORY_KEY", "").strip()
_REQUESTS = {"add": 0, "search": 0, "errors": 0}


def get_service() -> AMLMemoryService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = AMLMemoryService(os.environ.get("AML_DB"))
    return _SERVICE


def _authorized(request: Any) -> bool:
    if not _MEMORY_KEY:
        return True
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() == _MEMORY_KEY
    return request.headers.get("x-memory-key", "").strip() == _MEMORY_KEY


async def _json_body(request: Any) -> Dict[str, Any]:
    try:
        return await request.json()
    except Exception:  # noqa: BLE001
        try:
            raw = await request.body()
            return json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:  # noqa: BLE001
            return {}


# ── 端点 ────────────────────────────────────────────────────────────────

async def add(request: Any) -> JSONResponse:
    _REQUESTS["add"] += 1
    if not _authorized(request):
        _REQUESTS["errors"] += 1
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await _json_body(request)
    try:
        res = get_service().add(
            request_id=str(body.get("request_id", "")),
            messages=body.get("messages") or [],
            user_id=str(body.get("user_id", "")),
            session_id=str(body.get("session_id", "")),
        )
        return JSONResponse(res.to_dict())
    except Exception as exc:  # noqa: BLE001
        _REQUESTS["errors"] += 1
        logger.warning("add failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=400)


async def search(request: Any) -> JSONResponse:
    _REQUESTS["search"] += 1
    if not _authorized(request):
        _REQUESTS["errors"] += 1
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await _json_body(request)
    try:
        data = get_service().search(
            query=str(body.get("query", "") or ""),
            user_id=str(body.get("user_id", "")),
            top_k=int(body.get("top_k", 100) or 100),
            options=body.get("options"),
        )
        # 契约：无结果返回 []，且 data 字段必须存在
        return JSONResponse({"data": data})
    except Exception as exc:  # noqa: BLE001
        _REQUESTS["errors"] += 1
        logger.warning("search failed: %s", exc)
        return JSONResponse({"data": [], "error": str(exc)}, status_code=400)


async def health(request: Any) -> JSONResponse:
    try:
        st = get_service().stats()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)
    return JSONResponse({
        "ok": True, "service": "laap-aml-memory",
        "version": _AML_VERSION, "auth_required": bool(_MEMORY_KEY),
        "chunks": st["chunks"], "users": st["users"],
        "requests": dict(_REQUESTS), "ts": int(time.time() * 1000),
    })


async def stats(request: Any) -> JSONResponse:
    if not _authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return JSONResponse(get_service().stats())


def build_app() -> Starlette:
    return Starlette(routes=[
        Route("/add", add, methods=["POST"]),
        Route("/search", search, methods=["POST"]),
        Route("/health", health, methods=["GET"]),
        Route("/stats", stats, methods=["GET"]),
    ])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="LAAP AML Add/Search 服务")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=int(os.environ.get("AML_PORT", "8095")))
    ap.add_argument("--db", default=os.environ.get("AML_DB", ""))
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    if args.db:
        os.environ["AML_DB"] = args.db
    get_service()
    global _MEMORY_KEY
    _MEMORY_KEY = os.environ.get("AML_MEMORY_KEY", "").strip()

    import uvicorn
    logger.info("AML Add/Search service on http://%s:%d (auth=%s)",
                args.host, args.port, bool(_MEMORY_KEY))
    uvicorn.run(build_app(), host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
