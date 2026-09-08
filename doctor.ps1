#Requires -Version 5.1
<#
.SYNOPSIS
  Diagnostics for claude-host-mcp on Windows.
#>
$ErrorActionPreference = "Continue"
$InstallDir = if ($env:HOST_MCP_INSTALL_DIR) { $env:HOST_MCP_INSTALL_DIR } else { Join-Path $env:USERPROFILE ".local\share\claude-host-mcp" }

Write-Host "=== OS ==="
[System.Environment]::OSVersion | Format-List | Out-String | Write-Host

Write-Host "`n=== PYTHON ==="
try { python --version } catch { python3 --version }

Write-Host "`n=== INSTALLED SERVER ==="
$bin = Join-Path $InstallDir ".venv\Scripts\claude-host-mcp.exe"
if (Test-Path $bin) { Get-Item $bin | Format-List | Out-String | Write-Host } else { Write-Host "not installed" }

Write-Host "`n=== MCP SDK ==="
$venvPy = Join-Path $InstallDir ".venv\Scripts\python.exe"
if (Test-Path $venvPy) {
  & $venvPy -c "import importlib.metadata; print('mcp', importlib.metadata.version('mcp')); from mcp.server import MCPServer; print('MCPServer import OK')"
}

Write-Host "`n=== SKILLS ==="
$SkillsDir = if ($env:HOST_MCP_SKILLS_DIR) { $env:HOST_MCP_SKILLS_DIR } else { Join-Path $env:USERPROFILE ".claude\skills" }
foreach ($s in @("morning-diagnose", "safe-deploy")) {
  $p = Join-Path $SkillsDir "$s\SKILL.md"
  if (Test-Path $p) { Write-Host "skill ${s}: installed ($p)" } else { Write-Host "skill ${s}: missing ($p)" }
}

Write-Host "`n=== CLAUDE CONFIG ==="
$config = if ($env:CLAUDE_DESKTOP_CONFIG) { $env:CLAUDE_DESKTOP_CONFIG } else { Join-Path $env:APPDATA "Claude\claude_desktop_config.json" }
Write-Host $config
if (Test-Path $config) {
  $d = Get-Content $config -Raw | ConvertFrom-Json
  $d.mcpServers.'host-system' | ConvertTo-Json -Depth 5 | Write-Host
} else {
  Write-Host "config not found"
}
