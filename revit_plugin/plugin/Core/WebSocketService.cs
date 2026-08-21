using System;
using System.Collections.Generic;
using System.Net.WebSockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Autodesk.Revit.UI;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using RevitMCPSDK.API.Models.JsonRPC;
using RevitMCPSDK.API.Interfaces;
using revit_mcp_plugin.Configuration;
using revit_mcp_plugin.Utils;

namespace revit_mcp_plugin.Core
{
    /// <summary>
    /// WebSocket client service — connects TO the cloud server as a named slot.
    /// Receives JSON-RPC 2.0 requests from the server, executes them in Revit,
    /// and sends responses back through the same WebSocket.
    ///
    /// This is the reverse of SocketService (TCP):
    ///   TCP:       Server connects TO plugin (plugin = server)
    ///   WebSocket: Plugin connects TO server (plugin = client)
    ///
    /// JSON-RPC protocol is identical — only the transport layer changes.
    /// </summary>
    public class WebSocketService
    {
        private static WebSocketService _instance;
        private ClientWebSocket _ws;
        private CancellationTokenSource _cts;
        private Thread _workerThread;
        private bool _isRunning;
        private volatile string _lastConnectionError;

        private string _serverUrl;
        private string _slotId;
        private int _reconnectDelayMs = 5000;

        private UIApplication _uiApp;
        private ICommandRegistry _commandRegistry;
        private ILogger _logger;
        private CommandExecutor _commandExecutor;
        private string _authToken;
        private bool _allowRemoteCodeExecution;

        // 代码执行类高危方法白名单（默认关闭，除非配置显式允许）
        // Code-execution class of high-risk methods (disabled by default unless config allows).
        private static readonly HashSet<string> CodeExecutionMethods = new HashSet<string>(StringComparer.Ordinal)
        {
            "send_code_to_revit",
            "manage_solidified_tools",
        };

        public static WebSocketService Instance
        {
            get
            {
                if (_instance == null)
                    _instance = new WebSocketService();
                return _instance;
            }
        }

        private WebSocketService()
        {
            _commandRegistry = new RevitCommandRegistry();
            _logger = new Logger();
        }

        public bool IsRunning => _isRunning;
        public bool IsConnected => _ws != null && _ws.State == WebSocketState.Open;
        public string LastConnectionError => _lastConnectionError;
        public string SlotId => _slotId;
        public string ServerUrl => _serverUrl;

        /// <summary>
        /// Initialize with Revit context and load commands.
        /// Call this before Start().
        /// </summary>
        public void Initialize(UIApplication uiApp)
        {
            _uiApp = uiApp;

            ExternalEventManager.Instance.Initialize(uiApp, _logger);

            var versionAdapter = new RevitMCPSDK.API.Utils.RevitVersionAdapter(_uiApp.Application);
            string currentVersion = versionAdapter.GetRevitVersion();
            _logger.Info("WebSocket mode — Revit version: {0}", currentVersion);

            _commandExecutor = new CommandExecutor(_commandRegistry, _logger);

            ConfigurationManager configManager = new ConfigurationManager(_logger);
            configManager.LoadConfiguration();

            // 读取鉴权 token 与代码执行开关（可选增强，缺失时向后兼容）
            // Read auth token and code-execution switch (optional; backward compatible).
            _authToken = configManager.Config?.Settings?.Token;
            _allowRemoteCodeExecution =
                configManager.Config?.Settings?.AllowRemoteCodeExecution ?? false;

            CommandManager commandManager = new CommandManager(
                _commandRegistry, _logger, configManager, _uiApp);
            commandManager.LoadCommands();

            _logger.Info("WebSocket service initialized, commands loaded");
        }

        /// <summary>
        /// Start the WebSocket client.
        /// </summary>
        /// <param name="serverUrl">
        /// Base WebSocket URL, e.g. "wss://graptolite.ai/api/v1/bridge/ws"
        /// </param>
        /// <param name="slotId">Slot number 1-5</param>
        public void Start(string serverUrl, string slotId)
        {
            if (_isRunning) return;

            _serverUrl = serverUrl.TrimEnd('/');
            _slotId = slotId;
            _isRunning = true;
            _lastConnectionError = null;
            _cts = new CancellationTokenSource();

            _workerThread = new Thread(WorkerLoop)
            {
                IsBackground = true,
                Name = "WebSocketService"
            };
            _workerThread.Start();

            _logger.Info($"WebSocket service starting → {_serverUrl}/{_slotId}");
        }

        public void Stop()
        {
            if (!_isRunning) return;
            _isRunning = false;

            try
            {
                _cts?.Cancel();

                if (_ws != null && _ws.State == WebSocketState.Open)
                {
                    // Best-effort close
                    _ws.CloseAsync(WebSocketCloseStatus.NormalClosure, "Plugin stopping",
                        CancellationToken.None).Wait(2000);
                }
                _ws?.Dispose();
                _ws = null;

                if (_workerThread != null && _workerThread.IsAlive)
                    _workerThread.Join(2000);
            }
            catch (Exception ex)
            {
                // 停止过程中的异常仅记录，不影响关闭流程
                // Log shutdown errors instead of swallowing them silently.
                _logger.Warning("WebSocket 停止时异常 / Error stopping WebSocket service: {0}", ex.Message);
            }

            _logger.Info("WebSocket service stopped");
        }

        // ── Worker loop with auto-reconnect ─────────────────────────────

        private void WorkerLoop()
        {
            while (_isRunning)
            {
                try
                {
                    // Avoid AggregateException masking the actual HTTP/WebSocket error.
                    ConnectAndListen().GetAwaiter().GetResult();
                }
                catch (Exception ex)
                {
                    if (!_isRunning) break;
                    Exception rootCause = ex.GetBaseException();
                    _lastConnectionError = rootCause.Message;
                    _logger.Warning($"WebSocket connection lost: {rootCause.Message}");
                }

                // Wait before reconnecting
                if (_isRunning)
                {
                    _logger.Info($"Reconnecting in {_reconnectDelayMs / 1000}s...");
                    Thread.Sleep(_reconnectDelayMs);
                }
            }
        }

        private async Task ConnectAndListen()
        {
            _ws?.Dispose();
            _ws = new ClientWebSocket();
            // Cloudflare rejects the header-less .NET ClientWebSocket handshake
            // with HTTP 403. An explicit product User-Agent reaches the origin
            // and upgrades normally with HTTP 101.
            _ws.Options.SetRequestHeader("User-Agent", "RevitMCPPlugin/0.3");

            var uri = new Uri($"{_serverUrl}/{_slotId}");
            _logger.Info($"Connecting to {uri}...");

            await _ws.ConnectAsync(uri, _cts.Token);
            _lastConnectionError = null;
            _logger.Info($"Connected to slot {_slotId}");

            // 连接后发送鉴权 token 完成握手（仅在配置了 token 时发送，向后兼容）
            // Send auth token after connecting to complete the handshake.
            // Only sent when a token is configured, keeping the protocol backward compatible.
            if (!string.IsNullOrEmpty(_authToken))
            {
                string authMsg = JsonConvert.SerializeObject(new
                {
                    type = "auth",
                    slot_id = _slotId,
                    token = _authToken
                });
                byte[] authBytes = Encoding.UTF8.GetBytes(authMsg);
                await _ws.SendAsync(
                    new ArraySegment<byte>(authBytes),
                    WebSocketMessageType.Text,
                    endOfMessage: true,
                    cancellationToken: _cts.Token);
                _logger.Info("已发送鉴权握手 / Auth handshake sent");
            }

            // Receive loop
            var buffer = new byte[65536];
            var messageBuffer = new StringBuilder();

            while (_ws.State == WebSocketState.Open && _isRunning)
            {
                WebSocketReceiveResult result;
                try
                {
                    result = await _ws.ReceiveAsync(
                        new ArraySegment<byte>(buffer), _cts.Token);
                }
                catch (OperationCanceledException)
                {
                    break;
                }

                if (result.MessageType == WebSocketMessageType.Close)
                {
                    string closeDetail = $"Server closed WebSocket connection: " +
                        $"{result.CloseStatus} {result.CloseStatusDescription}".TrimEnd();
                    _lastConnectionError = closeDetail;
                    _logger.Warning(closeDetail);
                    break;
                }

                messageBuffer.Append(Encoding.UTF8.GetString(buffer, 0, result.Count));

                if (result.EndOfMessage)
                {
                    string request = messageBuffer.ToString();
                    messageBuffer.Clear();

                    System.Diagnostics.Trace.WriteLine($"[WS] Received: {request}");

                    // Process and respond
                    string response = ProcessJsonRPCRequest(request);
                    byte[] responseBytes = Encoding.UTF8.GetBytes(response);

                    await _ws.SendAsync(
                        new ArraySegment<byte>(responseBytes),
                        WebSocketMessageType.Text,
                        endOfMessage: true,
                        cancellationToken: _cts.Token);
                }
            }
        }

        // ── JSON-RPC processing (identical to SocketService) ────────────

        private string ProcessJsonRPCRequest(string requestJson)
        {
            JsonRPCRequest request;

            try
            {
                request = JsonConvert.DeserializeObject<JsonRPCRequest>(requestJson);

                if (request == null || !request.IsValid())
                {
                    return CreateErrorResponse(null,
                        JsonRPCErrorCodes.InvalidRequest,
                        "Invalid JSON-RPC request");
                }

                // 代码执行类高危方法白名单开关：默认关闭，防止远程直接下发代码执行。
                // Gate code-execution class methods: disabled by default so a remote peer
                // cannot trigger arbitrary code execution without explicit opt-in.
                if (!_allowRemoteCodeExecution && CodeExecutionMethods.Contains(request.Method))
                {
                    _logger.Warning("已拦截代码执行类方法 {0}（allowRemoteCodeExecution=false）\nBlocked code-execution method {0} (allowRemoteCodeExecution=false).", request.Method);
                    return CreateErrorResponse(request.Id,
                        JsonRPCErrorCodes.InvalidRequest,
                        $"Method '{request.Method}' is disabled. Set 'allowRemoteCodeExecution' to enable.");
                }

                if (!_commandRegistry.TryGetCommand(request.Method, out var command))
                {
                    return CreateErrorResponse(request.Id,
                        JsonRPCErrorCodes.MethodNotFound,
                        $"Method '{request.Method}' not found");
                }

                try
                {
                    object result = command.Execute(request.GetParamsObject(), request.Id);
                    return CreateSuccessResponse(request.Id, result);
                }
                catch (Exception ex)
                {
                    return CreateErrorResponse(request.Id,
                        JsonRPCErrorCodes.InternalError, ex.Message);
                }
            }
            catch (JsonException)
            {
                return CreateErrorResponse(null,
                    JsonRPCErrorCodes.ParseError, "Invalid JSON");
            }
            catch (Exception ex)
            {
                return CreateErrorResponse(null,
                    JsonRPCErrorCodes.InternalError, $"Internal error: {ex.Message}");
            }
        }

        private string CreateSuccessResponse(string id, object result)
        {
            var response = new JsonRPCSuccessResponse
            {
                Id = id,
                Result = result is JToken jToken ? jToken : JToken.FromObject(result)
            };
            return response.ToJson();
        }

        private string CreateErrorResponse(string id, int code, string message, object data = null)
        {
            var response = new JsonRPCErrorResponse
            {
                Id = id,
                Error = new JsonRPCError
                {
                    Code = code,
                    Message = message,
                    Data = data != null ? JToken.FromObject(data) : null
                }
            };
            return response.ToJson();
        }
    }
}
