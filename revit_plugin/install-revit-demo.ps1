[CmdletBinding()]
param(
    [string]$ServerUrl = "wss://graptolite.ai/api/v1/bridge/ws",
    [ValidatePattern('^[1-5]$')]
    [string]$SlotId = "1",
    [switch]$EnableRemoteCodeExecution
)

$ErrorActionPreference = "Stop"
$packageRevision = "2026-08-21-autoxec1"

if (Get-Process -Name Revit -ErrorAction SilentlyContinue) {
    throw "Close Revit before installing or updating the plugin."
}

$kitRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$payloadRoot = Join-Path $kitRoot "plugin"
$pluginSource = Join-Path $payloadRoot "revit_mcp_plugin"
$addinSource = Join-Path $payloadRoot "mcp-servers-for-revit.addin"
$registrySource = Join-Path $payloadRoot "commandRegistry.json"
$tokenPath = Join-Path $kitRoot "revit-slot-$SlotId.token"

foreach ($requiredPath in @($pluginSource, $addinSource, $registrySource, $tokenPath)) {
    if (-not (Test-Path -LiteralPath $requiredPath)) {
        throw "Missing package file: $requiredPath"
    }
}

$token = (Get-Content -LiteralPath $tokenPath -Raw).Trim()
if ([string]::IsNullOrWhiteSpace($token)) {
    throw "The slot token file is empty."
}

$addinsRoot = Join-Path $env:APPDATA "Autodesk\Revit\Addins\2026"
$pluginDestination = Join-Path $addinsRoot "revit_mcp_plugin"
$addinDestination = Join-Path $addinsRoot "mcp-servers-for-revit.addin"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"

New-Item -ItemType Directory -Path $addinsRoot -Force | Out-Null

if (Test-Path -LiteralPath $pluginDestination) {
    $backupDestination = "$pluginDestination.backup-$stamp"
    Move-Item -LiteralPath $pluginDestination -Destination $backupDestination
    Write-Host "Existing plugin backed up to: $backupDestination"
}
if (Test-Path -LiteralPath $addinDestination) {
    Copy-Item -LiteralPath $addinDestination -Destination "$addinDestination.backup-$stamp" -Force
}

Copy-Item -LiteralPath $pluginSource -Destination $addinsRoot -Recurse -Force
Copy-Item -LiteralPath $addinSource -Destination $addinDestination -Force

$commandsDestination = Join-Path $pluginDestination "Commands"
New-Item -ItemType Directory -Path $commandsDestination -Force | Out-Null
$registryDestination = Join-Path $commandsDestination "commandRegistry.json"
Copy-Item -LiteralPath $registrySource -Destination $registryDestination -Force

$config = Get-Content -LiteralPath $registryDestination -Raw | ConvertFrom-Json
$settings = [pscustomobject]@{
    logLevel = "Info"
    port = 18080
    mode = "websocket"
    wsUrl = $ServerUrl.TrimEnd('/')
    slotId = $SlotId
    token = $token
    allowRemoteCodeExecution = [bool]$EnableRemoteCodeExecution
}

if ($null -eq $config.settings) {
    $config | Add-Member -NotePropertyName settings -NotePropertyValue $settings
} else {
    $config.settings = $settings
}

$config | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $registryDestination -Encoding UTF8

Write-Host "Revit 2026 plugin installed: $pluginDestination"
Write-Host "Package revision: $packageRevision"
Write-Host "Connection mode: WebSocket, slot $SlotId"
Write-Host "Remote code execution enabled: $([bool]$EnableRemoteCodeExecution)"
Write-Host "Start Revit 2026, then click 'Revit MCP Switch'."
