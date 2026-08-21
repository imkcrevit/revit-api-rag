# Revit 2026远程联调包

此包已为`graptolite.ai`远程Bridge预配置。Revit插件主动通过HTTPS/WSS的443端口连接服务器，不需要开放本机的18080端口。

## 安装

准备条件：Windows x64、Autodesk Revit 2026、7-Zip，以及可访问`graptolite.ai:443`的网络。

1. 使用随下载链接提供的密码解压整个`.7z`文件。
2. 完全关闭Revit。
3. 在解压目录空白处按住Shift并右键，选择“在此处打开PowerShell窗口”。
4. 执行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
.\install-revit-demo.ps1
```

安装器会备份已有插件，并将文件安装到：

```text
%APPDATA%\Autodesk\Revit\Addins\2026\
```

首次联调默认关闭远程代码执行，只允许较安全的连接与查询测试。

## 连接服务器

1. 启动Revit 2026；如出现插件安全提示，确认加载`mcp-servers-for-revit`。
2. 打开Ribbon中的`Revit MCP`面板。
3. 点击`Revit MCP Switch`。
4. 对话框应显示`WebSocket Connected`、服务器地址和`Slot: 1`。
5. 浏览器打开`https://graptolite.ai/revit/`。
6. 进入`Bridge`页签，选择`Slot 1`。
7. 打开包内`revit-slot-1.token`，把唯一一行复制到`Slot token`输入框。
8. 刷新连接状态；应显示Revit已连接。

令牌只保存在浏览器当前标签页的`sessionStorage`中，关闭标签页后清除。不要把令牌截图、上传或粘贴到录屏画面中。

## Demo录制前检查

先保持默认的只读安全模式，完成以下检查：

1. `https://graptolite.ai/api/v1/bridge/service-health`显示`remote_relay_ready: true`。
2. 浏览器Bridge页显示Slot 1已连接。
3. 在Revit保持一个测试模型和非生产视图，确认基础查询可返回结果。

需要执行生成的C#代码或固化工具时，关闭Revit，再从解压目录执行：

```powershell
.\install-revit-demo.ps1 -EnableRemoteCodeExecution
```

重新启动Revit并再次点击`Revit MCP Switch`。只在专用测试模型中开启该选项，录制完成后建议重新运行不带开关的安装命令恢复关闭状态。

## 排障

- 无`Revit MCP`面板：确认使用Revit 2026，并检查`%APPDATA%\Autodesk\Revit\Addins\2026\mcp-servers-for-revit.addin`。
- 显示未连接：确认Windows能访问`https://graptolite.ai/api/v1/bridge/service-health`，并再次点击`Revit MCP Switch`。
- Slot已占用：关闭另一台正在使用Slot 1的Revit，等待数秒后重试。
- 插件日志：`%APPDATA%\Autodesk\Revit\Addins\2026\revit_mcp_plugin\Logs\mcp_YYYYMMDD.log`。
- 安装器会把旧插件目录重命名为`revit_mcp_plugin.backup-时间戳`，可用于回退。

## 安全收尾

录制完成后关闭Revit和浏览器标签页，删除解压目录中的`revit-slot-1.token`及不再需要的安装包，并通知服务器侧轮换Slot 1令牌。
