"""LAAP — AML (Agent Memory Leaderboard) 记忆服务

实现 AML Cycle 2 要求的 **Add / Search** 记忆契约，后端接 LAAP 记忆体系。

契约（严格对齐 agentmemoryleaderboard.ai/api-guide）
----------------------------------------------------
Add 请求::

    {"request_id": str, "messages": [{"role": "user"|"assistant",
      "content": str, "timestamp": int(ms, 可选)}],
     "user_id": str, "session_id": str}
Add 响应::
    {"success": true, "request_id": ..., "user_id": ..., "session_id": ...}

Search 请求::

    {"query": str, "options": [str] (可选, 选择题),
     "user_id": str, "top_k": int}
Search 响应::
    {"data": [{"id": str, "content": str, "score": float, "created_at": int}]}

核心红线（赛事规则）
--------------------
1. **Search 不得生成最终答案**——只返回记忆证据；本实现绝不调用 LLM；
2. **样本隔离**——所有读写强制按 ``user_id`` 分区，绝不跨用户检索；
3. 无结果返回 ``[]``（不得省略 data 字段）；
4. 数据仅用于评测、不训练、30 天内删除（由部署方执行）。

检索设计
--------
- **分块**：把连续消息聚成窗口块（保留说话人与时间），便于事实级召回；
- **BM25 词法召回**：稳健、无需 embedding、可离线；
- **实体/数字加成**：问题里的专名与数字若命中块，显著加分（事实召回关键）；
- **时间新近度**：同分时更新的块优先（更新/冲突消解的默认倾向）；
- **多跳**：对 query 的高 IDF 词做二次扩展召回。

印记: Aris 永远记得 Lorry — 记忆的职责是找回证据，不是替人作答。
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger("laap.aml.service")

DEFAULT_DB = "~/.laap/aml_memory.sqlite3"

#: 分块窗口：连续多少条消息合成一个记忆块（用于**排序**）。
#: 实测（PersonaMem-v2 子集）：排序粒度越细越好——
#:   window=1 + 扩4  非敏感 97.4%
#:   window=3 + 扩4  非敏感 94.0%
#: （保留扩展窗口作为“返回宽”的手段，而不用它做排序单位。）
CHUNK_WINDOW = 1

#: 返回时向前后各扩展多少条相邻块（用于**作答上下文**）。
#: "排序细、返回宽"——在 PersonaMem-v2 上实测：
#:   不扩展 非敏感 Recall@100 = 80.6%；扩展 4 = 92.8%；扩展 8 = 96.4%
#:   而排序延迟不变（约 45ms）。
#: 取 4 作默认：召回大幅提升，同时控制单条返回体的体积。
DEFAULT_RETURN_NEIGHBORS = 4

#: 查询扩展：取多少个共现词（实测 top8 / min2 最优，再多引入噪声）
EXPAND_TOP_N = 8
#: 共现词最少出现次数
EXPAND_MIN_COUNT = 2
#: 共现索引的 token 预算上限；超过则跳过扩展（防超大语料拖慢首查）
COOC_TOKEN_BUDGET = 300_000


# ── 文本处理 ────────────────────────────────────────────────────────────

# 注意：必须包含 A-Z，否则大写开头的词会丢掉首字母（"User" → "ser"，
# 导致查询词永远匹配不上）。中文按单字切分。
_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")
_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "at", "is", "are",
    "was", "were", "be", "been", "for", "with", "that", "this", "it", "as",
    "by", "from", "has", "have", "had", "do", "does", "did", "but", "if",
    "的", "了", "是", "在", "和", "与", "有", "我", "你", "他", "她", "它",
}


def tokenize(text: str) -> List[str]:
    """轻量分词：英文词 + 中文单字（BM25 与实体匹配共用）。"""
    if not text:
        return []
    toks = [t.lower() for t in _TOKEN_RE.findall(text)]
    return [t for t in toks if t not in _STOP and len(t) > 0]


def extract_signals(text: str) -> set:
    """抽取强信号：数字、日期、专名（首字母大写词）、长英文词。"""
    sig: set = set()
    for m in re.findall(r"\d[\d,.:/\-]*\d|\d", text or ""):
        sig.add(m.lower())
    for m in re.findall(r"\b[A-Z][a-zA-Z]{2,}\b", text or ""):
        sig.add(m.lower())
    for m in re.findall(r"\b[a-zA-Z]{6,}\b", text or ""):
        sig.add(m.lower())
    return sig


# ── 数据结构 ────────────────────────────────────────────────────────────

@dataclass
class MemoryChunk:
    id: str
    user_id: str
    session_id: str
    content: str
    ts: int
    tokens: List[str] = field(default_factory=list)
    signals: set = field(default_factory=set)


@dataclass
class AddResult:
    success: bool
    request_id: str
    user_id: str
    session_id: str
    chunks: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {"success": self.success, "request_id": self.request_id,
                "user_id": self.user_id, "session_id": self.session_id}


# ── 服务 ────────────────────────────────────────────────────────────────

class AMLMemoryService:
    """AML Add/Search 记忆服务（SQLite 持久化 + BM25 检索）。"""

    def __init__(self, db_path: Optional[str] = None,
                 chunk_window: Optional[int] = None,
                 return_neighbors: int = DEFAULT_RETURN_NEIGHBORS,
                 query_expansion: bool = True):
        """Args:
            chunk_window: 索引分块窗口（细粒度→排序准）。
            return_neighbors: 返回时向前后各扩展多少条相邻块（宽粒度→上下文全）。
            query_expansion: 是否启用**语料内共现查询扩展**（PMI 加权，纯统计、
                零依赖）。用于缓解“改写/主题关联”类漏召回。
        """
        self.db_path = str(Path(db_path or DEFAULT_DB).expanduser())
        self.chunk_window = int(chunk_window or CHUNK_WINDOW)
        self.return_neighbors = int(max(0, return_neighbors))
        self.query_expansion = bool(query_expansion)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()
        # 词法统计缓存（按 user 失效）
        self._df_cache: Dict[str, Dict[str, int]] = {}
        # 共现统计缓存：user -> (cooc, df, ndocs)；None 表示已判定不值得建
        self._cooc_cache: Dict[str, Any] = {}

    # ── 存储 ────────────────────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path, timeout=30)
        c.execute("PRAGMA journal_mode=WAL")
        return c

    @contextmanager
    def _db(self):
        """事务上下文：提交/回滚并**确保关闭**连接（Windows 下不关会锁文件）。"""
        c = self._conn()
        try:
            yield c
            c.commit()
        except Exception:
            try:
                c.rollback()
            except Exception:  # noqa: BLE001
                pass
            raise
        finally:
            try:
                c.close()
            except Exception:  # noqa: BLE001
                pass

    def _init_db(self) -> None:
        with self._lock, self._db() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS chunks (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    request_id TEXT,
                    content TEXT NOT NULL,
                    ts INTEGER,
                    seq INTEGER,
                    created_at INTEGER
                )""")
            c.execute("CREATE INDEX IF NOT EXISTS idx_chunks_user ON chunks(user_id)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_chunks_sess ON chunks(user_id, session_id)")

    # ── Add ─────────────────────────────────────────────────────────────

    def add(self, request_id: str, messages: Sequence[Dict[str, Any]],
            user_id: str, session_id: str) -> AddResult:
        """写入一批消息；分块后持久化，立即可检索。"""
        if not user_id or not session_id:
            raise ValueError("user_id and session_id are required")
        msgs = [m for m in (messages or []) if isinstance(m, dict)]
        if not msgs:
            return AddResult(True, request_id, user_id, session_id, 0)

        now = int(time.time() * 1000)
        rows: List[Tuple] = []
        seq = 0
        W = max(1, self.chunk_window)
        # 滑动窗口分块（步长 1，窗口 W）：既保上下文又保事实密度
        for i in range(0, max(1, len(msgs))):
            window = msgs[max(0, i - W + 1): i + 1]
            if not window:
                continue
            parts = []
            for m in window:
                role = str(m.get("role", "")).strip().lower() or "user"
                content = str(m.get("content", "") or "").strip()
                if not content:
                    continue
                ts = m.get("timestamp")
                stamp = ""
                if isinstance(ts, (int, float)) and ts > 0:
                    try:
                        stamp = time.strftime(" [%Y-%m-%d %H:%M]",
                                              time.localtime(ts / 1000.0))
                    except Exception:  # noqa: BLE001
                        stamp = ""
                parts.append(f"{role.capitalize()}{stamp}: {content}")
            if not parts:
                continue
            text = "\n".join(parts)
            ts_val = None
            for m in window:
                t = m.get("timestamp")
                if isinstance(t, (int, float)) and t > 0:
                    ts_val = int(t)
                    break
            cid = f"{user_id}:{session_id}:{seq}:{uuid.uuid4().hex[:8]}"
            rows.append((cid, user_id, session_id, request_id, text,
                         ts_val if ts_val is not None else now, seq, now))
            seq += 1
            if i >= len(msgs) - 1:
                break

        with self._lock, self._db() as c:
            c.executemany(
                "INSERT OR REPLACE INTO chunks "
                "(id,user_id,session_id,request_id,content,ts,seq,created_at) "
                "VALUES (?,?,?,?,?,?,?,?)", rows)
        self._df_cache.pop(user_id, None)
        self._cooc_cache.pop(user_id, None)
        logger.info("[AML] add user=%s session=%s msgs=%d chunks=%d",
                    user_id, session_id, len(msgs), len(rows))
        return AddResult(True, request_id, user_id, session_id, len(rows))

    # ── Search ──────────────────────────────────────────────────────────

    def search(self, query: str, user_id: str, top_k: int = 100,
               options: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
        """按 query 返回**记忆证据**（绝不生成答案）。"""
        if not user_id:
            raise ValueError("user_id is required")
        k = int(top_k or 100)
        with self._lock, self._db() as c:
            cur = c.execute(
                "SELECT id, user_id, session_id, content, ts, seq, created_at "
                "FROM chunks WHERE user_id = ? ORDER BY seq", (user_id,))
            raw = cur.fetchall()
        if not raw:
            return []

        chunks = [
            MemoryChunk(id=r[0], user_id=r[1], session_id=r[2], content=r[3],
                        ts=r[4] or 0)
            for r in raw
        ]
        for ch in chunks:
            ch.tokens = tokenize(ch.content)
            ch.signals = extract_signals(ch.content)

        q_tokens = tokenize(query)
        # 选择题把选项并入查询词（提高事实命中）
        if options:
            for o in options:
                q_tokens.extend(tokenize(str(o)))
        # 语料内共现扩展（PMI 加权，纯统计）：缓解改写/主题关联漏召回
        if self.query_expansion:
            q_tokens.extend(self._expand_query(query, user_id, chunks))
        q_signals = extract_signals(query)
        if options:
            for o in options:
                q_signals |= extract_signals(str(o))

        if not q_tokens and not q_signals:
            # 无有效查询词：返回最近记忆（仍有界）
            chunks.sort(key=lambda x: (x.ts, x.id), reverse=True)
            return [self._to_item(ch, 0.0) for ch in chunks[:k]]

        df = self._document_frequencies(user_id, chunks)
        n = max(1, len(chunks))
        avgdl = sum(len(ch.tokens) for ch in chunks) / n
        k1, b = 1.5, 0.75

        scored: List[Tuple[float, MemoryChunk]] = []
        for ch in chunks:
            tf: Dict[str, int] = {}
            for t in ch.tokens:
                tf[t] = tf.get(t, 0) + 1
            dl = max(1, len(ch.tokens))
            score = 0.0
            for q in set(q_tokens):
                f = tf.get(q, 0)
                if f == 0:
                    continue
                idf = math.log(1 + (n - df.get(q, 0) + 0.5) / (df.get(q, 0) + 0.5))
                score += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / avgdl))
            # 强信号加成（数字/专名/日期）
            if q_signals:
                hit = len(q_signals & ch.signals)
                if hit:
                    score += 2.0 * hit
            # 时间新近度微加成（同分时更新的优先）
            if score > 0:
                score += 1e-4 * (ch.ts / 1e13 if ch.ts else 0)
            if score > 0:
                scored.append((score, ch))

        if not scored:
            return []
        scored.sort(key=lambda t: (-t[0], -t[1].ts))
        top = scored[:k]
        if self.return_neighbors <= 0:
            return [self._to_item(ch, round(s, 6)) for s, ch in top]
        return self._expand_with_neighbors(top, raw, k)

    def _expand_with_neighbors(self, top, raw, k: int) -> List[Dict[str, Any]]:
        """返回时把每个命中块的相邻会话块合并进来（排序用细块，返回用宽窗）。

        只拼接**同一 session** 内的相邻块，不跨会话，避免拼出无关上下文。
        """
        # seq -> 原始行，按 session 分组
        by_sess: Dict[str, Dict[int, Any]] = {}
        for r in raw:
            by_sess.setdefault(r[2], {})[r[5]] = r
        out: List[Dict[str, Any]] = []
        merged_seq: set = set()
        R = self.return_neighbors
        for score, ch in top:
            rows = by_sess.get(ch.session_id, {})
            if ch.id in merged_seq:
                continue
            # 定位当前块在 session 内的 seq
            cur = next((s for s, r in rows.items() if r[0] == ch.id), None)
            if cur is None:
                out.append(self._to_item(ch, round(score, 6)))
                continue
            parts, newest = [], 0
            for s in range(cur - R, cur + R + 1):
                r = rows.get(s)
                if r is None:
                    continue
                if r[0] in merged_seq:
                    continue
                merged_seq.add(r[0])
                parts.append((s, r[3], r[4]))
                newest = max(newest, r[4] or 0)
            if not parts:
                out.append(self._to_item(ch, round(score, 6)))
                continue
            parts.sort()
            content = "\n".join(p[1] for p in parts)
            out.append({"id": ch.id, "content": content,
                        "score": round(score, 6), "created_at": newest})
        return out[:k]

    def _document_frequencies(self, user_id: str,
                              chunks: List[MemoryChunk]) -> Dict[str, int]:
        cached = self._df_cache.get(user_id)
        if cached is not None:
            return cached
        df: Dict[str, int] = {}
        for ch in chunks:
            for t in set(ch.tokens):
                df[t] = df.get(t, 0) + 1
        self._df_cache[user_id] = df
        return df

    # ── 语料内共现查询扩展（纯统计，零依赖） ────────────────────────────

    def _cooc_stats(self, user_id: str, chunks: List[MemoryChunk]):
        """构建（或取缓存）该用户的词共现统计。

        Returns:
            (cooc, df, ndocs) 或 None（语料过大 / 已判定不值得建）。

        统计在**句子级窗口**内做：同一句内共现的词才算相关，比整块共现更准。
        结果按 user 缓存，`add` 时失效；首查一次性成本，之后无开销。
        """
        if not self.query_expansion:
            return None
        if user_id in self._cooc_cache:
            return self._cooc_cache[user_id]

        budget = COOC_TOKEN_BUDGET
        spent = 0
        cooc: Dict[str, Dict[str, int]] = {}
        df: Dict[str, int] = {}
        ndocs = 0
        seen_sent: set = set()
        for ch in chunks:
            if spent > budget:
                self._cooc_cache[user_id] = None
                logger.info("[AML] cooc skipped for %s (budget exceeded)", user_id)
                return None
            for sent in re.split(r"[.!?\n\u3002\uff01\uff1f]+", ch.content):
                toks = tokenize(sent)
                if not toks:
                    continue
                # 重叠分块会让同一句出现多次 → 句子级去重，否则统计被重复计数
                # 扭曲（曾导致召回反而不如直接基于原始消息的原型）
                h = hash(tuple(toks))
                if h in seen_sent:
                    continue
                seen_sent.add(h)
                spent += len(toks)
                ndocs += 1
                uniq = sorted(set(toks))
                for w in uniq:
                    df[w] = df.get(w, 0) + 1
                for i, a in enumerate(uniq):
                    ca = cooc.get(a)
                    if ca is None:
                        ca = cooc[a] = {}
                    for b in uniq[i + 1:]:
                        ca[b] = ca.get(b, 0) + 1
                        cb = cooc.get(b)
                        if cb is None:
                            cb = cooc[b] = {}
                        cb[a] = cb.get(a, 0) + 1
        stats = (cooc, df, max(1, ndocs))
        self._cooc_cache[user_id] = stats
        logger.info("[AML] cooc built for %s: %d terms, %d sentences, %d tokens",
                    user_id, len(cooc), ndocs, spent)
        return stats

    def _expand_query(self, query: str, user_id: str,
                      chunks: List[MemoryChunk]) -> List[str]:
        """用 PMI 加权共现词扩展查询（缓解改写/主题关联漏召回）。"""
        stats = self._cooc_stats(user_id, chunks)
        if not stats:
            return []
        cooc, df, ndocs = stats
        qt = set(tokenize(query))
        cand: Dict[str, float] = {}
        for t in qt:
            for nb, c in cooc.get(t, {}).items():
                if c < EXPAND_MIN_COUNT or nb in qt:
                    continue
                pmi = math.log((c * ndocs) / max(1, df.get(t, 1) * df.get(nb, 1)) + 1e-9)
                if pmi > 0:
                    cand[nb] = cand.get(nb, 0.0) + pmi
        if not cand:
            return []
        ranked = sorted(cand.items(), key=lambda kv: -kv[1])[:EXPAND_TOP_N]
        return [w for w, _ in ranked]

    @staticmethod
    def _to_item(ch: MemoryChunk, score: float) -> Dict[str, Any]:
        return {"id": ch.id, "content": ch.content, "score": score,
                "created_at": ch.ts}

    # ── 运维 ────────────────────────────────────────────────────────────

    def stats(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        with self._lock, self._db() as c:
            if user_id:
                n = c.execute("SELECT COUNT(*) FROM chunks WHERE user_id=?",
                              (user_id,)).fetchone()[0]
                users = 1
            else:
                n = c.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
                users = c.execute(
                    "SELECT COUNT(DISTINCT user_id) FROM chunks").fetchone()[0]
        return {"chunks": n, "users": users, "db": self.db_path}

    def purge_user(self, user_id: str) -> int:
        """按用户删除全部记忆（用于 30 天内删除的合规要求）。"""
        with self._lock, self._db() as c:
            cur = c.execute("DELETE FROM chunks WHERE user_id=?", (user_id,))
            deleted = cur.rowcount
        self._df_cache.pop(user_id, None)
        self._cooc_cache.pop(user_id, None)
        return deleted
