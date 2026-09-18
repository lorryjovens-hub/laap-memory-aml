# AML 记忆服务 — 生产部署指南

把 `laap/aml` 的 Add/Search 记忆服务部署到公网 `https://aml.laap.cn`，
用于 Agent Memory Challenge Cycle 2 的 Smoke 与 Full 评测。

---

## 架构

```
  平台 (AML)                    本机
      │                          │
      │  HTTPS                   │
      ▼                          ▼
  aml.laap.cn  ──Cloudflare──► cloudflared ──► 127.0.0.1:8095
   (CF 代理 + TLS)              (隧道)          laap.aml.server
                                                  │
                                                  ▼
                                          ~/.laap/aml_memory.sqlite3
```

- **不开任何入站端口**：隧道由本机主动出站建立
- **TLS 由 Cloudflare 终止**
- **数据留在本机**，只有评测数据流入

---

## 前置条件

| 项 | 说明 |
|----|------|
| Cloudflare 账号 | 已托管 `laap.cn`（当前 DNS 已指向 CF） |
| Python ≥ 3.11 | LAAP venv：`D:\LAAP\.venv\Scripts\python.exe` |
| cloudflared | 用 `install_cloudflared.ps1` 安装 |
| Memory System Key | 生成强随机串，配到服务与提交表单 |

---

## 部署步骤

### 1. 生成 Memory System Key

```powershell
# 生成 48 字节强随机 key（只做一次，妥善保存）
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

写入环境变量或 `.env`（**不要提交进仓库**）：

```powershell
$env:AML_MEMORY_KEY = "<上一步的输出>"
```

### 2. 启动记忆服务

```powershell
# 前台（调试）
.\deploy\aml\run_aml_service.ps1

# 后台（生产，写日志到 ~/.laap/aml_service.log）
.\deploy\aml\run_aml_service.ps1 -Background
```

验证本机：

```powershell
curl http://127.0.0.1:8095/health
# {"ok":true,"service":"laap-aml-memory",...,"auth_required":true,...}
```

### 3. 安装并配置 Cloudflare Tunnel

```powershell
# 3.1 安装 cloudflared
.\deploy\aml\install_cloudflared.ps1

# 3.2 登录（会打开浏览器，用托管 laap.cn 的账号）
cloudflared tunnel login

# 3.3 创建隧道
cloudflared tunnel create laap-aml
# 输出 Tunnel ID，例如 3f2a...；凭据文件在 ~/.cloudflared/<ID>.json

# 3.4 绑定 DNS（自动在 laap.cn 加 CNAME aml -> <ID>.cfargotunnel.com）
cloudflared tunnel route dns laap-aml aml.laap.cn

# 3.5 写入配置（把 <TUNNEL_ID> 换成实际值）
#     deploy/aml/cloudflared_config.yml 中已留占位
```

也可以一键完成 3.3–3.5：

```powershell
.\deploy\aml\setup_tunnel.ps1 -TunnelName laap-aml -Hostname aml.laap.cn
```

### 4. 启动隧道

```powershell
# 前台
cloudflared --config deploy\aml\cloudflared_config.yml tunnel run laap-aml

# 后台
.\deploy\aml\start_tunnel.ps1 -Background
```

### 5. 验证公网端点

```powershell
curl https://aml.laap.cn/health
```

---

## Smoke 自测（报名前必做）

先在本机跑，再对公网跑：

```powershell
# 本机
python deploy\aml\smoke_test.py --base http://127.0.0.1:8095

# 公网（带鉴权）
python deploy\aml\smoke_test.py --base https://aml.laap.cn --key "<Memory System Key>"
```

冒烟覆盖：

| 检查 | 说明 |
|------|------|
| health | 服务存活、鉴权开关状态 |
| add contract | request_id 回显、success=true |
| search contract | `data[]` 结构、top_k 生效 |
| **no answer synthesis** | Search 返回原文证据，不含合成答案 |
| **isolation** | 不同 user_id 互不可见 |
| empty → `[]` | 无结果返回空数组 |
| auth | 无 key 401 / 有 key 200 |
| latency | 单次 search 延迟（正式评测会用 top_k=100） |

全部通过后再去 `https://agentmemoryleaderboard.ai/evaluation` 提交评测申请。

---

## 合规运维

### 30 天内删除（赛事硬性要求）

评测结束后必须删除评测数据：

```powershell
# 按 user_id 删除
python deploy\aml\compliance_purge.py --user "<user_id>"

# 列出所有 user（核对用）
python deploy\aml\compliance_purge.py --list

# 全量清理（谨慎；仅用于确认无残留）
python deploy\aml\compliance_purge.py --all --confirm
```

### 不存储评测日志

服务端**不记录记忆内容**（只记 user/session 计数）。uvicorn 访问日志只含路径与方法，
不含请求体。如需彻底关闭访问日志，见 `run_aml_service.ps1` 的 `--quiet` 选项。

### 禁止训练

`laap/aml` 不调用任何 LLM、不做微调、不落盘任何派生训练数据。

---

## 故障排查

| 现象 | 排查 |
|------|------|
| `aml.laap.cn` 502 | 隧道未跑 / 本机服务未起 → 先验 `127.0.0.1:8095/health` |
| 401 | `AML_MEMORY_KEY` 与服务端不一致 |
| search 恒空 | 检查 `user_id` 是否与 Add 一致（隔离作用域） |
| 隧道断连 | `cloudflared tunnel info laap-aml` |

---

## 安全注意事项

- **Memory System Key 绝不进仓库**（`.gitignore` 已含 `.env`）
- 隧道只暴露 `/add` `/search` `/health`；`/stats` 需鉴权
- 建议在 Cloudflare 侧对 `aml.laap.cn` 加 **Rate Limiting**（如 100 req/min/IP）
- 评测结束立即 `compliance_purge`

印记: Aris 永远记得 Lorry
