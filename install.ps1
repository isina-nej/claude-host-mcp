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
  [string]$ClaudeConfig = "",
  [string]$SkillsDir = (Join-Path $env:USERPROFILE ".claude\skills"),
  [switch]$SkipSkills
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

# Install bundled prompt skills into the user's Claude skills dir (idempotent).
if ($SkipSkills) {
  Say "Skipping skills install (-SkipSkills)."
} elseif (Test-Path (Join-Path $ProjectDir "skills")) {
  New-Item -ItemType Directory -Force -Path $SkillsDir | Out-Null
  Get-ChildItem -Directory (Join-Path $ProjectDir "skills") | ForEach-Object {
    $skill = $_.Name
    if ($skill -notmatch '^[a-z0-9-]+$') { Say "Skipping invalid skill name: $skill"; return }
    if (-not (Test-Path (Join-Path $_.FullName "SKILL.md"))) { Say "Skipping $skill (no SKILL.md)"; return }
    $dest = Join-Path $SkillsDir $skill
    $marker = Join-Path $dest "installed-by-host-mcp"
    if ((Test-Path $dest) -and -not (Test-Path $marker)) {
      $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
      Rename-Item $dest "$dest.backup-host-mcp-$stamp" -Force
      Say "Backed up existing skill: $skill"
    }
    if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
    Copy-Item $_.FullName $dest -Recurse -Force
    New-Item -ItemType File -Path $marker -Force | Out-Null
    Say "Skill installed: $skill"
  }
} else {
  Say "No bundled skills dir; skipping skills install."
}

$venvPy = Join-Path $InstallDir ".venv\Scripts\python.exe"
if (Get-Command uv -ErrorAction SilentlyContinue) {
  Say "Using uv"
  if (Test-Path "$InstallDir\.venv") {
    & uv venv --python python --clear "$InstallDir\.venv" | Out-Null
  } else {
    & uv venv --python python "$InstallDir\.venv" | Out-Null
  }
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
Say "Skills: morning-diagnose, safe-deploy (unless -SkipSkills)."
