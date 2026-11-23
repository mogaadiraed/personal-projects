<#
CarbonSync AI - stops everything the dashboard runs on this PC (called by stop.bat).

1. Dashboard servers: Python processes running streamlit_app.py, with everything they started
   (pipeline runs, forecasts and Prophet's CmdStan processes, the model evaluation, assistant work).
2. CarbonSync commands started from a terminal: python -m carbonsync.* (pipeline, backtest,
   assistant, evaluation) and Python scripts of this project folder.
3. Local models: every model Ollama holds in memory is unloaded (frees the GPU), then Ollama is
   closed, unless -KeepOllama is given. The dashboard's Start button (AI & API > Ollama) or the
   Start menu opens Ollama again.

Only Python processes are matched, and only on their arguments (not the interpreter path), so
editors, terminals and language servers opened on this folder are left alone.
Keep this file ASCII: Windows PowerShell 5.1 reads scripts without a BOM in the ANSI code page.
#>
param([switch]$KeepOllama)

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path.TrimEnd('\')

Write-Host 'CarbonSync AI - stopping everything'
Write-Host ''

# --- 1 + 2: dashboard servers and CarbonSync commands ------------------------------------------
$all = @(Get-CimInstance Win32_Process)
$children = @{}
foreach ($p in $all) {
    $key = [int]$p.ParentProcessId
    if (-not $children.ContainsKey($key)) { $children[$key] = New-Object System.Collections.ArrayList }
    [void]$children[$key].Add($p)
}

function Get-Arguments([string]$commandLine) {
    # Drops the executable (quoted or not) and keeps what it was asked to run.
    return ($commandLine -replace '^\s*("[^"]*"|\S+)\s*', '')
}

function Get-Kind($proc) {
    if ($proc.Name -notmatch '^(python|pythonw|py|streamlit)\.exe$' -or -not $proc.CommandLine) { return $null }
    $a = Get-Arguments $proc.CommandLine
    if ($a -match 'streamlit_app\.py') { return 'dashboard' }
    if ($a -match '(^|\s)-m\s+carbonsync(\.|\s|$)') { return 'command' }
    if ($a.IndexOf($root, [StringComparison]::OrdinalIgnoreCase) -ge 0) { return 'command' }
    return $null
}

$targets = @($all | Where-Object { $_.ProcessId -ne $PID -and (Get-Kind $_) })
# A streamlit.exe launcher and the python it starts both match: keep only the top of each tree.
$targetIds = @($targets | ForEach-Object { [int]$_.ProcessId })
$tops = @($targets | Where-Object { $targetIds -notcontains [int]$_.ParentProcessId })

function Get-Descendants([int]$id) {
    $out = @()
    if ($children.ContainsKey($id)) {
        foreach ($c in $children[$id]) { $out += $c; $out += Get-Descendants ([int]$c.ProcessId) }
    }
    return $out
}

if ($tops.Count -eq 0) {
    Write-Host '  Dashboard        : not running'
} else {
    foreach ($t in $tops) {
        $id = [int]$t.ProcessId
        $kind = Get-Kind $t
        if ($kind -eq 'dashboard') {
            $ports = @(Get-NetTCPConnection -State Listen -OwningProcess $id -ErrorAction SilentlyContinue |
                       Select-Object -ExpandProperty LocalPort -Unique)
            $what = if ($ports.Count) { 'Dashboard on http://localhost:' + ($ports -join ', ') } else { 'Dashboard' }
        } else {
            $what = 'Command: ' + (Get-Arguments $t.CommandLine)
            if ($what.Length -gt 90) { $what = $what.Substring(0, 87) + '...' }
        }
        # Children first (CmdStan, workers), never Ollama: it is handled below.
        $tree = @(Get-Descendants $id | Where-Object { $_.Name -notmatch '^ollama' })
        [array]::Reverse($tree)
        foreach ($c in $tree) { Stop-Process -Id ([int]$c.ProcessId) -Force -ErrorAction SilentlyContinue }
        Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
        $extra = if ($tree.Count) { " and $($tree.Count) background process(es)" } else { '' }
        Write-Host "  Stopped          : $what (PID $id)$extra"
    }
}

# --- 3: local models and Ollama ----------------------------------------------------------------
$ollamaHost = 'http://127.0.0.1:11434'
if ($env:OLLAMA_HOST) {
    $ollamaHost = if ($env:OLLAMA_HOST -match '^https?://') { $env:OLLAMA_HOST } else { 'http://' + $env:OLLAMA_HOST }
    $ollamaHost = $ollamaHost.TrimEnd('/') -replace '://0\.0\.0\.0', '://127.0.0.1'
}

$running = $true
try {
    $ps = Invoke-RestMethod -Uri "$ollamaHost/api/ps" -TimeoutSec 3
} catch {
    $running = $false
}

if (-not $running) {
    Write-Host '  Local models     : Ollama is not running, the GPU holds no model'
} else {
    $models = @($ps.models)
    if ($models.Count -eq 0) {
        Write-Host '  Local models     : none in memory'
    } else {
        foreach ($m in $models) {
            $gb = [math]::Round(([double]$m.size_vram) / 1e9, 1)
            try {
                $body = @{ model = $m.name; keep_alive = 0 } | ConvertTo-Json
                $null = Invoke-RestMethod -Uri "$ollamaHost/api/generate" -Method Post -Body $body `
                                          -ContentType 'application/json' -TimeoutSec 30
                Write-Host "  Unloaded         : $($m.name) ($gb GB of GPU memory freed)"
            } catch {
                Write-Host "  Could not unload : $($m.name) - closing Ollama frees it"
            }
        }
    }
}

if ($KeepOllama) {
    if ($running) { Write-Host '  Ollama           : left open (keep-ollama)' }
} else {
    $ollama = @(Get-Process -Name 'ollama app', 'ollama', 'ollama_llama_server' -ErrorAction SilentlyContinue)
    if ($ollama.Count -eq 0) {
        Write-Host '  Ollama           : not running'
    } else {
        $ollama | Stop-Process -Force -ErrorAction SilentlyContinue
        Write-Host "  Ollama           : closed ($($ollama.Count) process(es))"
    }
}

Write-Host ''
Write-Host 'Done. Start again with start.bat.'
