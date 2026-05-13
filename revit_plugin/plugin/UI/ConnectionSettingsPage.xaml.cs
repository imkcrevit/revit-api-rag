using Newtonsoft.Json;
using revit_mcp_plugin.Configuration;
using revit_mcp_plugin.Core;
using revit_mcp_plugin.Utils;
using System;
using System.IO;
using System.Windows;
using System.Windows.Controls;

namespace revit_mcp_plugin.UI
{
    public partial class ConnectionSettingsPage : Page
    {
        public ConnectionSettingsPage()
        {
            InitializeComponent();
            LoadSettings();
            UpdateStatus();
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
                        WsUrlTextBox.Text = s.WsUrl ?? "wss://graptolite.ai/api/v1/bridge/ws";

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
            catch
            {
                // Use defaults
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
            bool wsRunning = WebSocketService.Instance.IsRunning;

            if (tcpRunning)
            {
                StatusText.Text = $"TCP Server running on port {SocketService.Instance.Port}";
            }
            else if (wsRunning)
            {
                StatusText.Text = $"WebSocket connected to slot {WebSocketService.Instance.SlotId}";
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
