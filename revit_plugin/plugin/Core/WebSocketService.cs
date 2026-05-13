using System;
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

        private string _serverUrl;
        private string _slotId;
        private int _reconnectDelayMs = 5000;

        private UIApplication _uiApp;
        private ICommandRegistry _commandRegistry;
        private ILogger _logger;
        private CommandExecutor _commandExecutor;

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
            catch (Exception)
            {
                // Swallow shutdown errors
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
                    ConnectAndListen().Wait();
                }
                catch (Exception ex)
                {
                    if (!_isRunning) break;
                    _logger.Warning($"WebSocket connection lost: {ex.Message}");
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

            var uri = new Uri($"{_serverUrl}/{_slotId}");
            _logger.Info($"Connecting to {uri}...");

            await _ws.ConnectAsync(uri, _cts.Token);
            _logger.Info($"Connected to slot {_slotId}");

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
                    _logger.Info("Server closed WebSocket connection");
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
