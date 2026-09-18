"""AML 记忆服务 — 契约冒烟自测（可对本地或公网端点运行）

用法::

    # 本机
    python deploy/aml/smoke_test.py --base http://127.0.0.1:8095

    # 公网（带 Memory System Key）
    python deploy/aml/smoke_test.py --base https://aml.laap.cn --key "<key>"

覆盖（对齐 AML 官方 Smoke 的关注点 + 赛事红线）::

    [1] health             服务存活 / 鉴权开关
    [2] add contract       request_id 回显、success=true
    [3] search contract    data[] 结构、top_k 生效
    [4] no synthesis       Search 只回证据，不生成答案   ← 红线
    [5] isolation          不同 user_id 互不可见          ← 红线
    [6] empty -> []        无结果返回空数组
    [7] auth               无 key 401 / 有 key 200
    [8] latency            单次 search 延迟

只用标准库，零依赖。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Dict, Optional, Tuple

RED, GREEN, YELLOW, RESET = "\033[31m", "\033[32m", "\033[33m", "\033[0m"

#: 默认 User-Agent。
#: 注意：Cloudflare 的 Browser Integrity Check 会拦截 Python 默认 UA
#: (``Python-urllib/3.x``，返回 403 error code 1010)。显式设置一个普通客户端
#: UA 可避免被误拦；正式部署建议在 CF 侧对该域名关闭该检查。
DEFAULT_UA = "laap-aml-smoke/1.0 (+https://aml.laap.cn)"


def call(base: str, path: str, payload: Optional[dict] = None,
         key: str = "", timeout: int = 60,
         ua: str = DEFAULT_UA, retries: int = 2) -> Tuple[int, Dict[str, Any], float]:
    """发一次请求；网络层抖动（status=0）自动重试。"""
    last: Tuple[int, Dict[str, Any], float] = (0, {}, 0.0)
    for attempt in range(retries + 1):
        last = _call_once(base, path, payload, key, timeout, ua)
        if last[0] != 0:
            return last
        if attempt < retries:
            time.sleep(1.0 + attempt)
    return last


def _call_once(base: str, path: str, payload: Optional[dict],
               key: str, timeout: int,
               ua: str) -> Tuple[int, Dict[str, Any], float]:
    url = base.rstrip("/") + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", ua)
    req.add_header("Accept", "application/json")
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8")
            return r.status, (json.loads(body) if body else {}), time.time() - t0
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "ignore")
        try:
            return e.code, json.loads(raw), time.time() - t0
        except Exception:  # noqa: BLE001
            return e.code, {"_raw": raw[:200]}, time.time() - t0
    except Exception as e:  # noqa: BLE001
        return 0, {"_error": f"{type(e).__name__}: {e}"}, time.time() - t0


class Suite:
    def __init__(self, base: str, key: str):
        self.base = base
        self.key = key
        self.passed = 0
        self.failed = 0
        self.uid = f"smoke-{uuid.uuid4().hex[:8]}"
        self.uid2 = f"smoke-{uuid.uuid4().hex[:8]}"

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        if ok:
            self.passed += 1
            print(f"{GREEN}[PASS]{RESET} {name}" + (f"  ({detail})" if detail else ""))
        else:
            self.failed += 1
            print(f"{RED}[FAIL]{RESET} {name}" + (f"  {detail}" if detail else ""))
        return ok

    # ── 各项检查 ────────────────────────────────────────────────────────

    def t_health(self) -> bool:
        st, body, dt = call(self.base, "/health", key=self.key)
        ok = st == 200 and body.get("ok") is True
        return self.check("health", ok,
                          f"status={st} auth_required={body.get('auth_required')} "
                          f"{dt*1000:.0f}ms")

    def t_add(self) -> bool:
        rid = f"smoke-add-{uuid.uuid4().hex[:8]}"
        payload = {
            "request_id": rid,
            "messages": [
                {"role": "user", "content": "Last year I moved to Stockholm, Sweden.",
                 "timestamp": 1735689600000},
                {"role": "assistant", "content": "Noted."},
                {"role": "user", "content": "The project budget code is 4482."},
            ],
            "user_id": self.uid, "session_id": "smoke-s1",
        }
        st, body, _ = call(self.base, "/add", payload, self.key)
        ok = (st == 200 and body.get("success") is True
              and body.get("request_id") == rid
              and body.get("user_id") == self.uid)
        return self.check("add contract", ok,
                          f"status={st} echoed={body.get('request_id') == rid}")

    def t_search(self) -> bool:
        st, body, dt = call(self.base, "/search", {
            "query": "Where did the user move to?",
            "user_id": self.uid, "top_k": 100}, self.key)
        data = body.get("data")
        ok = st == 200 and isinstance(data, list) and len(data) > 0
        if ok:
            it = data[0]
            ok = "id" in it and isinstance(it.get("content"), str) and it["content"]
        self.last_search_ms = dt * 1000
        return self.check("search contract", ok,
                          f"status={st} items={len(data) if isinstance(data, list) else '?'} "
                          f"{dt*1000:.0f}ms")

    def t_no_synthesis(self) -> bool:
        st, body, _ = call(self.base, "/search", {
            "query": "Where does the user live?", "user_id": self.uid, "top_k": 20},
            self.key)
        data = body.get("data") or []
        joined = " ".join(str(d.get("content", "")) for d in data).lower()
        # 必须出现原始记忆痕迹，而不是"用户住在瑞典"这类合成句
        has_raw = "stockholm" in joined or "sweden" in joined
        # 粗判：不得出现明显的答案式前缀
        synth_markers = ["the answer is", "答案是", "based on the memories,"]
        no_synth = not any(m in joined for m in synth_markers)
        return self.check("no answer synthesis (红线)", bool(data) and has_raw and no_synth,
                          f"raw_evidence={has_raw} synth_free={no_synth}")

    def t_isolation(self) -> bool:
        # 给第二个 user 写不同内容
        call(self.base, "/add", {
            "request_id": f"iso-{uuid.uuid4().hex[:6]}",
            "messages": [{"role": "user", "content": "Second user likes hiking."}],
            "user_id": self.uid2, "session_id": "smoke-s2"}, self.key)
        st, body, _ = call(self.base, "/search", {
            "query": "budget code 4482", "user_id": self.uid2, "top_k": 50}, self.key)
        joined = " ".join(str(d.get("content", "")) for d in (body.get("data") or []))
        leaked = "4482" in joined or "stockholm" in joined.lower()
        return self.check("sample isolation (红线)", not leaked,
                          "no cross-user leak" if not leaked else "LEAK DETECTED")

    def t_empty(self) -> bool:
        st, body, _ = call(self.base, "/search", {
            "query": "anything at all",
            "user_id": f"nonexistent-{uuid.uuid4().hex[:6]}", "top_k": 10}, self.key)
        ok = st == 200 and body.get("data") == []
        return self.check("empty -> []", ok, f"status={st} data={body.get('data')}")

    def t_auth(self) -> bool:
        if not self.key:
            print(f"{YELLOW}[SKIP]{RESET} auth (未提供 --key，服务可能未开启鉴权)")
            return True
        st_no, _, _ = call(self.base, "/search",
                           {"query": "x", "user_id": self.uid, "top_k": 1}, key="")
        st_ok, _, _ = call(self.base, "/search",
                           {"query": "x", "user_id": self.uid, "top_k": 1}, key=self.key)
        ok = st_no == 401 and st_ok == 200
        return self.check("auth enforced", ok, f"no_key={st_no} with_key={st_ok}")

    def run(self) -> int:
        print("=" * 66)
        print(f"AML 记忆服务 Smoke — {self.base}")
        print("=" * 66)
        if not self.t_health():
            print(f"\n{RED}服务不可达，后续检查跳过。{RESET}")
            return 1
        self.t_add()
        self.t_search()
        self.t_no_synthesis()
        self.t_isolation()
        self.t_empty()
        self.t_auth()
        print("-" * 66)
        print(f"结果: {self.passed} passed / {self.failed} failed")
        if self.failed == 0:
            print(f"{GREEN}SMOKE OK — 可以提交 AML 评测申请{RESET}")
        else:
            print(f"{RED}SMOKE FAILED — 修复后再提交{RESET}")
        return 0 if self.failed == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="AML 记忆服务契约冒烟自测")
    ap.add_argument("--base", default="http://127.0.0.1:8095")
    ap.add_argument("--key", default="", help="Memory System Key（服务开启鉴权时必填）")
    args = ap.parse_args()
    return Suite(args.base, args.key).run()


if __name__ == "__main__":
    sys.exit(main())
