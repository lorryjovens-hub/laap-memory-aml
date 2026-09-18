"""LAAP AML Add/Search 契约冒烟测试。

覆盖赛事规则的关键红线：
1. Add 请求/响应契约（request_id 回显、success=true）；
2. Search 返回**证据**而非答案（data[] 结构、top_k 生效）；
3. 无结果返回 ``[]`` 且 data 字段存在；
4. **样本隔离**：不同 user_id 绝不互相检索（赛事硬性要求）；
5. 特殊信号（数字/专名）能命中；
6. HTTP 层端到端（Starlette + TestClient）；
7. 鉴权（设置 Memory System Key 后拒绝无 key 请求）；
8. 30 天删除的运维接口（purge_user）。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# 可移植导入：以本文件为基准解析仓库根
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

from laap.aml.service import AMLMemoryService


def _mk(tmp: Path) -> AMLMemoryService:
    return AMLMemoryService(str(tmp / "aml.sqlite3"))


# ── 1. Add 契约 ─────────────────────────────────────────────────────────

def test_add_contract(tmp: Path):
    svc = _mk(tmp)
    req = {
        "request_id": "req-001",
        "messages": [
            {"role": "user", "content": "我搬到了瑞典的斯德哥尔摩。",
             "timestamp": 1735689600000},
            {"role": "assistant", "content": "记下了。"},
            {"role": "user", "content": "我的预算代码是 4482。"},
        ],
        "user_id": "u1",
        "session_id": "s1",
    }
    res = svc.add(req["request_id"], req["messages"], req["user_id"], req["session_id"])
    d = res.to_dict()
    assert d["success"] is True
    assert d["request_id"] == "req-001"
    assert d["user_id"] == "u1"
    assert d["session_id"] == "s1"
    assert res.chunks >= 1
    print(f"[OK] add contract (chunks={res.chunks})")


# ── 2. Search 返回证据 ──────────────────────────────────────────────────

def test_search_returns_evidence(tmp: Path):
    svc = _mk(tmp)
    svc.add("r", [
        {"role": "user", "content": "Last year I moved to Stockholm, Sweden."},
        {"role": "user", "content": "My favourite food is ramen."},
        {"role": "user", "content": "The project budget code is 4482."},
    ], "u1", "s1")
    data = svc.search("Where did the user move to?", "u1", top_k=5)
    assert isinstance(data, list) and data, "expected evidence"
    joined = " ".join(d["content"] for d in data).lower()
    assert "stockholm" in joined or "sweden" in joined
    # 结构契约
    for it in data:
        assert set(["id", "content"]).issubset(it.keys())
        assert isinstance(it["content"], str) and it["content"]
    print(f"[OK] search returns evidence ({len(data)} items, top={data[0]['score']})")


def test_search_is_not_answer_generation(tmp: Path):
    """Search 必须只回证据——不得合成答案（赛事红线）。"""
    svc = _mk(tmp)
    svc.add("r", [{"role": "user", "content": "The user lives in Sweden."}], "u1", "s1")
    data = svc.search("Where does the user live?", "u1", top_k=5)
    assert data
    # 返回内容应包含原始记忆片段，而不是"用户住在瑞典"这类合成句
    assert any("Sweden" in d["content"] for d in data)
    print("[OK] search returns raw evidence (no answer synthesis)")


# ── 3. 空结果 ───────────────────────────────────────────────────────────

def test_empty_results(tmp: Path):
    svc = _mk(tmp)
    data = svc.search("anything", "nonexistent-user", top_k=10)
    assert data == [], data
    print("[OK] empty results -> []")


# ── 4. 样本隔离（硬性要求）──────────────────────────────────────────────

def test_sample_isolation(tmp: Path):
    svc = _mk(tmp)
    svc.add("r", [{"role": "user", "content": "User A secret code is 1111."}], "userA", "sA")
    svc.add("r", [{"role": "user", "content": "User B likes hiking."}], "userB", "sB")

    a = svc.search("secret code", "userA", top_k=10)
    b = svc.search("secret code", "userB", top_k=10)
    assert a and "1111" in " ".join(x["content"] for x in a)
    assert not b or "1111" not in " ".join(x["content"] for x in b), \
        "cross-user leak detected!"
    print("[OK] sample isolation enforced (no cross-user leak)")


# ── 5. 信号命中 ─────────────────────────────────────────────────────────

def test_signal_matching(tmp: Path):
    svc = _mk(tmp)
    svc.add("r", [
        {"role": "user", "content": "Project Alpha budget is 7311."},
        {"role": "user", "content": "Project Beta budget is 9042."},
        {"role": "user", "content": "Project Gamma budget is 5588."},
    ], "u1", "s1")
    data = svc.search("What is Project Beta budget?", "u1", top_k=3)
    assert data
    assert "9042" in data[0]["content"], data[0]["content"]
    print(f"[OK] signal matching (top hit = {data[0]['content'][:44]!r})")


def test_top_k_respected(tmp: Path):
    svc = _mk(tmp)
    msgs = [{"role": "user", "content": f"Fact number {i} about topic alpha."}
            for i in range(20)]
    svc.add("r", msgs, "u1", "s1")
    data = svc.search("topic alpha", "u1", top_k=5)
    assert len(data) <= 5, len(data)
    print(f"[OK] top_k respected ({len(data)} <= 5)")


# ── 6. HTTP 端到端 ──────────────────────────────────────────────────────

def test_http_end_to_end(tmp: Path):
    os.environ["AML_DB"] = str(tmp / "http.sqlite3")
    os.environ.pop("AML_MEMORY_KEY", None)
    import importlib
    from laap.aml import server as srv
    importlib.reload(srv)
    srv._MEMORY_KEY = ""
    srv._SERVICE = None
    app = srv.build_app()
    try:
        from starlette.testclient import TestClient
    except Exception as e:  # noqa: BLE001
        print(f"[SKIP] TestClient unavailable: {e}")
        return
    with TestClient(app) as cli:
        r = cli.get("/health")
        assert r.status_code == 200 and r.json()["ok"] is True
        r = cli.post("/add", json={
            "request_id": "h1",
            "messages": [{"role": "user", "content": "The launch code is 7788."}],
            "user_id": "hu", "session_id": "hs"})
        assert r.status_code == 200 and r.json()["success"] is True
        r = cli.post("/search", json={
            "query": "launch code", "user_id": "hu", "top_k": 5})
        assert r.status_code == 200
        body = r.json()
        assert "data" in body and body["data"]
        assert "7788" in body["data"][0]["content"]
    print("[OK] HTTP end-to-end (/health, /add, /search)")


def test_auth(tmp: Path):
    os.environ["AML_DB"] = str(tmp / "auth.sqlite3")
    os.environ["AML_MEMORY_KEY"] = "secret-key-123"
    import importlib
    from laap.aml import server as srv
    importlib.reload(srv)
    srv._MEMORY_KEY = "secret-key-123"
    srv._SERVICE = None
    app = srv.build_app()
    try:
        from starlette.testclient import TestClient
    except Exception as e:  # noqa: BLE001
        print(f"[SKIP] TestClient unavailable: {e}")
        return
    with TestClient(app) as cli:
        assert cli.post("/search", json={"query": "x", "user_id": "u", "top_k": 1}).status_code == 401
        ok = cli.post("/search", json={"query": "x", "user_id": "u", "top_k": 1},
                      headers={"X-Memory-Key": "secret-key-123"})
        assert ok.status_code == 200
        ok2 = cli.post("/search", json={"query": "x", "user_id": "u", "top_k": 1},
                       headers={"Authorization": "Bearer secret-key-123"})
        assert ok2.status_code == 200
    os.environ.pop("AML_MEMORY_KEY", None)
    print("[OK] auth enforced (401 without key, 200 with key)")


# ── 8. 运维 ─────────────────────────────────────────────────────────────

def test_purge(tmp: Path):
    svc = _mk(tmp)
    svc.add("r", [{"role": "user", "content": "temp data 1234"}], "u9", "s9")
    assert svc.stats("u9")["chunks"] > 0
    n = svc.purge_user("u9")
    assert n > 0 and svc.stats("u9")["chunks"] == 0
    print(f"[OK] purge_user (deleted {n} chunks, compliance support)")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        tests = [
            lambda: test_add_contract(tmp),
            lambda: test_search_returns_evidence(tmp),
            lambda: test_search_is_not_answer_generation(tmp),
            lambda: test_empty_results(tmp),
            lambda: test_sample_isolation(tmp),
            lambda: test_signal_matching(tmp),
            lambda: test_top_k_respected(tmp),
            lambda: test_http_end_to_end(tmp),
            lambda: test_auth(tmp),
            lambda: test_purge(tmp),
        ]
        failed = 0
        for fn in tests:
            try:
                fn()
            except AssertionError as e:
                failed += 1
                print(f"[FAIL] {fn}: {e}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"[ERROR] {fn}: {type(e).__name__}: {e}")
        print(f"\n{'='*50}\n{len(tests)-failed}/{len(tests)} passed")
        sys.exit(1 if failed else 0)
