#Requires -Version 5.1
<#
.SYNOPSIS
  Uninstall claude-host-mcp on Windows: remove config entry + runtime.
#>
[CmdletBinding()]
param(
  [string]$InstallDir = (Join-Path $env:USERPROFILE ".local\share\claude-host-mcp"),
  [string]$ClaudeConfig = ""
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command python3 -ErrorAction SilentlyContinue }
if (-not $py) { Write-Error "Python not found."; exit 1 }

if (-not $ClaudeConfig) {
  if ($env:CLAUDE_DESKTOP_CONFIG) { $ClaudeConfig = $env:CLAUDE_DESKTOP_CONFIG }
  else { $ClaudeConfig = Join-Path $env:APPDATA "Claude\claude_desktop_config.json" }
}

& $py.Source - @"
import datetime, json, pathlib, shutil
cfg = pathlib.Path(r'''$ClaudeConfig''')
if cfg.exists():
    try:
        data = json.loads(cfg.read_text(encoding='utf-8'))
    except Exception as e:
        print(f'Skipping invalid config {cfg}: {e}')
    else:
        servers = data.get('mcpServers')
        if isinstance(servers, dict) and 'host-system' in servers:
            stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
            backup = cfg.with_name(cfg.name + '.backup-uninstall-host-mcp-' + stamp)
            shutil.copy2(cfg, backup)
            del servers['host-system']
            if not servers:
                data.pop('mcpServers', None)
            cfg.write_text(json.dumps(data, ensure_ascii=False, indent=2) + chr(10), encoding='utf-8')
            print('Removed host-system from:', cfg)
            print('Backup:', backup)
"@

if (Test-Path $InstallDir) { Remove-Item -Recurse -Force $InstallDir }
Write-Host "Removed $InstallDir"
Write-Host "Restart Claude Desktop to finish uninstalling."
