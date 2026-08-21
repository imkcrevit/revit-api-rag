[CmdletBinding()]
param(
    [string]$ServerUrl = "wss://graptolite.ai/api/v1/bridge/ws",
    [ValidatePattern('^[1-5]$')]
    [string]$SlotId = "1",
    [switch]$EnableRemoteCodeExecution
)

$ErrorActionPreference = "Stop"
$packageRevision = "2026-08-21-autoxec2"
$expectedCommandSetSha256 = "49ccdce6ba5ca3010a30a6714d6d18cc30d08dbaf638979d355977d17e958f27"

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

$commandDllDestination = Join-Path $commandsDestination "RevitMCPCommandSet\2026\RevitMCPCommandSet.dll"
if (-not (Test-Path -LiteralPath $commandDllDestination)) {
    throw "Installed command DLL is missing: $commandDllDestination"
}

$installedCommandSetSha256 = (Get-FileHash -LiteralPath $commandDllDestination -Algorithm SHA256).Hash.ToLowerInvariant()
if ($installedCommandSetSha256 -ne $expectedCommandSetSha256) {
    throw "Installed command DLL verification failed. Expected $expectedCommandSetSha256 but found $installedCommandSetSha256"
}

# Detect another active manifest that could make Revit load a machine-wide or
# per-user copy instead of the package verified above. Backup files do not end
# in .addin and are intentionally ignored.
$manifestRoots = @(
    $addinsRoot,
    (Join-Path $env:ProgramData "Autodesk\Revit\Addins\2026")
) | Select-Object -Unique
$activePluginManifests = @()
foreach ($manifestRoot in $manifestRoots) {
    if (-not (Test-Path -LiteralPath $manifestRoot)) {
        continue
    }
    foreach ($manifest in Get-ChildItem -LiteralPath $manifestRoot -Filter "*.addin" -File -ErrorAction SilentlyContinue) {
        if (Select-String -LiteralPath $manifest.FullName -SimpleMatch "revit_mcp_plugin.Core.Application" -Quiet) {
            $activePluginManifests += $manifest.FullName
        }
    }
}

Write-Host "Revit 2026 plugin installed: $pluginDestination"
Write-Host "Package revision: $packageRevision"
Write-Host "Command DLL SHA-256: $installedCommandSetSha256"
Write-Host "Connection mode: WebSocket, slot $SlotId"
Write-Host "Remote code execution enabled: $([bool]$EnableRemoteCodeExecution)"
if ($activePluginManifests.Count -gt 1) {
    Write-Warning "Multiple active Revit MCP manifests were found. Revit may load a different plugin copy:"
    foreach ($manifestPath in $activePluginManifests) {
        Write-Warning "  $manifestPath"
    }
}
Write-Host "Start Revit 2026, then click 'Revit MCP Switch'."
