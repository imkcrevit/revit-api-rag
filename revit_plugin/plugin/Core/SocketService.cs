using System;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using Autodesk.Revit.UI;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using RevitMCPSDK.API.Models.JsonRPC;
using RevitMCPSDK.API.Interfaces;
using revit_mcp_plugin.Configuration;
using revit_mcp_plugin.Utils;

namespace revit_mcp_plugin.Core
{
    public class SocketService
    {
        private static SocketService _instance;
        private TcpListener _listener;
        private Thread _listenerThread;
        private bool _isRunning;
        private int _port = 18080;
        private UIApplication _uiApp;
        private ICommandRegistry _commandRegistry;
        private ILogger _logger;
        private CommandExecutor _commandExecutor;
        private string _authToken;
        private bool _tokenWarningLogged;

        public static SocketService Instance
        {
            get
            {
                if(_instance == null)
                    _instance = new SocketService();
                return _instance;
            }
        }

        private SocketService()
        {
            _commandRegistry = new RevitCommandRegistry();
            _logger = new Logger();
        }

        public bool IsRunning => _isRunning;

        public int Port
        {
            get => _port;
            set => _port = value;
        }

        // 初始化
        // Initialization.
        public void Initialize(UIApplication uiApp)
        {
            _uiApp = uiApp;

            // 初始化事件管理器
            // Initialize ExternalEventManager
            ExternalEventManager.Instance.Initialize(uiApp, _logger);

            // 记录当前 Revit 版本
            // Get the current Revit version.
            var versionAdapter = new RevitMCPSDK.API.Utils.RevitVersionAdapter(_uiApp.Application);
            string currentVersion = versionAdapter.GetRevitVersion();
            _logger.Info("当前 Revit 版本: {0}\nCurrent Revit version: {0}", currentVersion);



            // 创建命令执行器
            // Create CommandExecutor
            _commandExecutor = new CommandExecutor(_commandRegistry, _logger);

            // 加载配置并注册命令
            // Load configuration and register commands.
            ConfigurationManager configManager = new ConfigurationManager(_logger);
            configManager.LoadConfiguration();

            // 读取预共享鉴权 token（可选增强，缺失时向后兼容放行）
            // Read the pre-shared auth token (optional; missing token stays backward compatible).
            _authToken = configManager.Config?.Settings?.Token;


            // 从配置中读取服务端口（缺省仍为字段默认值 18080）
            // Read the service port from the configuration (defaults to 18080 when unset).
            if (configManager.Config?.Settings?.Port > 0)
            {
                _port = configManager.Config.Settings.Port;
            }

            // 加载命令
            // Load command.
            CommandManager commandManager = new CommandManager(
                _commandRegistry, _logger, configManager, _uiApp);
            commandManager.LoadCommands();

            _logger.Info($"Socket service initialized on port {_port}");
        }

        public void Start()
        {
            if (_isRunning) return;

            try
            {
                _isRunning = true;
                // 仅监听本地回环地址，避免同网段主机直连未鉴权端口
                // Bind to loopback only so other hosts on the LAN cannot reach the port.
                _listener = new TcpListener(IPAddress.Loopback, _port);
                _listener.Start();

                _listenerThread = new Thread(ListenForClients)
                {
                    IsBackground = true
                };
                _listenerThread.Start();
            }
            catch (Exception ex)
            {
                _isRunning = false;
                _logger.Error("Socket 服务启动失败 / Failed to start socket service: {0}", ex.Message);
            }
        }

        public void Stop()
        {
            if (!_isRunning) return;

            try
            {
                _isRunning = false;

                _listener?.Stop();
                _listener = null;

                if(_listenerThread!=null && _listenerThread.IsAlive)
                {
                    _listenerThread.Join(1000);
                }
            }
            catch (Exception ex)
            {
                _logger.Error("Socket 服务停止时异常 / Error stopping socket service: {0}", ex.Message);
            }
        }

        private void ListenForClients()
        {
            try
            {
                while (_isRunning)
                {
                    TcpClient client = _listener.AcceptTcpClient();

                    Thread clientThread = new Thread(HandleClientCommunication)
                    {
                        IsBackground = true
                    };
                    clientThread.Start(client);
                }
            }
            catch (SocketException)
            {
                // 监听器被 Stop() 关闭时的正常退出，仅调试记录
                // Normal exit when Stop() closes the listener; debug-level only.
                _logger.Debug("Socket 监听线程结束 / Socket listener thread ended");
            }
            catch(Exception ex)
            {
                _logger.Error("Socket 监听循环异常 / Socket listen loop error: {0}", ex.Message);
            }
        }

        private void HandleClientCommunication(object clientObj)
        {
            TcpClient tcpClient = (TcpClient)clientObj;
            NetworkStream stream = tcpClient.GetStream();

            try
            {
                byte[] buffer = new byte[8192];

                while (_isRunning && tcpClient.Connected)
                {
                    // 循环读取并累加到 MemoryStream，直到读完当前完整帧再处理，
                    // 避免单次 8192B 读取截断超长消息（对齐 WebSocket 端的分片累加）。
                    // Loop-read into a MemoryStream until the full frame is received before
                    // processing, so messages larger than the buffer are not truncated
                    // (mirrors the fragment accumulation on the WebSocket side).
                    int bytesRead;

                    try
                    {
                        bytesRead = stream.Read(buffer, 0, buffer.Length);
                    }
                    catch (IOException)
                    {
                        // 客户端断开连接
                        // Client disconnected.
                        break;
                    }

                    if (bytesRead == 0)
                    {
                        // 客户端断开连接
                        // Client disconnected.
                        break;
                    }

                    string message;
                    using (var ms = new MemoryStream())
                    {
                        ms.Write(buffer, 0, bytesRead);

                        // 继续读取仍在缓冲区中的剩余分片，直到没有更多数据
                        // Drain the remaining fragments of the same frame still buffered.
                        while (stream.DataAvailable)
                        {
                            int more;
                            try
                            {
                                more = stream.Read(buffer, 0, buffer.Length);
                            }
                            catch (IOException)
                            {
                                break;
                            }
                            if (more <= 0)
                                break;
                            ms.Write(buffer, 0, more);
                        }

                        message = Encoding.UTF8.GetString(ms.GetBuffer(), 0, (int)ms.Length);
                    }

                    System.Diagnostics.Trace.WriteLine($"收到消息: {message}\nReceived message: {message}");

                    string response = ProcessJsonRPCRequest(message);

                    // 发送响应
                    // Send response.
                    byte[] responseData = Encoding.UTF8.GetBytes(response);
                    stream.Write(responseData, 0, responseData.Length);
                }
            }
            catch (Exception ex)
            {
                // 记录客户端通信异常，便于排障（原为静默吞异常）
                // Log client-communication errors for diagnostics (was silently swallowed).
                _logger.Error("Socket 客户端通信异常 / Socket client communication error: {0}", ex.Message);
            }
            finally
            {
                tcpClient.Close();
            }
        }

        private string ProcessJsonRPCRequest(string requestJson)
        {
            JsonRPCRequest request;

            try
            {
                // 解析JSON-RPC请求
                // Parse JSON-RPC requests.
                request = JsonConvert.DeserializeObject<JsonRPCRequest>(requestJson);

                // 验证请求格式是否有效
                // Verify that the request format is valid.
                if (request == null || !request.IsValid())
                {
                    return CreateErrorResponse(
                        null,
                        JsonRPCErrorCodes.InvalidRequest,
                        "Invalid JSON-RPC request"
                    );
                }

                // 校验鉴权 token（未配置时向后兼容放行，仅记录一次警告）
                // Validate the auth token (backward-compatible when unset; warns once).
                if (!IsAuthorized(requestJson))
                {
                    return CreateErrorResponse(request.Id,
                        JsonRPCErrorCodes.InvalidRequest,
                        "Unauthorized: invalid or missing token");
                }

                // 查找命令
                // Search for the command in the registry.
                if (!_commandRegistry.TryGetCommand(request.Method, out var command))
                {
                    return CreateErrorResponse(request.Id, JsonRPCErrorCodes.MethodNotFound,
                        $"Method '{request.Method}' not found");
                }

                // 执行命令
                // Execute command.
                try
                {                
                    object result = command.Execute(request.GetParamsObject(), request.Id);

                    return CreateSuccessResponse(request.Id, result);
                }
                catch (Exception ex)
                {
                    return CreateErrorResponse(request.Id, JsonRPCErrorCodes.InternalError, ex.Message);
                }
            }
            catch (JsonException)
            {
                // JSON解析错误
                // JSON parsing error.
                return CreateErrorResponse(
                    null,
                    JsonRPCErrorCodes.ParseError,
                    "Invalid JSON"
                );
            }
            catch (Exception ex)
            {
                // 处理请求时的其他错误
                // Catch other errors produced when processing requests.
                return CreateErrorResponse(
                    null,
                    JsonRPCErrorCodes.InternalError,
                    $"Internal error: {ex.Message}"
                );
            }
        }

        /// <summary>
        /// 校验请求中的 token 字段是否与用户配置密钥一致。
        /// 配置里若无 token 则记录一次 warning 并放行（向后兼容）。
        /// Validate the request's token field against the configured secret.
        /// If no token is configured, log a warning once and allow (backward compatible).
        /// </summary>
        private bool IsAuthorized(string requestJson)
        {
            if (string.IsNullOrEmpty(_authToken))
            {
                if (!_tokenWarningLogged)
                {
                    _logger.Warning("未配置鉴权 token，已放行 socket 请求（向后兼容）\nNo auth token configured; allowing socket requests (backward compatible).");
                    _tokenWarningLogged = true;
                }
                return true;
            }

            try
            {
                var obj = JObject.Parse(requestJson);
                string reqToken = obj.Value<string>("token")
                    ?? obj["params"]?.Value<string>("token");
                return string.Equals(reqToken, _authToken, StringComparison.Ordinal);
            }
            catch
            {
                return false;
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
