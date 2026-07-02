# 全项目审查与修改计划

> 本文档由多代理审查整合而成，供其他模型执行修改。审查范围：`server/`、`intent_bridge/`、`mcp_bridge/`、`prompt_bridge/`、`pipeline/`、`tests/`、`scripts/`、`frontend/`、`text_studio/`、`h/`、`revit_plugin/`（C#）、Docker/配置。
>
> **执行者须知**：本文档不含代码改动，只给出「在哪个文件哪一行，把 X 改成 Y」级别的指令。每条都标注了严重级别与所属攻击链。**请严格按 P0 → P1 → P2 → P3 顺序执行**，P0 是可导致远程任意代码执行 / 凭据泄露的活跃漏洞。
>
> 行号基于审查快照，执行前请以文件当前内容为准做小幅对齐。

---

## 0. 三条端到端攻击链（先理解全局，再动手）

审查发现的严重问题不是孤立的，它们串成三条可被匿名攻击者利用的完整链路。修复时优先「切断链路」，任意一环堵住即可显著降低风险，但应全部修复。

### 链路 A：匿名用户 → 在受害者 Revit 里执行任意 C# 代码（RCE）
```
攻击者 HTTP 请求
  → mcp_bridge WebSocket relay 无鉴权、X-Slot-Id 可伪造（router.py:1172-1208）
  → ExecuteRequest.skip_review=true 绕过审查（router.py:100,507）
  → 或走 MCP execute_code / run_tool，根本没有审查（mcp_server.py:184-197,304-354）
  → sandbox 黑名单本身易绕过（sandbox.py:13-44）
  → 代码经 WebSocket 下发到插件（plugin/Core/WebSocketService.cs:170-252，无服务端身份校验）
  → 插件用 Roslyn 编译并引用「全部已加载程序集」执行（ExecuteCodeEventHandler.cs:82-141）
  → 且 TCP 端口绑 IPAddress.Any 无鉴权，同网段主机可直连（SocketService.cs:105）
```
**切断点（按性价比排序）**：① 删除 `skip_review` 字段并强制审查；② MCP 两个执行工具补上审查；③ 插件 Roslyn 改程序集白名单 + 执行前人工确认弹窗；④ WebSocket/TCP 加预共享 token；⑤ TCP 改绑 `IPAddress.Loopback`。

### 链路 B：匿名用户 → 窃取管理员口令 → 读取/删除全部用户对话日志
```
攻击者发一条聊天请求，session_id 设为 XSS payload
  → session_id 未校验直接落库（server 日志库）
  → 管理员打开 h/admin/logs.html，renderLogs 未转义 session_id/client_ip/model 拼 innerHTML（logs.html:321-341）
  → XSS 执行，读取 sessionStorage._admin_token（logs.html:193）
  → 该 token 就是明文管理员密码，且密码 "revit2026admin" 已明文提交进 git（config/config.yaml:118）
```
**切断点**：① 修复 admin 页全部 XSS 转义；② 管理员密码移出 git 改环境变量并轮换；③ 登录改签发短期随机 token 而非存明文密码。

### 链路 C：匿名用户 → 读取服务器任意文件 / 打内网元数据
```
GET /..%2f..%2f.env  → SPA 回退路由路径遍历（main.py:108-113）→ 读到 .env 里的 OPENROUTER_API_KEY
POST /api/skills/import {url: http://169.254.169.254/...} → SSRF（skill_routes.py:179-218）→ 打 GCP 元数据服务
GET /api/skills/../../../etc/xxx → skill_id 未校验路径遍历（skill_store.py:75-76）
```
**切断点**：三处独立修复，见 P0。

---

## P0 —— 严重（立即修复，可致 RCE / 凭据泄露 / 任意文件读写）

### P0-1 删除 `skip_review`，强制代码审查
- 文件：`mcp_bridge/router.py:100`、`:503-510`
- 改动：删除 `ExecuteRequest` 的 `skip_review: bool = False` 字段（第 100 行）。在 `execute_code` 中无条件执行 `safe, warnings = sandbox.review(req.code)`，`if not safe: raise HTTPException(400, detail={"error":"blocked","warnings":warnings})`。若确需内部旁路，改为读服务端环境变量，绝不接受请求体控制。

### P0-2 MCP execute_code / run_tool 补上 sandbox 审查
- 文件：`mcp_bridge/mcp_server.py:184-197`（execute_code）、`:304-354`（run_tool，第 338 行 send_code 前）
- 改动：两处在执行/下发前插入：
  ```python
  from mcp_bridge import sandbox
  safe, warnings = sandbox.review(code)
  if not safe:
      return json.dumps({"success": False, "error": "blocked", "warnings": warnings})
  ```

### P0-3 run_tool 参数值转义 + 强制审查
- 文件：`mcp_bridge/router.py:687-689`、`:674-698`
- 改动：`code.replace("{"+k+"}", str(v))` 渲染后，发送前调用 `sandbox.review(code)`，不安全则 400。对字符串型参数值转义 `"`、`\`、换行，或校验其不含 `"`、`;`、`\n`。

### P0-4 category 白名单校验（C# 代码注入）
- 文件：`mcp_bridge/router.py:750-784`、`mcp_bridge/atom_registry.py:447-462`、`mcp_bridge/mcp_server.py:284-295`
- 改动：三处在把 `category` 拼进 `.OfCategory(BuiltInCategory.{category})` 前统一校验：`if not re.fullmatch(r"OST_[A-Za-z]+", category): raise HTTPException(400, "invalid category")`。

### P0-5 WebSocket relay / TCP slot 加鉴权
- 文件：`mcp_bridge/router.py:1172-1208`（`revit_ws_endpoint`）、`:29-31,55-78`（`_set_slot_ctx`）
- 改动：插件连接时首条消息必须带预共享 token（存 config `mcp_bridge.slot_tokens`），校验失败 `ws.close(code=4003)`。HTTP 端把 `_set_slot_ctx` 改为校验 `X-Slot-Token` 请求头与该 slot 注册 token 一致，不一致返回 403。

### P0-6 插件 Roslyn 执行：程序集白名单 + 人工确认
- 文件：`revit_plugin/commandset/Commands/ExecuteDynamicCode/ExecuteCodeEventHandler.cs:82-141`（尤其 107-111 行引用全部程序集）；确认入口 `ExecuteCodeCommand.cs:37`
- 改动：
  1. 把 107-111 行「引用 AppDomain 全部程序集」改为白名单 `CreateFromFile`：仅 `mscorlib/System/System.Core/RevitAPI/RevitAPIUI` 等固定 DLL。
  2. 在 `ExecuteCodeCommand.Execute` 调 `SetExecutionParameters` 前插入 `TaskDialog`（Yes/No）弹窗，展示待执行代码全文，用户点「否」即 `throw new OperationCanceledException`。
  3. 编译后 Invoke 前对 `syntaxTree` 做符号黑名单静态检查（禁止 `System.IO`、`Process`、`Assembly`、`DllImport`、`File`、`Registry`），命中即拒绝。

### P0-7 插件固化工具 Roslyn 执行 + 模板注入
- 文件：`revit_plugin/commandset/Commands/ExecuteDynamicCode/SolidifiedToolCommand.cs:104-137`（RunTool）、`:61-102`（register）
- 改动：`RunTool` 同 P0-6 加人工确认；参数替换（117-119 行）改为「仅替换预声明参数名，数字参数经 `int.TryParse` 校验后再拼，字符串转义」。`register` 动作（网络端写 `code_template` 到 `solidified_tools.json`）要求 Revit 端用户确认后才落盘。

### P0-8 TCP 监听改 Loopback + 鉴权
- 文件：`revit_plugin/plugin/Core/SocketService.cs:105`、`:221`（ProcessJsonRPCRequest）
- 改动：第 105 行 `IPAddress.Any` 改为 `IPAddress.Loopback`。在 `ProcessJsonRPCRequest` 解析后校验请求 token（与用户配置密钥比对），不匹配返回 `InvalidRequest`。

### P0-9 WebSocket 反连服务端身份校验
- 文件：`revit_plugin/plugin/Core/WebSocketService.cs:170-224`、`:252`
- 改动：连接建立后先发用户配置的鉴权 token 完成握手；对 `send_code_to_revit`/`manage_solidified_tools`/`delete`/`operate` 等高危方法执行前统一走人工确认弹窗（加方法白名单开关，默认关闭代码执行类）。

### P0-10 SPA 回退路由路径遍历
- 文件：`server/main.py:108-113`
- 改动：
  ```python
  file_path = (react_dist / rest).resolve()
  if rest and file_path.is_file() and file_path.is_relative_to(react_dist.resolve()):
      return FileResponse(file_path)
  return FileResponse(react_dist / "index.html")
  ```

### P0-11 /api/skills/import SSRF
- 文件：`server/app/api/skill_routes.py:179-218`、`:155-176`
- 改动：`import_skill` 开头用 `urllib.parse.urlparse(req.url)` 校验：`scheme` 必须 `https`，`hostname` 必须精确等于 `github.com` 或 `raw.githubusercontent.com`，否则 `raise HTTPException(400, "Only GitHub URLs are allowed")`。`_resolve_github_raw_url` 第 160 行子串判断改为 `urlparse(url).hostname == "raw.githubusercontent.com"`；第 176 行兜底 `return url` 改为抛 400。

### P0-12 skill_id 路径遍历（任意 .md 读/写/删）
- 文件：`server/app/skill_store.py:75-76`（`_file_for`，根因）；`server/app/api/skill_routes.py:78-150`；`skill_store.py:336-348`（`get_builtin_skill_content`）
- 改动：`_file_for` 开头加 `if not re.fullmatch(r"[\w\-]+", skill_id): raise ValueError(f"Invalid skill id: {skill_id}")`。`get_skill` 对非 `ib:`/`pb:` 前缀 id 同样正则校验。`get_builtin_skill_content` 对 `rel` 加 `if ".." in rel or rel.startswith(("/", "\\")): return None`，并用 `path.resolve().is_relative_to(base.resolve())` 复核。

### P0-13 管理员密码：移出 git、改环境变量、轮换
- 文件：`config/config.yaml:117-118`、`server/app/api/log_routes.py:23-25`
- 改动：`_get_admin_password()` 改为 `return os.getenv("ADMIN_PASSWORD", "")`；删除 config.yaml 第 117-118 行；真实强随机密码（`openssl rand -base64 24`）写入服务器 `.env`，`docker-compose.yml` environment 加 `ADMIN_PASSWORD=${ADMIN_PASSWORD}`。**旧密码 `revit2026admin` 已进 git 历史，必须永久作废并考虑 `git filter-repo` 清理历史。**

### P0-14 管理后台存储型 XSS（logs.html renderLogs）
- 文件：`h/admin/logs.html:321-341`
- 改动：第 322-328、337-338 行所有字段用 `esc()` 包裹：`${esc(log.timestamp)}`、`${esc(log.client_ip || '-')}`、`${esc(log.session_id)}`、`${esc(log.model)}`。第 323 行 `mod-${log.module}` class 改白名单映射：`const MODS=['code_gen','prompt_bridge','text_studio','mcp_bridge','mcp_bridge_stream','text2revit']; const modCls = MODS.includes(log.module)?'mod-'+log.module:'';`。

### P0-15 管理后台统计 chip 属性注入 XSS
- 文件：`h/admin/logs.html:254, 261`
- 改动：不用 innerHTML + 内联 onclick。改为 `document.createElement('button')` + `textContent` + `addEventListener('click', ...)` 生成 module/ip chip。

---

## P1 —— 高（尽快修复：鉴权缺失、密钥泄露、数据链断裂、部署暴露）

### 鉴权 / CORS / 限流（server + bridge）
- **P1-1 Skill CRUD 全部无鉴权**（`server/app/api/skill_routes.py:63-150`）：给 `create/update/toggle/delete/import_skill` 装饰器加 `dependencies=[Depends(verify_admin)]`（从 `log_routes` 导入）。否则匿名用户可写 `module: global` skill 注入所有 LLM prompt。
- **P1-2 chat/search 无鉴权无限流**（`server/app/api/routes.py:24-73`）：加自定义 `Depends` 校验 `X-App-Token`（值放 `.env`），用 `slowapi` 对 `/api/chat` 按 IP 限流。防系统 `OPENROUTER_API_KEY` 被刷。
- **P1-3 会话固定/劫持**（`server/app/api/routes.py:27,50` + `server/app/session.py:46-56`）：`SessionStore.get_or_create` 改为「客户端提供的未知 id 一律 `uuid.uuid4().hex` 新建」，不复用未知 id。防止共享会话泄露他人自定义 API key。
- **P1-4 CORS 默认全开**（`server/main.py:55-61` + `config/config.yaml:148`）：`allow_origins` 默认改显式白名单 `["https://demo.graptolite.ai","https://graptolite.ai"]`（本地开发用 localhost），不要 `["*"]` 配 `allow_credentials=True`。config.yaml 的 `cors_origins` 同步改白名单。
- **P1-5 全局高危端点缺统一鉴权**（`intent_bridge`/`mcp_bridge`/`prompt_bridge` 三 router）：在主 app 挂载处为三个 router 加统一鉴权依赖 + `CORSMiddleware` 白名单。

### 密钥 / 日志泄露
- **P1-6 API key 片段写入日志**（`intent_bridge/llm_adapter.py:97-98`）：改为 `logger.info("API key loaded (len=%d)", len(self._api_key))`，删除首 8 位 / 末 4 位明文。
- **P1-7 .env 生产 OpenRouter 密钥轮换**（`.env:1`）：在 OpenRouter 后台轮换，新 key 只放服务器 `.env`（`chmod 600`），本地用低额度独立 key 并设 spend limit。

### 事件循环阻塞（server 性能，高并发下等于拒绝服务）
- **P1-8 同步向量检索**（`server/app/rag/service.py:44,90`）：改 `await asyncio.to_thread(retriever.search, message, api_top_k=..., code_top_k=...)`，顶部 `import asyncio`。
- **P1-9 同步 LLM 调用**（`server/app/text2revit/service.py:93,117`）：改 `await asyncio.to_thread(recognizer.recognize, message)`，顶部 `import asyncio`。
- **P1-10 forwarded_allow_ips="\*"**（`server/main.py:152`）：改为实际反代 IP（如 `"127.0.0.1"` 或负载均衡网段），否则 XFF 可任意伪造污染日志。

### 部署暴露（DEPLOY_NOTES / docker）
- **P1-11 SSH 反向隧道绑 0.0.0.0**（`DEPLOY_NOTES.md:51`）：`-R 0.0.0.0:18080` 改 `-R 127.0.0.1:18080:localhost:18080`；核查 GCP 防火墙无 18080 放行。
- **P1-12 Cloudflare Flexible SSL**（`DEPLOY_NOTES.md:180`）：改 Origin CA 证书 + Full(strict)，nginx 监听 443，GCP 防火墙限 80/443 仅 Cloudflare IP 段。

### 前端 XSS（高）
- **P1-13 admin token 存明文密码**（`h/admin/logs.html:193,219-224`）：后端 `/verify` 改签发短期随机 token（`secrets.token_urlsafe(32)`，服务端存 1 小时），前端存该 token 而非密码。
- **P1-14 主页博客卡片未转义**（`h/index.html:263-266`、`h/zh/index.html:263-266`）：新增 `esc()`，`title`/`tags` 转义，`href` 改 `BLOG_ORIGIN+'/'+encodeURI(String(p.path||''))` 防 `javascript:` 注入。两语言版本同步。
- **P1-15 Intent Bridge gr.HTML 未转义**（`intent_bridge/frontend/app.py:35-44,54-61`）：顶部 `import html`，`name`/`val`/槽位名一律 `html.escape(...)`。

### 数据链断裂（pipeline，双库不同步根因）
- **P1-16 SQLite id 漂移**（`pipeline/api_parser/parse_chm.py:444`）：`DELETE FROM revit_api` 改为 `DROP TABLE IF EXISTS revit_api` 后重建；或 DELETE 后追加 `DELETE FROM sqlite_sequence WHERE name='revit_api'`。这是「chromadb 旧构建不同步」的根因之一。
- **P1-17 ChromaDB 写入非幂等**（`pipeline/embedder/embed.py:94-99,190-195`）：`collection.add` 改 `collection.upsert`；循环前读 `existing = set(collection.get(include=[])["ids"])` 做断点续传；全量重建时先 `client.delete_collection("revit_api")`（try/except）。
- **P1-18 DELETE 后分批 commit 留半空库**（`pipeline/api_parser/parse_chm.py:444-489`）：删除循环内两处 `conn.commit()`，`conn.close()` 前只 commit 一次；或写 `.tmp` 后 `os.replace`。
- **P1-19 先删旧 DB 再写无原子替换**（`pipeline/sdk_parser/extract.py:756-758`、`pipeline/sdk_parser/quality_agent.py:402-404`）：改为写 `db.with_suffix(".db.tmp")`，成功后 `os.replace(tmp, db)`，删除 `db.unlink()` 分支。
- **P1-20 双库无一致性校验**（`server/app/rag` 侧 retriever 或 `pipeline/retriever.py:89-105`）：`RAGRetriever.__init__` 读 `chromadb_api_dir/meta.json` 的 `record_count`，与 `SELECT COUNT(*) FROM revit_api` 比对，不一致至少 `error` 告警（最好抛异常）。
- **P1-21 连通性 raise 被 verbose 包裹**（`pipeline/api_parser/quality_agent.py:552-566`）：把 `if not conn["api_ok"]: raise RuntimeError(...)` 移出 `if verbose:` 块，仅打印保留 verbose。

### C# 插件（高）
- **P1-22 注册成功却打印「失败」日志**（`revit_plugin/plugin/Core/CommandManager.cs:171`）：171-172 行日志改为「成功注册命令 [{0}] 来自 {1}」。误导排障的 bug。

---

## P2 —— 中（功能正确性、资源泄漏、次要安全）

### 安全 / 越权
- **P2-1 admin token 走 URL query**（`server/app/api/log_routes.py:30,45`）：删除 `token: str | None = Query(None)` 分支，仅保留 `X-Admin-Token` Header，前端同步改。
- **P2-2 sandbox 黑名单易绕过**（`mcp_bridge/sandbox.py:13-44`）：补黑名单 `AppDomain`、`Marshal`、`\bdynamic\b`、`unsafe`、`DllImport`、`GCHandle`、`\.Assembly\b`、`Activator`、`System\.Threading`；文档注明 sandbox 仅纵深防御一层，真正隔离在插件侧。
- **P2-3 orchestrate 会话无 TTL + 短 session_id**（`mcp_bridge/router.py:846,1024-1034`）：改带 TTL 存储（复用 `IntentSessionStore`），`session_id` 用完整 `uuid4().hex`。
- **P2-4 LLM 生成模板落盘前未审查**（`mcp_bridge/code_generator.py:449-467`）：solidify 落盘前对 `param_code` 跑 `sandbox.review`，不安全拒绝固化。
- **P2-5 交互 atom prompt 插入 C# 未转义**（`mcp_bridge/atom_registry.py:491-501,508-521`、`interactive.py:449-467`）：`msg.replace('\\','\\\\').replace('"','\\"')` 后再插入，或强制内部常量。
- **P2-6 破坏性删除无确认无显式回滚**（`revit_plugin/commandset/Services/DeleteElementEventHandler.cs:62-71`）：开事务前加 `TaskDialog` 确认（显示删除数量/类别）；catch 内 `if (transaction.HasStarted() && !transaction.HasEnded()) transaction.RollBack();`。
- **P2-7 MCP Bridge HTML 未转义 + 回显 traceback**（`mcp_bridge/frontend/app.py:252-262,943,1131,1320`）：`_step_md` 内 `msg`/`status` 先 `html.escape`；三处 `Error: {err[:200]}` 改 `f"Error: {type(e).__name__}: {e}"`，traceback 只进 logger。
- **P2-8 prompt_bridge 历史平铺 + 异常泄露前端**（`prompt_bridge/service.py:207-220,236-241`）：改结构化 messages（system + 逐条 role）；`str(e)` 只进日志，前端返回泛化提示。
- **P2-9 DeleteWarningSuperUtils 自动解决 Error**（`revit_plugin/commandset/Utils/DeleteWarningSuperUtils.cs:41-52`）：合并冗余 if/else 为单句；`Error` 级别不自动 `ResolveFailure`，改 `ProceedWithRollBack` 或交用户处理。

### 资源泄漏 / 阻塞（server + pipeline + bridge）
- **P2-10 log_store SQLite 连接不关闭**（`server/app/log_store.py:44-46,95,146,161,204`）：`with self._get_conn() as conn:` 改 `with contextlib.closing(self._get_conn()) as conn, conn:`，顶部 `import contextlib`。
- **P2-11 同步写库在事件循环 finally**（`server/app/log_store.py:270-280`）：改 `await asyncio.to_thread(store.log, ...)`。
- **P2-12 get_client_ip 盲信 XFF**（`server/app/log_store.py:220-230`）：优先 `request.client.host`，仅当直连 IP 在可信代理列表才取 XFF 首元素。
- **P2-13 intent.py enrich_from_db 吞异常 + 连接泄漏**（`server/app/text2revit/intent.py:176-198`）：用 `with contextlib.closing(sqlite3.connect(...)) as conn:`，`except Exception as e: logger.warning(...)`。
- **P2-14 streaming 断连后线程继续烧 token**（`server/app/api/streaming.py:31-49`）：加 `threading.Event()` 停止标志，`_produce` 循环内 `if stop.is_set(): break`，消费循环 `finally: stop.set()`。
- **P2-15 deps.get_retriever 首请求冷启动阻塞**（`server/app/deps.py:31-52`）：`main.py` 加 startup 钩子 `await asyncio.to_thread(get_retriever)` 预热。
- **P2-16 每请求新建 LLM 客户端 + 同步阻塞**（`mcp_bridge/router.py:311-341`）：`llm`/`gen` 缓存为模块级单例；同步 LLM 调用包 `run_in_executor`。
- **P2-17 httpx.Client 从不关闭**（`pipeline/llm_client.py:53-58`、`pipeline/embedder/providers/openai.py:21`）：给 `LLMClient` 加 `close()` 与 `__enter__/__exit__`。
- **P2-18 slot_engine sqlite 异常路径不关闭 + LIKE 未转义**（`intent_bridge/slot_engine.py:206-271`）：用 `with sqlite3.connect(...) as conn:` 或 try/finally；pattern 转义 `%`/`_`。

### 数据 / 逻辑正确性
- **P2-19 审核失败给满分 1.0**（`pipeline/api_parser/quality_agent.py:309-319`）：失败返回哨兵值，落库 `quality_score` 存 NULL 而非 1.0。
- **P2-20 metadata 含 None 使 ChromaDB 崩**（`pipeline/embedder/embed.py:83-90`）：改 `{"name": name or "", "full_id": full_id or "", "summary": (summary or "")[:500], "info": (info or "")[:500]}`。
- **P2-21 embedding 无重试 + 可能传空串**（`pipeline/embedder/embed.py:92,188,80`）：`embed_texts` 外包 3 次退避重试；空文本回退为 `name or str(_id)`。
- **P2-22 rglob O(N×M) 性能灾难**（`pipeline/api_parser/quality_agent.py:170-177`）：`run_quality_agent` 开头建一次 `name_index`，`_load_html_excerpt` 改字典查找。
- **P2-23 403 当瞬时错误重试 4 次**（`pipeline/llm_client.py:29`）：403 只重试 1 次或连续 N 个 403 熔断。
- **P2-24 retriever schema 探测吞异常**（`pipeline/retriever.py:121-129`）：`except sqlite3.OperationalError`，前面加 `if not os.path.exists(self._sdk_db): raise FileNotFoundError`。
- **P2-25 retriever LIKE 未转义 + 全表扫描**（`pipeline/retriever.py:559-589`）：pattern 转义 `%`/`_` 加 `ESCAPE '\'`；中期建 FTS5 虚表。
- **P2-26 SolidifiedTool(\*\*data) 未过滤字段易崩**（`mcp_bridge/tool_store.py:112-118`）：`load` 包 try/except，只取 `{k: data[k] for k in KNOWN_FIELDS if k in data}`。
- **P2-27 ws_relay 不校验 JSON-RPC id**（`mcp_bridge/ws_relay.py:104-110,132-144`）：`send_command` 收 raw 后若 `resp.get("id") != request_id` 则继续等待，对齐 TCP 客户端行为。
- **P2-28 llm_adapter 重试模型选择逻辑混乱**（`intent_bridge/llm_adapter.py:181-182`）：重构为显式 `for model_cfg in [primary, fallback]: for attempt in range(retries):`，去掉 swap。
- **P2-29 _use_skills 共享单例竞态**（`intent_bridge/router.py:92-107`）：`use_skills` 作为 `process_turn` 参数传入，不改共享实例状态。
- **P2-30 TCP 单次 Read 8192B 截断长消息**（`revit_plugin/plugin/Core/SocketService.cs:174-203`）：循环 `Read` 累加到 `MemoryStream` 直到完整帧再处理（对齐 WebSocket 端）。
- **P2-31 端口硬编码忽略配置**（`revit_plugin/plugin/Core/SocketService.cs:87`）：删除硬编码 `_port=18080`，恢复读配置逻辑。

### 前端功能 bug（中）
- **P2-32 API key 逐键上报**（`frontend/src/App.tsx:50,58`）：`handleSettingsChange` 从 `onChange` 挪到 `onBlur` 或 500ms debounce。
- **P2-33 工具页引入 AdSense**（`frontend/index.html:14-15`）：删除该第三方脚本（`h/index.html` 保留）。
- **P2-34 Skills 操作不查 resp.ok**（`frontend/src/components/tabs/SkillsTab.tsx:111-117,119-123,274-288`）：三处加 `if (!resp.ok) { alert(...); return }` 再更新 state。
- **P2-35 组件卸载不清理 timer/abort**（`BridgeTab.tsx:116-152`、`ChatPanel.tsx:21`、`PromptBridgeTab.tsx:80`、`TextStudioTab.tsx:60`）：各加 `useEffect(() => () => { abortRef.current?.abort() }, [])`；BridgeTab 额外 `clearInterval(timerRef.current)`。
- **P2-36 TextStudio 实验门禁形同虚设**（`frontend/src/components/tabs/TextStudioTab.tsx:113`）：`accept_experimental: true` 改 `accept_experimental: accepted`。
- **P2-37 SSE 解析重复 4 份**（`frontend/src/api/client.ts:43-83`、`api/settings.ts:11-52`、`PromptBridgeTab.tsx:105-136`、`TextStudioTab.tsx:131-162`）：抽 `client.ts` 的 `tokenStream()` 统一，其余替换。

### 配置 / 越权补充
- **P2-38 catch-all 遮蔽 /app**（`server/main.py:104-130`）：Gradio 挂载移到 catch-all 注册之前，或 `serve_react_spa` 开头排除 `app`/`app/`。
- **P2-39 ChatRequest.message 无长度上限**（`server/app/models.py:10`）：`message: str = Field(min_length=1, max_length=8000)`；`SettingsUpdate.api_key` 加 `max_length=200`。
- **P2-40 prompt_bridge session 越权待确认**（`prompt_bridge/router.py:29-48`）：核查 `get_session_store().get_or_create` 是否绑定用户，否则一个用户可用他人 session_id 读写历史。

### Docker / 依赖 / git 卫生（中）
- **P2-41 容器 root 运行 + 无 HEALTHCHECK**（`Dockerfile:10-47`）：加 `useradd appuser` + `USER appuser` + `HEALTHCHECK`（`/health` 已存在）。
- **P2-42 7860 绑 0.0.0.0**（`docker-compose.yml:7-8`）：改 `"127.0.0.1:7860:7860"`，仅供 nginx 反代。
- **P2-43 200MB 数据库被 git 追踪**（`data/legacy_db/`、`legacy/revit_sdk_collection/revit_sdk.db`、`legacy/split_revit.ipynb`）：`git rm -r --cached`，`.gitignore` 追加 `data/legacy_db/`；历史瘦身用 `git filter-repo`（需团队协调）。
- **P2-44 依赖浮动版本无锁**（`requirements-server.txt`、`requirements-pipeline.txt`）：`pip freeze > *.lock.txt`，Dockerfile 装 lock；或 `uv pip compile` 带 hash。
- **P2-45 .dockerignore 缺 node_modules/大数据**（`.dockerignore`）：追加 `frontend/node_modules`、`frontend/dist`、`data/legacy_db/`、`h/`、`scratch/`、`images/`、`tests/`。
- **P2-46 数据烘焙进镜像**（`Dockerfile:37-38`）：删除 `COPY data/sqlite/`、`COPY data/chromadb/`（compose volume 已挂载），保留 `data/skills/` 若运行时必需。
- **P2-47 notebook 脚本删 git hooks + 自动 push**（`scripts/fix_step6_cell.py:45-49,80,89`）：删清 hooks 循环改 `-c core.hooksPath=/dev/null`；`except:` 改 `except OSError:`；`git add -A` 改显式 add 目标文件，push 前打印 `git status --porcelain`。

### 测试质量（中）
- **P2-48 测试测的是副本非真实代码**（`tests/test_sse_thinking.py:8-9`）：删本地 `parse_sse_stream` 定义，改 `from server.app... import _parse_sse_stream`。
- **P2-49 测试依赖真实 DB 不可重复**（`tests/test_ppt_stats.py:15-17,126-127`）：加 `pytestmark = pytest.mark.skipif(not API_DB.exists() ..., reason=...)`，或移出 tests/。补 pipeline 核心纯函数单测。

---

## P3 —— 低（清理、优化、去重）

### 硬编码 / 去重
- **P3-1 后端地址硬编码 4 处**（`intent_bridge/frontend/app.py:24`、`mcp_bridge/frontend/app.py:23`、`mcp_bridge/frontend/api_explorer.py:69`、`server/frontend/gradio_app.py:25`）：抽 `def api_base(): return os.getenv("INTERNAL_API_BASE", "http://127.0.0.1:7860")`，4 处统一 import。
- **P3-2 SDK 绝对路径硬编码**（`pipeline/demo_sdk_cleaning.py:45`、`demo_real_golden.py:28`）：默认值改 `os.getenv("REVIT_SDK_ROOT", "F:/Revit 2026.3 SDK/Samples")`。
- **P3-3 _load_dotenv 三份重复**（`pipeline/demo_real_scoring.py:35-45`、`demo_real_golden.py:39-47`、`demo_production_real.py:186-191`）：移到 `config/__init__.py`。
- **P3-4 init_packages.sh 硬编码路径**（`scripts/init_packages.sh:3-20`）：改 `ROOT="$(cd "$(dirname "$0")/.." && pwd)"`。
- **P3-5 WsUrl 硬编码/重复**（`revit_plugin/plugin/Configuration/ServiceSettings.cs:37`、`ConnectionSettingsPage.xaml.cs:35`）：抽 `public const string DefaultWsUrl`。

### 死代码 / 未用导入
- **P3-6** `server/main.py:90`（未用 `import os`）、`server/app/skill_store.py:13`（未用 `import os`）：删除。
- **P3-7** `server/app/text2revit/service.py:25-27`（未用常量 `_T2R_*`）：删除并修正 31 行注释。
- **P3-8** `server/frontend/gradio_app.py:93-96`（favicon_path 未用）：删除。
- **P3-9** `pipeline/retriever.py:507-514`（停用词双重判断死代码）：删第二个 if，修正注释。
- **P3-10** `intent_bridge/router.py:48-78`（`max_turns` 定义未用）：移除或实现。

### 弃用 API / 小优化
- **P3-11** `server/app/api/streaming.py:29`（`asyncio.get_event_loop()` 弃用）：改 `get_running_loop()`。
- **P3-12** `server/app/log_store.py:92`（`datetime.utcnow()` 弃用）：改 `datetime.now(timezone.utc)`。
- **P3-13** `server/app/session.py:36-38`（history 无上限）：`add_message` 末尾 `if len(self.history) > 40: self.history = self.history[-40:]`。
- **P3-14** `server/app/log_store.py:96-103`（日志无滚动清理）：`log()` 截断输入 `[:10000]`，启动时清理 90 天前。
- **P3-15** 多处 `FilteredElementCollector` 未 Dispose（`revit_plugin` 多文件）：短生命周期收集器改 `using`。
- **P3-16** JSON-RPC 处理三方法重复（`SocketService.cs`/`WebSocketService.cs`）：复用 `CommandExecutor.ExecuteCommand`。
- **P3-17** `.First()`/`.FirstOrDefault()` 静默取多结果首元素（`CreateLineElementEventHandler.cs:305,311,326`、`ProjectUtils.cs:347,1007` 等）：多个合法候选时返回列表让用户选择（项目既定约定）。
- **P3-18** 空 catch 吞异常（`Application.cs:40,47`、`SocketService.cs:136-164`、`WebSocketService.cs:137-140`、`ConnectionSettingsPage.xaml.cs:50-53`）：至少写日志。
- **P3-19** `atom_registry.py:564-568` 双花括号疑为 C# 编译 bug：非格式化字符串应用单花括号。
- **P3-20** URL 参数未编码（`frontend/src/api/bridge.ts:48,50,53,57`、`SkillsTab.tsx:111,120`）：统一 `encodeURIComponent`。
- **P3-21** `[DONE]` break 层级错误（`PromptBridgeTab.tsx:121`、`TextStudioTab.tsx:147`）：改 break 外层或用统一 helper。
- **P3-22** 绕过受控组件写 DOM（`OrchestratorQuestions.tsx:29-31`）：删 querySelector，加 `pickDisplay` state。
- **P3-23** package.json 冗余依赖（`frontend/package.json:13,18,28`）：卸载 `@monaco-editor/react`、`react-syntax-highlighter`、`@types/react-syntax-highlighter`；`tailwindcss` 系移到 devDependencies；跑 `npm audit`。
- **P3-24** 巨型组件（`SkillsTab.tsx` 888 行、`BridgeTab.tsx` 777 行）：拆分到子目录/hooks。
- **P3-25** key 片段打印（`check_api.py:156`、`server/frontend/gradio_app.py:232`）：只显示 len 或后 4 位。
- **P3-26** docker-compose `version` 废弃字段 + 无资源限制（`docker-compose.yml:1`）：删 `version`，加 `mem_limit: 2g`。
- **P3-27** Dockerfile 残留 build-essential（`Dockerfile:15-17`）：pip install 后 purge 或拆 builder stage。
- **P3-28** 其余 pipeline 低危项：`parse_chm.py:418` makedirs 空 dirname 崩、`quality_agent.py:553-556` 就地改传入 config、`:694` 403 日志相对路径、`sdk_parser/extract.py:549-551` 脆弱正则、`demo_*.py` 未关连接/丢异常/无存在性检查（详见 pipeline 审查原文）。

---

## 附：做得好的地方（勿误改）
- 主 React 前端全程 ReactMarkdown 且未启用 `rehype-raw`（HTML 默认转义），无 `dangerouslySetInnerHTML`/`eval`。
- API key 只存内存（Zustand），未落 localStorage；`dist/` 产物无 key/token/内网 IP 泄露。
- `.env` 未被 git 追踪、历史干净；`.dockerignore` 已排除 `.env`/`.git`；compose 密钥经 `${OPENROUTER_API_KEY}` 注入。
- 密码比对用 `hmac.compare_digest`；pipeline 无硬编码 key、无 pickle、无 `verify=False`。
- 插件 `ClientWebSocket` 默认校验 TLS；ExternalEvent 线程模型正确；`.addin` 仅 1 个文件 ClientId 唯一。
- SQL 多数已参数化（`intent.py`、`slot_engine.py`）；`RevitClientPool` 单例 + 超时 + id 校验健壮。

---

## 执行顺序建议
1. **今天**：P0 全部（尤其链路 A 的 P0-1/P0-2、链路 B 的 P0-13/P0-14、链路 C 的 P0-10/P0-11/P0-12）+ P1-7（轮换密钥）+ P1-11/P1-12（部署暴露）。
2. **本周**：P1 剩余（鉴权、CORS、事件循环阻塞、双库一致性 P1-16~P1-20）。
3. **迭代中**：P2（正确性、资源泄漏、Docker/git 卫生）。
4. **有余力**：P3（清理去重）。

修改后建议再跑一次 `/code-review` 或 `/security-review` 验证 P0/P1 已闭合。
