# Revit Plugin (TCP Socket Service)

Revit 2026 插件，提供 TCP JSON-RPC 2.0 服务，接收 Python 端发来的命令和动态 C# 代码执行请求。

## 来源

源码 fork 自 [mcp-servers-for-revit](https://github.com/mcp-servers-for-revit/mcp-servers-for-revit)（MIT License），仅保留 `plugin/` 和 `commandset/` 部分，删除了 Node.js MCP Server（由本项目的 Python RAG Server 替代）。

原始项目 License 见 [LICENSE-upstream](./LICENSE-upstream)。

## 本项目的修改

| 文件 | 修改内容 |
|------|----------|
| `plugin/Configuration/ServiceSettings.cs` | 默认端口 `8080` → `18080` |
| `plugin/Core/SocketService.cs` | 硬编码端口 `8080` → `18080`（避免与 AdskLicensingAgent 冲突） |

## 编译

**前置条件**：
- .NET 8 SDK
- Revit 2026 已安装（需要 Revit API DLL 引用）

```bash
cd revit_plugin
dotnet restore plugin/RevitMCPPlugin.csproj -p:Configuration="Debug R26"
dotnet build plugin/RevitMCPPlugin.csproj -c "Debug R26"
```

编译产物输出到 `plugin/bin/AddIn 2026 Debug R26/revit_mcp_plugin/`。

## 部署

### 方式 A：使用 Release 预编译 DLL（推荐）

1. 从 [GitHub Release](https://github.com/imkcrevit/revit-api-rag/releases) 下载 `revit_mcp_plugin.zip`
2. 解压到 `%AppData%\Autodesk\Revit\Addins\2026\revit_mcp_plugin\`
3. 将 `mcp-servers-for-revit.addin` 复制到 `%AppData%\Autodesk\Revit\Addins\2026\`
4. 将 `commandRegistry.json` 复制到 `%AppData%\Autodesk\Revit\Addins\2026\revit_mcp_plugin\Commands\`

### 方式 B：从源码编译

1. 按上面的编译步骤生成 DLL
2. 将 `plugin/bin/AddIn 2026 Debug R26/revit_mcp_plugin/` 复制到 `%AppData%\Autodesk\Revit\Addins\2026\`
3. 同方式 A 的步骤 3-4

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
        ├── commandRegistry.json         ← 命令注册（23 条）
        └── RevitMCPCommandSet\
            ├── command.json             ← 命令定义
            └── 2026\
                ├── RevitMCPCommandSet.dll   ← 命令实现
                ├── Microsoft.CodeAnalysis.CSharp.dll  ← Roslyn
                ├── Microsoft.CodeAnalysis.dll
                └── ...
```

## 使用

1. 启动 Revit 2026
2. 点击 Ribbon 上的 **"Revit MCP Switch"** 按钮启动 TCP 服务
3. 服务监听 `localhost:18080`，接受 JSON-RPC 2.0 请求
4. Python 端通过 `mcp_bridge/revit_client.py` 连接

## 关键参数

| 参数 | 值 | 说明 |
|------|-----|------|
| 端口 | 18080 | TCP 监听端口 |
| 协议 | JSON-RPC 2.0 | UTF-8 编码 |
| Buffer | 8192 bytes | 单次读取上限 |
| 超时 | 60s | 代码执行超时 |
| 命令数 | 23 | 包含 `send_code_to_revit` |

## 23 个预置命令

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
| ... | 完整列表见 `command.json` |
