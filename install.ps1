#Requires -Version 5.1
<#
.SYNOPSIS
  Install claude-host-mcp on Windows: venv + MCP entry + Claude Desktop config.
.PARAMETER InstallDir
  Override install location. Default: $env:USERPROFILE\.local\share\claude-host-mcp
.PARAMETER ClaudeConfig
  Override Claude Desktop config path.
#>
[CmdletBinding()]
param(
  [string]$InstallDir = (Join-Path $env:USERPROFILE ".local\share\claude-host-mcp"),
  [string]$ClaudeConfig = ""
)

$ErrorActionPreference = "Stop"

function Say($m) { Write-Host $m }
function Fail($m) { Write-Error $m; exit 1 }

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path

$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command python3 -ErrorAction SilentlyContinue }
if (-not $py) { Fail "Python 3.10+ is required (python/python3 not found)." }

& $py.Source -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)"
if ($LASTEXITCODE -ne 0) { Fail "Python 3.10+ is required." }

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Copy-Item (Join-Path $ProjectDir "pyproject.toml") (Join-Path $InstallDir "pyproject.toml") -Force
if (Test-Path (Join-Path $InstallDir "src")) { Remove-Item -Recurse -Force (Join-Path $InstallDir "src") }
Copy-Item (Join-Path $ProjectDir "src") (Join-Path $InstallDir "src") -Recurse -Force

$venvPy = Join-Path $InstallDir ".venv\Scripts\python.exe"
if (Get-Command uv -ErrorAction SilentlyContinue) {
  Say "Using uv"
  & uv venv --python python "$InstallDir\.venv" | Out-Null
  & uv pip install --python $venvPy -e $InstallDir
} else {
  Say "uv not found; using venv + pip"
  & $py.Source -m venv "$InstallDir\.venv"
  & $venvPy -m pip install --upgrade pip | Out-Null
  & $venvPy -m pip install -e $InstallDir
}

$serverBin = Join-Path $InstallDir ".venv\Scripts\claude-host-mcp.exe"
if (-not (Test-Path $serverBin)) { Fail "Entry point not found: $serverBin" }

if (-not $ClaudeConfig) {
  if ($env:CLAUDE_DESKTOP_CONFIG) {
    $ClaudeConfig = $env:CLAUDE_DESKTOP_CONFIG
  } else {
    $ClaudeConfig = Join-Path $env:APPDATA "Claude\claude_desktop_config.json"
  }
}
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $ClaudeConfig) | Out-Null

& $py.Source - @"
import datetime, json, os, pathlib, shutil
cfg = pathlib.Path(r'''$ClaudeConfig''')
server = r'''$serverBin'''
data = {}
if cfg.exists():
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    backup = cfg.with_name(cfg.name + '.backup-host-mcp-' + stamp)
    shutil.copy2(cfg, backup)
    print('Config backup:', backup)
    data = json.loads(cfg.read_text(encoding='utf-8'))
servers = data.setdefault('mcpServers', {})
servers['host-system'] = {'command': server, 'args': [], 'env': {'HOST_MCP_LOG_LEVEL': 'WARNING'}}
cfg.write_text(json.dumps(data, ensure_ascii=False, indent=2) + chr(10), encoding='utf-8')
print('Claude config:', cfg)
print('MCP server: host-system')
"@

Say ""
Say "Installed. Fully restart Claude Desktop, then new session."
Say "Test: Use the host-system MCP tool host_identity."
