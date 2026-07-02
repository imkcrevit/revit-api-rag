using System.IO;
using System.Reflection;
using Autodesk.Revit.UI;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Newtonsoft.Json;
using RevitMCPSDK.API.Interfaces;

namespace RevitMCPCommandSet.Commands.ExecuteDynamicCode
{
    /// <summary>
    /// 处理代码执行的外部事件处理器
    /// </summary>
    public class ExecuteCodeEventHandler : IExternalEventHandler, IWaitableExternalEventHandler
    {
        // 代码执行参数
        private string _generatedCode;
        private object[] _executionParameters;

        // 执行结果信息
        public ExecutionResultInfo ResultInfo { get; private set; }

        // 状态同步对象
        public bool TaskCompleted { get; private set; }
        private readonly ManualResetEvent _resetEvent = new ManualResetEvent(false);

        // 设置要执行的代码和参数
        public void SetExecutionParameters(string code, object[] parameters = null)
        {
            _generatedCode = code;
            _executionParameters = parameters ?? Array.Empty<object>();
            TaskCompleted = false;
            _resetEvent.Reset();
        }

        // 等待执行完成 - IWaitableExternalEventHandler接口实现
        public bool WaitForCompletion(int timeoutMilliseconds = 10000)
        {
            _resetEvent.Reset();
            return _resetEvent.WaitOne(timeoutMilliseconds);
        }

        public void Execute(UIApplication app)
        {
            try
            {
                var doc = app.ActiveUIDocument.Document;
                ResultInfo = new ExecutionResultInfo();

                // 人工确认弹窗（在 Revit UI 线程上下文，由 ExternalEvent 触发）
                // Manual confirmation dialog (runs on the Revit UI thread via ExternalEvent).
                // 展示待执行代码全文，用户点「否」即取消执行。
                var codePreview = _generatedCode ?? string.Empty;
                if (codePreview.Length > 4000)
                    codePreview = codePreview.Substring(0, 4000) + "\n... (truncated)";

                var confirmDialog = new TaskDialog("确认执行 AI 代码 / Confirm code execution")
                {
                    MainInstruction = "即将在当前 Revit 文档中执行以下代码，是否继续？\n" +
                                      "The following code will be executed in the current Revit document. Continue?",
                    MainContent = codePreview,
                    CommonButtons = TaskDialogCommonButtons.Yes | TaskDialogCommonButtons.No,
                    DefaultButton = TaskDialogResult.No
                };
                if (confirmDialog.Show() != TaskDialogResult.Yes)
                {
                    throw new OperationCanceledException("用户取消了代码执行 / User cancelled code execution");
                }

                using (var transaction = new Transaction(doc, "执行AI代码"))
                {
                    transaction.Start();

                    // 动态编译执行代码
                    var result = CompileAndExecuteCode(
                        code: _generatedCode,
                        doc: doc,
                        parameters: _executionParameters
                    );

                    transaction.Commit();

                    ResultInfo.Success = true;
                    ResultInfo.Result = JsonConvert.SerializeObject(result);
                }
            }
            catch (Exception ex)
            {
                ResultInfo.Success = false;
                var innerMsg = ex.InnerException != null
                    ? $"{ex.InnerException.GetType().Name}: {ex.InnerException.Message}"
                    : ex.Message;
                ResultInfo.ErrorMessage = $"执行失败: {innerMsg}";
            }
            finally
            {
                TaskCompleted = true;
                _resetEvent.Set();
            }
        }

        private object CompileAndExecuteCode(string code, Document doc, object[] parameters)
        {
            // 包装代码以规范入口点
            var wrappedCode = $@"
using System;
using System.Linq;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using System.Collections.Generic;

namespace AIGeneratedCode
{{
    public static class CodeExecutor
    {{
        public static object Execute(Document document, object[] parameters)
        {{
            // 用户代码入口
            {code}
        }}
    }}
}}";

            var syntaxTree = CSharpSyntaxTree.ParseText(wrappedCode);

            // 符号黑名单静态检查：编译/执行前拒绝危险符号
            // Symbol blacklist static check: reject dangerous symbols before compile/execute.
            EnforceSymbolBlacklist(syntaxTree);

            // 添加必要的程序集引用（白名单）：只引用固定的基础库与 Revit API，
            // 而非「全部已加载程序集」，缩小可被滥用的攻击面。
            // Reference only a fixed whitelist of assemblies (base libraries + Revit API),
            // instead of every loaded assembly, to shrink the abuse surface.
            var allowedAssemblyNames = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
            {
                "mscorlib",
                "System",
                "System.Core",
                "System.Runtime",
                "System.Private.CoreLib",
                "System.Collections",
                "System.Linq",
                "netstandard",
                "RevitAPI",
                "RevitAPIUI",
            };

            var references = AppDomain.CurrentDomain.GetAssemblies()
                .Where(a => !a.IsDynamic && !string.IsNullOrEmpty(a.Location))
                .Where(a => allowedAssemblyNames.Contains(a.GetName().Name))
                .Select(a => MetadataReference.CreateFromFile(a.Location))
                .Cast<MetadataReference>()
                .ToList();

            // 编译代码
            var compilation = CSharpCompilation.Create(
                "AIGeneratedCode",
                syntaxTrees: new[] { syntaxTree },
                references: references,
                options: new CSharpCompilationOptions(OutputKind.DynamicallyLinkedLibrary)
            );

            using (var ms = new MemoryStream())
            {
                var result = compilation.Emit(ms);

                // 处理编译结果
                if (!result.Success)
                {
                    var errors = string.Join("\n", result.Diagnostics
                        .Where(d => d.Severity == DiagnosticSeverity.Error)
                        .Select(d => $"Line {d.Location.GetLineSpan().StartLinePosition.Line}: {d.GetMessage()}"));
                    throw new Exception($"代码编译错误:\n{errors}");
                }

                // 反射调用执行方法
                ms.Seek(0, SeekOrigin.Begin);
                var assembly = Assembly.Load(ms.ToArray());
                var executorType = assembly.GetType("AIGeneratedCode.CodeExecutor");
                var executeMethod = executorType.GetMethod("Execute");

                return executeMethod.Invoke(null, new object[] { doc, parameters });
            }
        }

        /// <summary>
        /// 符号黑名单静态检查：遍历语法树的标识符，命中危险符号即抛异常拒绝执行。
        /// Static symbol-blacklist check: walk the syntax tree identifiers and reject
        /// execution if a dangerous symbol (System.IO / Process / Assembly / DllImport /
        /// File / Registry) is present.
        /// </summary>
        private static void EnforceSymbolBlacklist(SyntaxTree syntaxTree)
        {
            // 被禁止的标识符（含命名空间片段，如 System.IO 的 "IO"）
            var forbidden = new HashSet<string>(StringComparer.Ordinal)
            {
                "IO",        // System.IO
                "Process",   // System.Diagnostics.Process
                "Assembly",  // System.Reflection.Assembly
                "DllImport", // P/Invoke
                "File",      // System.IO.File
                "Registry",  // Microsoft.Win32.Registry
            };

            var root = syntaxTree.GetRoot();
            foreach (var token in root.DescendantTokens())
            {
                if (token.IsKind(SyntaxKind.IdentifierToken) &&
                    forbidden.Contains(token.ValueText))
                {
                    throw new Exception(
                        $"代码包含被禁止的符号，已拒绝执行: '{token.ValueText}'\n" +
                        $"Code contains a forbidden symbol and was rejected: '{token.ValueText}'");
                }
            }
        }

        public string GetName()
        {
            return "执行AI代码";
        }
    }

    // 执行结果数据结构
    public class ExecutionResultInfo
    {
        [JsonProperty("success")]
        public bool Success { get; set; }

        [JsonProperty("result")]
        public string Result { get; set; }

        [JsonProperty("errorMessage")]
        public string ErrorMessage { get; set; } = string.Empty;
    }
}
