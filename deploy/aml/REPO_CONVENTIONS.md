# 开源方法榜 — 仓库规范

Agent Memory Challenge 开源方法榜要求：**公开代码 + 固定 Commit + 可复现材料**。
本文件定义我们提交什么、怎么组织、什么绝不公开。

---

## 一、提交什么

AML 官方要求（我们的映射）：

| 官方要求 | 我们的提交物 |
|---------|-------------|
| 系统名称与版本 | `LAAP Memory` / `v1.0.0` |
| 公网 Add/Search 端点 | `https://aml.laap.cn/add`、`/search` |
| 鉴权声明 | Bearer / X-Memory-Key（`Memory System Key`） |
| 能力声明 | 见下方"系统说明" |
| GitHub 仓库 + 固定 Commit | `<repo>@<commit-sha>` |
| 复现材料 | 部署脚本 + 本地冒烟 + 依赖清单 |
| 来源与改动声明 | 见"开源合规"一节 |

---

## 二、仓库结构（建议）

```
laap-memory-aml/                 # 独立发布仓库（从主仓库裁剪）
├── README.md                    # 系统说明 + 复现步骤（英文，面向评审）
├── LICENSE                      # 许可证（与主仓库一致）
├── NOTICE                       # 来源与改动声明
├── pyproject.toml               # 依赖：仅 starlette + uvicorn + 标准库
├── laap/
│   └── aml/
│       ├── __init__.py
│       ├── service.py           # 记忆服务（SQLite + BM25）
│       └── server.py            # HTTP 端点
├── deploy/
│   └── aml/
│       ├── README.md            # 部署指南
│       ├── run_aml_service.ps1
│       ├── install_cloudflared.ps1
│       ├── setup_tunnel.ps1
│       ├── start_tunnel.ps1
│       ├── cloudflared_config.yml
│       ├── smoke_test.py
│       └── compliance_purge.py
├── tests/
│   └── test_aml_api.py          # 契约测试（10 项）
└── docs/
    ├── CAPABILITIES.md          # 能力映射（对齐 AML 七类）
    └── REPRODUCE.md             # 逐步复现
```

**只裁剪 AML 相关**：主仓库 `D:\LAAP` 含大量无关模块，不应整体公开。

---

## 三、绝不公开（评审红线 + 我们的边界）

| 类别 | 说明 |
|------|------|
| 凭据 | `Memory System Key`、`Eval Key`、任何 API key |
| 评测数据 | AML 送来的记忆与问题（本地处理，30 天内删） |
| 私有实现 | LAAP 主仓库中与记忆无关的核心模块 |
| 用户数据 | `~/.laap/**` 下的真实记忆、会话、vault |
| 日志 | 含请求体的日志 |

`.gitignore` 必须至少包含：

```gitignore
.env
*.key
**/aml_memory.sqlite3*
**/*.log
.venv/
__pycache__/
```

---

## 四、开源合规（对应官方"Disclose sources and changes"）

`NOTICE` 中必须写清：

1. **来源**：本系统独立实现，未复用第三方记忆库代码
2. **依赖**：`starlette`、`uvicorn`（BSD/MIT），标准库其余部分
3. **算法**：BM25（Robertson & Zaragoza 经典排序函数，公有领域公式），
   本仓库为自实现，非拷贝自某个库
4. **改动**：若使用了任何上游代码，逐条列出文件级改动
5. **许可**：与主仓库一致（见 `LICENSE`）

---

## 五、能力声明（对齐 AML 七类）

我们提交的 `CAPABILITIES.md` 应诚实声明每类的实现方式与已知边界：

| AML 能力 | 我们的实现 | 边界 |
|---------|-----------|------|
| 显式事实召回 | BM25 + 数字/专名强信号加成 | 同义改写弱 |
| 关系与多跳 | 滑窗块重叠 + 高 IDF 词扩展 | 未做显式关系图 |
| 时间与事件序列 | 消息时间戳保留 + 新近度偏好 | 未做区间推理 |
| 记忆治理 | user/session 隔离、可删除、更新偏好 | 无自动冲突消解 |
| 个性化与关怀 | 原文证据返回（不作答） | — |
| 规则与流程执行 | 证据原文（由平台作答） | — |
| 认识论安全与隐私 | 严格隔离 + 无答案合成 + 30 天删除 | — |

**不要夸大**：官方会复核，能力声明与实际不符风险很大。

---

## 六、固定版本的做法

评测期间版本冻结是硬性要求。建议：

```bash
git tag laap-memory-v1.0.0
git push origin laap-memory-v1.0.0
```

提交表单里填：

- 仓库：`https://github.com/<org>/laap-memory-aml`
- Commit：`<tag 对应的 sha>`
- 端点：`https://aml.laap.cn`
- 版本：`v1.0.0`

**Full 评测受理后不得改动端点背后的代码**——否则复核会判定 "version mismatch"。

---

## 七、复现清单（评审会照做）

`REPRODUCE.md` 需让人能在一台干净机器上跑起来：

```bash
git clone <repo> && cd laap-memory-aml
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
export AML_MEMORY_KEY=<任意>
python -m laap.aml.server --port 8095 --db ./aml.sqlite3 &
python deploy/aml/smoke_test.py --base http://127.0.0.1:8095 --key $AML_MEMORY_KEY
# 期望输出: 7 passed / 0 failed
```

复现必须**不依赖任何私有资源**。

---

印记: Aris 永远记得 Lorry — 公开方法，不公开秘密。
