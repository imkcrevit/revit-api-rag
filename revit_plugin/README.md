# Revit Plugin (TCP + Remote WebSocket)

Revit 2026插件，既可提供TCP JSON-RPC 2.0服务，也可主动连接远程WebSocket Bridge，接收Python端发来的命令和动态C#代码执行请求。

## 来源

源码 fork 自 [mcp-servers-for-revit](https://github.com/mcp-servers-for-revit/mcp-servers-for-revit)（MIT License），仅保留 `plugin/` 和 `commandset/` 部分，删除了 Node.js MCP Server（由本项目的 Python RAG Server 替代）。

原始项目 License 见 [LICENSE-upstream](./LICENSE-upstream)。

## 本项目的修改

| 文件 | 修改内容 |
|------|----------|
| `plugin/Configuration/ServiceSettings.cs` | 默认端口 `8080` → `18080` |
| `plugin/Core/SocketService.cs` | 硬编码端口 `8080` → `18080`（避免与 AdskLicensingAgent 冲突） |
| `plugin/Core/WebSocketService.cs` | 增加WSS重连、槽位令牌握手和远程代码执行开关 |
| `plugin/UI/ConnectionSettingsPage.*` | 增加TCP/WebSocket连接模式与槽位设置 |

远程握手必须发送`User-Agent: RevitMCPPlugin/0.3`。Cloudflare会对缺少该请求头的
.NET `ClientWebSocket`握手返回HTTP 403；修正版会正常升级为HTTP 101。

## 编译

**前置条件**：
- .NET 8 SDK
- Windows构建环境（WPF/WindowsDesktop SDK）

```bash
cd revit_plugin
dotnet build plugin/RevitMCPPlugin.csproj -c "Release R26"
dotnet build commandset/RevitMCPCommandSet.csproj -c "Release R26"
```

编译产物输出到 `plugin/bin/AddIn 2026 Release R26/revit_mcp_plugin/`。必须同时
编译`plugin`和`commandset`，后者会把24个命令及Roslyn依赖复制到最终插件目录。

## 部署

### 方式 A：使用加密Demo Kit（远程联调推荐）

1. 下载本次联调提供的加密`Revit-Demo-Kit.7z`并解压。
2. 关闭Revit，以PowerShell运行`install-revit-demo.ps1`。
3. 首次安装保持远程代码执行关闭；只读联通通过后，运行
   `./install-revit-demo.ps1 -EnableRemoteCodeExecution`并重新启动Revit。

旧的`v0.2.0-plugin`Release早于WebSocket和槽位鉴权实现，不适用于本次远程联调。

### 方式 B：从源码编译

1. 按上面的编译步骤生成 DLL
2. 将 `plugin/bin/AddIn 2026 Release R26/revit_mcp_plugin/` 复制到 `%AppData%\Autodesk\Revit\Addins\2026\`
3. 将`mcp-servers-for-revit.addin`复制到`%AppData%\Autodesk\Revit\Addins\2026\`
4. 将`commandRegistry.json`复制到插件目录的`Commands\`，再按下方远程WebSocket说明配置。

### 部署后的文件结构

```
%AppData%\Autodesk\Revit\Addins\2026\
├── mcp-servers-for-revit.addin          ← 插件注册
└── revit_mcp_plugin\
    ├── RevitMCPPlugin.dll               ← 主插件 DLL
    ├── RevitMCPSDK.dll                  ← SDK 依赖
    ├── Microsoft.Windows.SDK.NET.dll
    ├── Newtonsoft.Json.dll
    ├── WinRT.Runtime.dll
    └── Commands\
        ├── commandRegistry.json         ← 命令注册（24 条）
        └── RevitMCPCommandSet\
            ├── command.json             ← 命令定义
            └── 2026\
                ├── RevitMCPCommandSet.dll   ← 命令实现
                ├── Microsoft.CodeAnalysis.CSharp.dll  ← Roslyn
                ├── Microsoft.CodeAnalysis.dll
                └── ...
```

## 使用

### 本地TCP模式

1. 启动 Revit 2026
2. 点击 Ribbon 上的 **"Revit MCP Switch"** 按钮启动 TCP 服务
3. 服务监听 `localhost:18080`，接受 JSON-RPC 2.0 请求
4. Python 端通过 `mcp_bridge/revit_client.py` 连接

### 远程WebSocket模式（graptolite.ai）

远程模式不需要、也不应该把Windows上的`18080`暴露到公网。插件主动通过
`wss://graptolite.ai/api/v1/bridge/ws/{slot_id}`连接服务器的443端口。

服务器部署侧先运行：

```bash
scripts/init-bridge-token.sh
docker compose up -d --build revit-api-rag
```

Revit端在`Settings → Connection`中选择`WebSocket (Cloud)`，服务器地址保持：

```text
wss://graptolite.ai/api/v1/bridge/ws
```

初次联调使用槽位`1`。把服务器`.secrets/revit-slot-1.token`的内容写入插件目录下
`Commands/commandRegistry.json`的`settings.token`；初次只读联调保持
`allowRemoteCodeExecution: false`。确认槽位、`say_hello`和模型查询通过后，再按需显式开启远程代码执行。

`allowRemoteCodeExecution: true`是安装级执行授权。开启后，动态代码和已注册固化工具直接执行，不再在Revit内逐次显示代码确认弹窗；Slot令牌校验、服务器端安全审查以及固化工具注册确认仍然保留。

浏览器打开`https://graptolite.ai/revit/`，选择`Slot 1`，把同一个令牌粘贴到
`Slot token`输入框。令牌仅保存在当前标签页的`sessionStorage`中，关闭标签页后清除。

## 关键参数

| 参数 | 值 | 说明 |
|------|-----|------|
| 端口 | 18080 | TCP 监听端口 |
| 协议 | JSON-RPC 2.0 | UTF-8 编码 |
| Buffer | 8192 bytes | 单次读取上限 |
| 超时 | 60s | 代码执行超时 |
| 命令数 | 24 | 包含`send_code_to_revit`和`manage_solidified_tools` |

## 24个预置命令

| 命令 | 说明 |
|------|------|
| `say_hello` | 连通测试 |
| `send_code_to_revit` | 动态 C# 代码执行（Roslyn 编译） |
| `get_available_family_types` | 按类别查询族类型 |
| `get_selected_elements` | 获取用户选中的元素 |
| `operate_element` | 操作元素（选择/着色/隐藏/隔离） |
| `create_point_based_element` | 创建点基族实例 |
| `create_line_based_element` | 创建线基族实例 |
| `create_surface_based_element` | 创建面基族实例 |
| `create_wall` / `create_grid` / `create_level` / `create_room` | 创建建筑元素 |
| `delete_element` | 删除元素 |
| `analyze_model_statistics` | 模型统计分析 |
| `manage_solidified_tools` | 管理浏览器端固化工具 |
| ... | 完整列表见 `command.json` |
