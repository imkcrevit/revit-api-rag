using Newtonsoft.Json;
using revit_mcp_plugin.Configuration;
using revit_mcp_plugin.Core;
using revit_mcp_plugin.Utils;
using System;
using System.IO;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Threading;

namespace revit_mcp_plugin.UI
{
    public partial class ConnectionSettingsPage : Page
    {
        private readonly DispatcherTimer _statusTimer;

        public ConnectionSettingsPage()
        {
            InitializeComponent();
            LoadSettings();
            UpdateStatus();

            _statusTimer = new DispatcherTimer
            {
                Interval = TimeSpan.FromSeconds(1)
            };
            _statusTimer.Tick += (_, _) => UpdateStatus();
            Loaded += (_, _) => _statusTimer.Start();
            Unloaded += (_, _) => _statusTimer.Stop();
        }

        private void LoadSettings()
        {
            try
            {
                string configPath = PathManager.GetCommandRegistryFilePath();
                if (File.Exists(configPath))
                {
                    string json = File.ReadAllText(configPath);
                    var config = JsonConvert.DeserializeObject<FrameworkConfig>(json);
                    if (config?.Settings != null)
                    {
                        var s = config.Settings;

                        PortTextBox.Text = s.Port.ToString();
                        WsUrlTextBox.Text = s.WsUrl ?? ServiceSettings.DefaultWsUrl;

                        // Set slot combo
                        int slotIndex;
                        if (int.TryParse(s.SlotId, out slotIndex) && slotIndex >= 1 && slotIndex <= 5)
                            SlotComboBox.SelectedIndex = slotIndex - 1;

                        // Set mode radio
                        bool isWs = string.Equals(s.Mode, "websocket",
                            StringComparison.OrdinalIgnoreCase);
                        TcpRadio.IsChecked = !isWs;
                        WsRadio.IsChecked = isWs;
                    }
                }
            }
            catch (Exception ex)
            {
                // 读取配置失败时回退到默认值，并记录原因便于排障
                // Fall back to defaults on read failure, logging the cause for diagnostics.
                System.Diagnostics.Trace.WriteLine(
                    $"加载连接设置失败，使用默认值 / Failed to load connection settings, using defaults: {ex.Message}");
            }
        }

        private void Mode_Changed(object sender, RoutedEventArgs e)
        {
            if (TcpPanel == null || WsPanel == null) return;

            if (WsRadio.IsChecked == true)
            {
                TcpPanel.Visibility = Visibility.Collapsed;
                WsPanel.Visibility = Visibility.Visible;
            }
            else
            {
                TcpPanel.Visibility = Visibility.Visible;
                WsPanel.Visibility = Visibility.Collapsed;
            }
        }

        private void UpdateStatus()
        {
            bool tcpRunning = SocketService.Instance.IsRunning;
            WebSocketService wsService = WebSocketService.Instance;

            if (tcpRunning)
            {
                StatusText.Text = $"TCP Server running on port {SocketService.Instance.Port}";
            }
            else if (wsService.IsConnected)
            {
                StatusText.Text = $"WebSocket connected to slot {wsService.SlotId}";
            }
            else if (wsService.IsRunning)
            {
                StatusText.Text = string.IsNullOrWhiteSpace(wsService.LastConnectionError)
                    ? $"WebSocket connecting to slot {wsService.SlotId}..."
                    : $"WebSocket reconnecting to slot {wsService.SlotId}. " +
                      $"Last error: {wsService.LastConnectionError}";
            }
            else
            {
                StatusText.Text = "Not connected";
            }
        }

        private void SaveButton_Click(object sender, RoutedEventArgs e)
        {
            try
            {
                string configPath = PathManager.GetCommandRegistryFilePath();
                FrameworkConfig config;

                if (File.Exists(configPath))
                {
                    string json = File.ReadAllText(configPath);
                    config = JsonConvert.DeserializeObject<FrameworkConfig>(json) ?? new FrameworkConfig();
                }
                else
                {
                    config = new FrameworkConfig();
                }

                config.Settings.Mode = WsRadio.IsChecked == true ? "websocket" : "tcp";

                int port;
                if (int.TryParse(PortTextBox.Text, out port) && port > 0 && port < 65536)
                    config.Settings.Port = port;

                config.Settings.WsUrl = WsUrlTextBox.Text.Trim();
                config.Settings.SlotId = ((ComboBoxItem)SlotComboBox.SelectedItem)?.Content?.ToString() ?? "1";

                string output = JsonConvert.SerializeObject(config, Formatting.Indented);
                File.WriteAllText(configPath, output);

                MessageBox.Show("Settings saved.\nRestart the connection for changes to take effect.",
                    "Settings", MessageBoxButton.OK, MessageBoxImage.Information);
            }
            catch (Exception ex)
            {
                MessageBox.Show($"Failed to save: {ex.Message}",
                    "Error", MessageBoxButton.OK, MessageBoxImage.Error);
            }
        }
    }
}
