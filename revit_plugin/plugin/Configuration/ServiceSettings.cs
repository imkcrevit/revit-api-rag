using Newtonsoft.Json;

namespace revit_mcp_plugin.Configuration
{
    /// <summary>
    /// <para>服务设置类</para>
    /// <para>Service settings.</para>
    /// </summary>
    public class ServiceSettings
    {
        /// <summary>
        /// <para>默认 WebSocket 服务器地址（统一常量，避免多处硬编码重复）。</para>
        /// <para>Default WebSocket server URL (single source of truth to avoid duplication).</para>
        /// </summary>
        public const string DefaultWsUrl = "wss://graptolite.ai/api/v1/bridge/ws";

        /// <summary>
        /// <para>日志级别</para>
        /// <para>Log level.</para>
        /// </summary>
        [JsonProperty("logLevel")]
        public string LogLevel { get; set; } = "Info";

        /// <summary>
        /// <para>socket服务端口</para>
        /// <para>Socket service port.</para>
        /// </summary>
        [JsonProperty("port")]
        public int Port { get; set; } = 18080;

        /// <summary>
        /// <para>连接模式: "tcp" 或 "websocket"</para>
        /// <para>Connection mode: "tcp" or "websocket".</para>
        /// </summary>
        [JsonProperty("mode")]
        public string Mode { get; set; } = "tcp";

        /// <summary>
        /// <para>WebSocket 服务器地址</para>
        /// <para>WebSocket server URL (used when mode = "websocket").</para>
        /// </summary>
        [JsonProperty("wsUrl")]
        public string WsUrl { get; set; } = DefaultWsUrl;

        /// <summary>
        /// <para>WebSocket 槽位编号 (1-5)</para>
        /// <para>WebSocket slot ID (1-5, used when mode = "websocket").</para>
        /// </summary>
        [JsonProperty("slotId")]
        public string SlotId { get; set; } = "1";

        /// <summary>
        /// <para>预共享鉴权令牌。若为空则向后兼容放行（仅记录警告）。</para>
        /// <para>Pre-shared auth token. When empty, requests are allowed for
        /// backward compatibility but a warning is logged.</para>
        /// </summary>
        [JsonProperty("token")]
        public string Token { get; set; } = "";

        /// <summary>
        /// <para>是否允许远程下发的代码执行类高危方法（send_code_to_revit /
        /// manage_solidified_tools run 等）。默认关闭。</para>
        /// <para>Whether remote code-execution methods are allowed. Default off.</para>
        /// </summary>
        [JsonProperty("allowRemoteCodeExecution")]
        public bool AllowRemoteCodeExecution { get; set; } = false;
    }
}
