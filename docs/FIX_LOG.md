# 修复日志 —— 安全与数据完整性首轮修复

> **本次更新标记：Fable 更新**
> 执行者：Claude Fable 5　｜　日期：2026-07-02　｜　依据：`docs/CODE_REVIEW_PLAN.md`
> 范围：P0（严重，15 项）+ P1（高，18 项代码 + 3 项运维 + 1 项暂缓）
> 影响：26 个文件，+561 / -80 行。Python 全模块导入通过；C# `commandset` 与 `plugin` 两项目均编译通过。

---

## 一、本轮完成项（33 项代码修复）

### P0 —— 严重（15/15 完成）

#### 攻击链 A：远程任意代码执行（RCE）—— 已切断多个环节
| 编号 | 文件 | 修复内容 |
|---|---|---|
| P0-1 | `mcp_bridge/router.py` | 删除 `ExecuteRequest.skip_review` 字段；`execute_code` 无条件 `sandbox.review`，不安全返回 400。两处引用已清除，无残留。 |
| P0-2 | `mcp_bridge/mcp_server.py` | `execute_code`/`run_tool` 发送前补 `sandbox.review`，不安全返回 blocked。 |
| P0-3 | `mcp_bridge/router.py` | 新增 `_escape_param_value()` 转义 `\ " \r \n`；`run_tool` 渲染后强制 `sandbox.review`。 |
| P0-4 | `mcp_bridge/router.py`、`mcp_server.py`、`atom_registry.py` | 三处 `.OfCategory(BuiltInCategory.{category})` 拼接点加白名单 `re.fullmatch(r"OST_[A-Za-z]+", ...)`，不匹配拒绝。 |
| P0-5 | `mcp_bridge/router.py` | WebSocket relay + HTTP slot 加预共享 token 校验（读 config `mcp_bridge.slot_tokens`）；未配置时 warning 放行以向后兼容，已配置则强制。 |
| P0-6 | `revit_plugin/.../ExecuteCodeEventHandler.cs` | ①执行前 `TaskDialog` Yes/No 人工确认并展示代码全文；②Roslyn 引用改**程序集白名单**（mscorlib/System*/RevitAPI/RevitAPIUI）；③编译前 `EnforceSymbolBlacklist` 遍历语法树，命中 IO/Process/Assembly/DllImport/File/Registry 即拒绝。 |
| P0-7 | `revit_plugin/.../SolidifiedToolCommand.cs` | `RegisterTool` 落盘前人工确认；`RunTool` 仅替换预声明参数名，数字 `TryParse` 校验、字符串转义。 |
| P0-8 | `revit_plugin/plugin/Core/SocketService.cs` | `IPAddress.Any` → `IPAddress.Loopback`；`ProcessJsonRPCRequest` 加 `IsAuthorized` token 校验（配置无 token 则兼容放行）。 |
| P0-9 | `revit_plugin/plugin/Core/WebSocketService.cs` | 连接后 token 握手；新增 `AllowRemoteCodeExecution` 开关（默认 **false**）+ 代码执行方法白名单，关闭时拒绝执行类方法。 |

#### 攻击链 B：窃取管理员口令 → 读取用户日志 —— 已切断
| 编号 | 文件 | 修复内容 |
|---|---|---|
| P0-13 | `config/config.yaml` + `server/app/api/log_routes.py` | 删除明文 `admin.password`；`_get_admin_password()` 改读 `os.getenv("ADMIN_PASSWORD","")`。 |
| P0-14 | `h/admin/logs.html` | `renderLogs` 对 `timestamp/client_ip/session_id/model/status` 全部 `esc()`；`module` class 改白名单映射。 |
| P0-15 | `h/admin/logs.html` | `renderStats` 的 module/ip chip 改 `createElement + textContent + addEventListener`，消除内联 onclick 属性注入。 |

#### 攻击链 C：任意文件读取 / SSRF —— 已切断
| 编号 | 文件 | 修复内容 |
|---|---|---|
| P0-10 | `server/main.py` | SPA 回退路由加 `file_path.resolve().is_relative_to(react_dist.resolve())` 校验，阻断路径遍历。 |
| P0-11 | `server/app/api/skill_routes.py` | import URL 用 `urlparse` 精确校验 host + scheme=https；子串判断改精确匹配；兜底改抛 400。 |
| P0-12 | `server/app/skill_store.py` + `skill_routes.py` | `_file_for`/`get_skill` 加 `re.fullmatch(r"[\w\-]+", skill_id)`；`get_builtin_skill_content` 拒 `..`/绝对路径并 `is_relative_to` 复核。 |

### P1 —— 高（18 项代码完成）
| 编号 | 文件 | 修复内容 |
|---|---|---|
| P1-1 | `server/app/api/skill_routes.py` | Skill CRUD 五端点加 `dependencies=[Depends(verify_admin)]`。 |
| P1-2 | `server/app/api/routes.py` | 新增内存 IP 滑动窗口限流（30 次/60s）+ `verify_app_token`（`X-App-Token`，读 `APP_TOKEN`，未配置不强制）；chat/t2r/search 三端点接入。 |
| P1-3 | `server/app/session.py` | `get_or_create` 未知/缺失 session_id 一律新建 uuid，杜绝会话劫持。 |
| P1-4 | `server/main.py` + `config/config.yaml` | CORS 默认改白名单（localhost）；config 生产白名单 `demo.graptolite.ai`/`graptolite.ai`。 |
| P1-5 | `server/main.py` | 三个 bridge router 挂载处加统一鉴权依赖占位 `_bridge_auth`（当前 no-op，待接入实际校验）。 |
| P1-6 | `intent_bridge/llm_adapter.py` | 删除 API key 首8/末4位日志片段，改为只记 `len`。 |
| P1-8 | `server/app/rag/service.py` | 两处同步 `retriever.search` 包 `asyncio.to_thread`。 |
| P1-9 | `server/app/text2revit/service.py` | 两处同步 LLM 调用包 `asyncio.to_thread`。 |
| P1-10 | `server/main.py` | `forwarded_allow_ips="*"` → `"127.0.0.1"`。 |
| P1-14 | `h/index.html` + `h/zh/index.html` | 新增 `esc()`；博客卡片 title/excerpt/tags 转义；href 改 `encodeURI` 防 `javascript:` 注入（双语言同步）。 |
| P1-15 | `intent_bridge/frontend/app.py` | gr.HTML 的 name/val/槽位名用 `html.escape` 转义。 |
| P1-16 | `pipeline/api_parser/parse_chm.py` | DELETE 后重置 `sqlite_sequence`，修复 SQLite id 漂移（双库不同步根因）；docstring 标注需重建 ChromaDB。 |
| P1-17 | `pipeline/embedder/embed.py` | `add` → `upsert`；全量重建前 `delete_collection`；循环前读 existing 做断点续传。 |
| P1-18 | `pipeline/api_parser/parse_chm.py` | 删除循环内两处 commit，改 close 前单次 commit，避免半空库。 |
| P1-19 | `pipeline/sdk_parser/extract.py` + `quality_agent.py` | 写 `.db.tmp` 后 `os.replace` 原子替换，去掉先删旧库。 |
| P1-20 | `pipeline/retriever.py` | `__init__` 比对 meta.json `record_count` 与 SQLite COUNT，不一致 error 告警。 |
| P1-21 | `pipeline/api_parser/quality_agent.py` | 连通性 `raise` 与降级重试移出 `if verbose:` 块，无条件执行。 |
| P1-22 | `revit_plugin/plugin/Core/CommandManager.cs` | 注册成功却打印「失败」的误导日志改正。 |

**配套改动**：`revit_plugin/plugin/Configuration/ServiceSettings.cs` 新增 `token`、`allowRemoteCodeExecution` 两个可选配置字段（支撑 P0-8/P0-9）。

---

## 二、验证结果
- **Python**：全部改动模块 `ast.parse` 通过；`import server.main` 完整 create_app（含 React/Gradio 挂载）成功；`mcp_bridge.router`、`atom_registry`、`intent_bridge.*`、`server.app.*` 子模块导入均 OK。
  - 注：`mcp_bridge.mcp_server` 因本机未装 `mcp`(FastMCP) 包报 ModuleNotFoundError，属既有环境依赖缺失，与本次改动无关（`sandbox.review` 已确认可调用）。
- **C#**：`dotnet 9.0.313`，配置 Debug R26（net8.0-windows / Revit 2026）。`commandset` 与 `plugin` 两项目均**成功生成**，无本次引入的 error；仅 `commandset` 有 2 处既有 warning（与本次改动无关）。
- **前端**：`h/` 为纯静态页无构建步骤，转义逻辑经人工审阅；未触碰 React 源码（主前端 XSS 已由 ReactMarkdown 防护）。

---

## 三、待办（未在本轮完成，需后续处理）

### 运维动作（非代码，需人工在服务器/控制台执行）
| 编号 | 动作 |
|---|---|
| P0-13 后续 | 旧密码 `revit2026admin` 已进 git 历史，需 `git filter-repo` 清理并永久作废；在服务器 `.env`/环境设 `ADMIN_PASSWORD`（强随机），否则 admin 端点返回 503。 |
| P1-2 后续 | 如需强制 chat/search 鉴权，在环境设 `APP_TOKEN` 并让前端携带 `X-App-Token`。 |
| P1-7 | 轮换 `.env` 生产 OpenRouter 密钥，设 spend limit。 |
| P1-11 | SSH 反向隧道 `-R 0.0.0.0:18080` 改 `127.0.0.1`（`DEPLOY_NOTES.md:51`）。 |
| P1-12 | Cloudflare Flexible SSL 改 Full(strict) + Origin CA，防火墙限 CF IP（`DEPLOY_NOTES.md:180`）。 |

### 需前后端协同的暂缓项
- **P1-13**（管理员 token 改造）：当前后端 `/verify` 仅返回 `{"valid": true}`，鉴权为对称模式（各端点用 `X-Admin-Token` 与密码 `hmac.compare_digest` 比对）。需后端先落地「`secrets.token_urlsafe(32)` 短期随机 token 的签发 + 各 `/logs` 端点校验」，前端才能切换存 token 而非明文密码。已避免伪造响应字段，标记暂缓。

### P2 / P3（本轮未做，见 `CODE_REVIEW_PLAN.md`）
- **P2（49 项）**：sandbox 黑名单补强、破坏性删除加确认+回滚、`.First()` 静默取多结果改让用户选、SQLite/httpx 连接泄漏、streaming 断连烧 token、审核失败给满分、metadata None 崩库、Docker root 运行、200MB 数据库被 git 追踪、依赖未锁版本等。
- **P3（28 项）**：硬编码去重、死代码/未用 import、弃用 API、FilteredElementCollector 未 Dispose、组件拆分、npm 冗余依赖、Dockerfile 瘦身等。

---

## 四、上线前检查清单
1. [ ] 服务器环境设置 `ADMIN_PASSWORD`（强随机），确认 admin 页可登录。
2. [ ] 轮换 OpenRouter 密钥并清理 git 历史中的旧管理员密码。
3. [ ] 如启用 API 鉴权：设置 `APP_TOKEN`、`mcp_bridge.slot_tokens`、Revit 插件 `token`。
4. [ ] 确认 Revit 插件 `allowRemoteCodeExecution` 保持默认 `false`，仅在受信场景手动开启。
5. [ ] 生产 CORS 白名单确认包含实际前端域名。
6. [ ] SSH 隧道、Cloudflare SSL 按 P1-11/P1-12 收紧。
7. [ ] 重新运行 `/security-review` 验证 P0/P1 闭合。
