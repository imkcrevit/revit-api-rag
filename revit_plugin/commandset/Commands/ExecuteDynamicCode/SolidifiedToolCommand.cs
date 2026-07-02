using Autodesk.Revit.UI;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using RevitMCPSDK.API.Base;
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;

namespace RevitMCPCommandSet.Commands.ExecuteDynamicCode
{
    /// <summary>
    /// Manages solidified tools — code templates created from the web frontend.
    ///
    /// TCP command: "manage_solidified_tools"
    /// Actions:
    ///   - list: returns all registered solidified tools
    ///   - register: adds/updates a tool definition
    ///   - run: renders a tool's code template with params and executes via Roslyn
    ///   - delete: removes a tool
    /// </summary>
    public class SolidifiedToolCommand : ExternalEventCommandBase
    {
        private ExecuteCodeEventHandler _handler => (ExecuteCodeEventHandler)Handler;
        private static readonly string ToolsFileName = "solidified_tools.json";

        public override string CommandName => "manage_solidified_tools";

        public SolidifiedToolCommand(UIApplication uiApp)
            : base(new ExecuteCodeEventHandler(), uiApp)
        {
        }

        public override object Execute(JObject parameters, string requestId)
        {
            string action = parameters.Value<string>("action") ?? "list";

            switch (action.ToLower())
            {
                case "list":
                    return ListTools();
                case "register":
                    return RegisterTool(parameters);
                case "run":
                    return RunTool(parameters, requestId);
                case "delete":
                    return DeleteTool(parameters);
                default:
                    throw new ArgumentException($"Unknown action: {action}. Use: list, register, run, delete");
            }
        }

        #region Actions

        private object ListTools()
        {
            var tools = LoadToolsFile();
            return new { tools = tools, count = tools.Count };
        }

        private object RegisterTool(JObject parameters)
        {
            string name = parameters.Value<string>("name")
                ?? throw new ArgumentException("Missing 'name'");
            string codeTemplate = parameters.Value<string>("code_template")
                ?? throw new ArgumentException("Missing 'code_template'");
            string description = parameters.Value<string>("description") ?? "";
            string sourceQuery = parameters.Value<string>("source_query") ?? "";

            var toolParams = parameters["parameters"]?.ToObject<List<ToolParameter>>()
                ?? new List<ToolParameter>();

            // 落盘前人工确认（register 会把远程下发的 code_template 写入本地文件）
            // Manual confirmation before persisting: register writes a remotely supplied
            // code_template to a local file, so require the Revit user to approve it.
            var codePreview = codeTemplate.Length > 4000
                ? codeTemplate.Substring(0, 4000) + "\n... (truncated)"
                : codeTemplate;
            var confirmDialog = new TaskDialog("确认注册固化工具 / Confirm tool registration")
            {
                MainInstruction = $"即将注册/更新固化工具 '{name}' 并写入本地文件，是否继续？\n" +
                                  $"Register/update solidified tool '{name}' and write it to disk. Continue?",
                MainContent = codePreview,
                CommonButtons = TaskDialogCommonButtons.Yes | TaskDialogCommonButtons.No,
                DefaultButton = TaskDialogResult.No
            };
            if (confirmDialog.Show() != TaskDialogResult.Yes)
            {
                throw new OperationCanceledException(
                    "用户取消了固化工具注册 / User cancelled tool registration");
            }

            var tools = LoadToolsFile();

            // Update or add
            var existing = tools.FirstOrDefault(t => t.Name == name);
            if (existing != null)
            {
                existing.CodeTemplate = codeTemplate;
                existing.Description = description;
                existing.Parameters = toolParams;
                existing.SourceQuery = sourceQuery;
                existing.UpdatedAt = DateTime.Now.ToString("o");
            }
            else
            {
                tools.Add(new SolidifiedToolDef
                {
                    Name = name,
                    DisplayName = name.Replace("_", " "),
                    Description = description,
                    CodeTemplate = codeTemplate,
                    Parameters = toolParams,
                    SourceQuery = sourceQuery,
                    CreatedAt = DateTime.Now.ToString("o"),
                    UpdatedAt = DateTime.Now.ToString("o"),
                });
            }

            SaveToolsFile(tools);
            return new { status = "registered", name = name, total = tools.Count };
        }

        private object RunTool(JObject parameters, string requestId)
        {
            string name = parameters.Value<string>("name")
                ?? throw new ArgumentException("Missing 'name'");
            var toolParams = parameters["params"]?.ToObject<Dictionary<string, string>>()
                ?? new Dictionary<string, string>();

            var tools = LoadToolsFile();
            var tool = tools.FirstOrDefault(t => t.Name == name)
                ?? throw new ArgumentException($"Tool '{name}' not found");

            // Render code template.
            // 只替换工具中预声明的参数名，数字参数经 TryParse 校验，字符串转义，
            // 防止通过参数值注入任意 C# 代码。
            // Only substitute parameter names declared on the tool. Numeric params are
            // validated via TryParse; string params are escaped — this prevents arbitrary
            // C# injection through parameter values.
            string code = tool.CodeTemplate;
            foreach (var paramDef in tool.Parameters)
            {
                if (paramDef?.Name == null)
                    continue;
                if (!toolParams.TryGetValue(paramDef.Name, out var rawValue))
                    continue;

                string safeValue = SanitizeParamValue(paramDef, rawValue);
                code = code.Replace($"{{{paramDef.Name}}}", safeValue);
            }

            // Execute via Roslyn (same as send_code_to_revit).
            // 人工确认弹窗由 ExecuteCodeEventHandler.Execute 在 UI 线程统一展示渲染后代码。
            // The manual confirmation dialog is shown by ExecuteCodeEventHandler.Execute
            // on the UI thread, displaying the fully rendered code.
            _handler.SetExecutionParameters(code, Array.Empty<object>());
            if (RaiseAndWaitForCompletion(60000))
            {
                // Update usage count
                tool.ExecutionCount++;
                tool.LastUsed = DateTime.Now.ToString("o");
                SaveToolsFile(tools);

                return _handler.ResultInfo;
            }
            else
            {
                throw new TimeoutException("Tool execution timed out");
            }
        }

        /// <summary>
        /// 校验并转义单个模板参数值，防止代码注入。
        /// Validate and escape a single template parameter value to prevent injection.
        /// </summary>
        private static string SanitizeParamValue(ToolParameter def, string value)
        {
            value = value ?? "";
            string type = (def.Type ?? "string").ToLowerInvariant();

            switch (type)
            {
                case "int":
                case "integer":
                case "long":
                    if (!long.TryParse(value, NumberStyles.Integer, CultureInfo.InvariantCulture, out var l))
                        throw new ArgumentException(
                            $"参数 '{def.Name}' 需要整数，实际值: '{value}'");
                    return l.ToString(CultureInfo.InvariantCulture);

                case "double":
                case "float":
                case "number":
                case "decimal":
                    if (!double.TryParse(value, NumberStyles.Float, CultureInfo.InvariantCulture, out var d))
                        throw new ArgumentException(
                            $"参数 '{def.Name}' 需要数字，实际值: '{value}'");
                    return d.ToString(CultureInfo.InvariantCulture);

                default:
                    // 字符串：转义反斜杠、双引号、换行，防止跳出字符串字面量
                    // String: escape backslashes, double quotes and newlines so the value
                    // cannot break out of a string literal in the template.
                    return value
                        .Replace("\\", "\\\\")
                        .Replace("\"", "\\\"")
                        .Replace("\r", "\\r")
                        .Replace("\n", "\\n");
            }
        }

        private object DeleteTool(JObject parameters)
        {
            string name = parameters.Value<string>("name")
                ?? throw new ArgumentException("Missing 'name'");

            var tools = LoadToolsFile();
            int removed = tools.RemoveAll(t => t.Name == name);
            SaveToolsFile(tools);

            return new { status = removed > 0 ? "deleted" : "not_found", name = name };
        }

        #endregion

        #region File IO

        private string GetToolsFilePath()
        {
            string dir = Path.GetDirectoryName(
                System.Reflection.Assembly.GetExecutingAssembly().Location);
            // Go up to the plugin root, then into a shared data directory
            string dataDir = Path.Combine(dir, "..", "Data");
            if (!Directory.Exists(dataDir))
                Directory.CreateDirectory(dataDir);
            return Path.Combine(dataDir, ToolsFileName);
        }

        private List<SolidifiedToolDef> LoadToolsFile()
        {
            string path = GetToolsFilePath();
            if (!File.Exists(path))
                return new List<SolidifiedToolDef>();

            try
            {
                string json = File.ReadAllText(path);
                var container = JsonConvert.DeserializeObject<ToolsContainer>(json);
                return container?.Tools ?? new List<SolidifiedToolDef>();
            }
            catch
            {
                return new List<SolidifiedToolDef>();
            }
        }

        private void SaveToolsFile(List<SolidifiedToolDef> tools)
        {
            string path = GetToolsFilePath();
            var container = new ToolsContainer { Tools = tools };
            string json = JsonConvert.SerializeObject(container, Formatting.Indented);
            File.WriteAllText(path, json);
        }

        #endregion

        #region Models

        private class ToolsContainer
        {
            [JsonProperty("tools")]
            public List<SolidifiedToolDef> Tools { get; set; } = new List<SolidifiedToolDef>();
        }

        private class SolidifiedToolDef
        {
            [JsonProperty("name")]
            public string Name { get; set; }

            [JsonProperty("display_name")]
            public string DisplayName { get; set; }

            [JsonProperty("description")]
            public string Description { get; set; }

            [JsonProperty("code_template")]
            public string CodeTemplate { get; set; }

            [JsonProperty("parameters")]
            public List<ToolParameter> Parameters { get; set; } = new List<ToolParameter>();

            [JsonProperty("source_query")]
            public string SourceQuery { get; set; }

            [JsonProperty("execution_count")]
            public int ExecutionCount { get; set; }

            [JsonProperty("created_at")]
            public string CreatedAt { get; set; }

            [JsonProperty("updated_at")]
            public string UpdatedAt { get; set; }

            [JsonProperty("last_used")]
            public string LastUsed { get; set; }
        }

        private class ToolParameter
        {
            [JsonProperty("name")]
            public string Name { get; set; }

            [JsonProperty("type")]
            public string Type { get; set; } = "string";

            [JsonProperty("description")]
            public string Description { get; set; }

            [JsonProperty("choices_from")]
            public string ChoicesFrom { get; set; }
        }

        #endregion
    }
}
