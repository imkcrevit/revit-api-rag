using Autodesk.Revit.Attributes;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using revit_mcp_plugin.Configuration;
using revit_mcp_plugin.Utils;
using Newtonsoft.Json;
using System;
using System.IO;

namespace revit_mcp_plugin.Core
{
    [Transaction(TransactionMode.Manual)]
    public class MCPServiceConnection : IExternalCommand
    {
        public Result Execute(ExternalCommandData commandData, ref string message, ElementSet elements)
        {
            try
            {
                ServiceSettings settings = LoadSettings();
                bool useWebSocket = string.Equals(settings.Mode, "websocket",
                    StringComparison.OrdinalIgnoreCase);

                if (useWebSocket)
                {
                    return HandleWebSocket(commandData, settings);
                }
                else
                {
                    return HandleTcp(commandData);
                }
            }
            catch (Exception ex)
            {
                message = ex.Message;
                return Result.Failed;
            }
        }

        private Result HandleTcp(ExternalCommandData commandData)
        {
            // Stop WebSocket if it was running (mode switch)
            if (WebSocketService.Instance.IsRunning)
                WebSocketService.Instance.Stop();

            SocketService service = SocketService.Instance;

            if (service.IsRunning)
            {
                service.Stop();
                TaskDialog.Show("revitMCP", "TCP Server Closed");
            }
            else
            {
                service.Initialize(commandData.Application);
                service.Start();
                TaskDialog.Show("revitMCP", $"TCP Server Started (port {service.Port})");
            }

            return Result.Succeeded;
        }

        private Result HandleWebSocket(ExternalCommandData commandData, ServiceSettings settings)
        {
            // Stop TCP if it was running (mode switch)
            if (SocketService.Instance.IsRunning)
                SocketService.Instance.Stop();

            WebSocketService service = WebSocketService.Instance;

            if (service.IsRunning)
            {
                service.Stop();
                TaskDialog.Show("revitMCP", "WebSocket Disconnected");
            }
            else
            {
                service.Initialize(commandData.Application);
                service.Start(settings.WsUrl, settings.SlotId);
                TaskDialog.Show("revitMCP",
                    $"WebSocket connection started\nServer: {settings.WsUrl}\n" +
                    $"Slot: {settings.SlotId}\nCheck Settings > Connection for live status.");
            }

            return Result.Succeeded;
        }

        private ServiceSettings LoadSettings()
        {
            try
            {
                string configPath = PathManager.GetCommandRegistryFilePath();
                if (File.Exists(configPath))
                {
                    string json = File.ReadAllText(configPath);
                    var config = JsonConvert.DeserializeObject<FrameworkConfig>(json);
                    if (config?.Settings != null)
                        return config.Settings;
                }
            }
            catch
            {
                // Fall through to defaults
            }

            return new ServiceSettings();
        }
    }
}
