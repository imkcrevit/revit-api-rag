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
        public string WsUrl { get; set; } = "wss://graptolite.ai/api/v1/bridge/ws";

        /// <summary>
        /// <para>WebSocket 槽位编号 (1-5)</para>
        /// <para>WebSocket slot ID (1-5, used when mode = "websocket").</para>
        /// </summary>
        [JsonProperty("slotId")]
        public string SlotId { get; set; } = "1";
    }
}
