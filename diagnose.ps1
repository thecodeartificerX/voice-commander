#Requires -Version 5.1
<#
.SYNOPSIS
    Probe voice-commander remote endpoints (transcription + LLM) and report
    health, latency, and server kind. ASCII-only output for PS 5.1 safety.

.DESCRIPTION
    Hits hard-coded endpoints from config.toml so a fresh shell with no env
    can run this. For each endpoint:
      * TCP connect (catches firewall / wrong port / dead host fast)
      * HTTP probe (expected status code per kind)
      * For LLM: GET /models, detect server kind (vLLM / Ollama / LM Studio /
        unknown), then POST /chat/completions with reasoning_effort=none and
        time it. Flag <think> leak in content (means a reasoning model is
        loaded -- not the gemma4:e4b vLLM endpoint we expect).

    Run via diagnose.bat (double-click) or directly: .\diagnose.ps1
#>

[CmdletBinding()] param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Continue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# ---------------------------------------------------------------------------
# Endpoints (mirror config.toml + start.ps1 LlmPresets)
# ---------------------------------------------------------------------------

$TranscriptionUrl = 'http://192.168.4.200:8765/inference'

$LlmTargets = @(
    [PSCustomObject]@{
        Label = 'Remote vLLM'
        Url   = 'http://192.168.4.200:5055/v1'
        Model = 'gemma4:e4b'
    },
    [PSCustomObject]@{
        Label = 'Remote Ollama (preset port 5050)'
        Url   = 'http://192.168.4.200:5050/v1'
        Model = 'gemma4:e4b'
    }
)

# ---------------------------------------------------------------------------
# Color helpers
# ---------------------------------------------------------------------------

function W-Head { param([string]$T) Write-Host $T -ForegroundColor Cyan }
function W-OK   { param([string]$T) Write-Host $T -ForegroundColor Green }
function W-Warn { param([string]$T) Write-Host $T -ForegroundColor Yellow }
function W-Bad  { param([string]$T) Write-Host $T -ForegroundColor Red }
function W-Dim  { param([string]$T) Write-Host $T -ForegroundColor DarkGray }
function W-Info { param([string]$T) Write-Host $T -ForegroundColor Gray }

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

function Get-HostPort {
    param([string]$Url)
    $u = [uri]$Url
    return [PSCustomObject]@{ HostName = $u.Host; Port = $u.Port }
}

function Test-TcpPort {
    param([string]$HostName, [int]$Port, [int]$TimeoutMs = 1500)
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $task = $client.ConnectAsync($HostName, $Port)
        if ($task.Wait($TimeoutMs) -and $client.Connected) { return $true }
        return $false
    } catch {
        return $false
    } finally {
        try { $client.Close() } catch {}
    }
}

function Get-StatusCode {
    param($ErrorRecord)
    try { return [int]$ErrorRecord.Exception.Response.StatusCode } catch { return 0 }
}

# ---------------------------------------------------------------------------
# Transcription probe
# ---------------------------------------------------------------------------

function Test-Transcription {
    param([string]$Url)
    W-Head ("[Transcription] {0}" -f $Url)
    $hp = Get-HostPort $Url
    if (-not (Test-TcpPort -HostName $hp.HostName -Port $hp.Port)) {
        W-Bad ("  TCP {0}:{1} UNREACHABLE" -f $hp.HostName, $hp.Port)
        W-Dim '  -> server down, wrong IP, or firewall blocking'
        return
    }
    W-OK '  TCP open'

    # whisper.cpp /inference returns 400/500 on empty POST -- proves it is alive
    try {
        $sw = [Diagnostics.Stopwatch]::StartNew()
        $r = Invoke-WebRequest -Uri $Url -Method Post -TimeoutSec 5 -UseBasicParsing -ErrorAction Stop
        $sw.Stop()
        W-OK ("  POST -> HTTP {0} in {1} ms (alive)" -f $r.StatusCode, $sw.ElapsedMilliseconds)
    } catch {
        $code = Get-StatusCode $_
        if ($code -ge 400 -and $code -lt 600) {
            W-OK ("  POST -> HTTP {0} (whisper.cpp alive, expects multipart audio body)" -f $code)
        } else {
            W-Bad ("  POST failed: {0}" -f $_.Exception.Message)
        }
    }
}

# ---------------------------------------------------------------------------
# LLM probe
# ---------------------------------------------------------------------------

function Test-Llm {
    param([string]$Url, [string]$Model, [string]$Label)

    Write-Host ''
    W-Head ("[LLM:{0}] {1}  model={2}" -f $Label, $Url, $Model)
    $hp = Get-HostPort $Url
    if (-not (Test-TcpPort -HostName $hp.HostName -Port $hp.Port)) {
        W-Bad ("  TCP {0}:{1} UNREACHABLE" -f $hp.HostName, $hp.Port)
        return
    }
    W-OK '  TCP open'

    $base = $Url.TrimEnd('/')
    $root = $base -replace '/v1$', ''

    # Server kind detection
    $kind = 'unknown (OpenAI-compat)'
    try {
        $ver = Invoke-RestMethod -Uri "$root/version" -TimeoutSec 2 -ErrorAction Stop
        if ($ver.PSObject.Properties.Name -contains 'version') {
            $kind = "vLLM $($ver.version)"
        }
    } catch {}
    if ($kind -eq 'unknown (OpenAI-compat)') {
        try {
            $ollama = Invoke-RestMethod -Uri "$root/api/tags" -TimeoutSec 2 -ErrorAction Stop
            if ($ollama.PSObject.Properties.Name -contains 'models') {
                $kind = 'Ollama (native + OpenAI-compat at /v1)'
            }
        } catch {}
    }
    if ($kind -eq 'unknown (OpenAI-compat)') {
        try {
            $r = Invoke-WebRequest -Uri "$root/api/v0/models" -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop
            if ($r.StatusCode -eq 200) { $kind = 'LM Studio' }
        } catch {}
    }
    W-Info ("  Server kind: {0}" -f $kind)

    # GET /models
    try {
        $sw = [Diagnostics.Stopwatch]::StartNew()
        $models = Invoke-RestMethod -Uri "$base/models" -TimeoutSec 5 -ErrorAction Stop
        $sw.Stop()
        $ids = @()
        if ($models.PSObject.Properties.Name -contains 'data') {
            $ids = @($models.data | ForEach-Object { $_.id })
        }
        W-OK ("  GET /models -> {0} models in {1} ms" -f $ids.Count, $sw.ElapsedMilliseconds)
        foreach ($id in $ids) { W-Dim ("    - {0}" -f $id) }
        if ($ids -notcontains $Model) {
            W-Warn ("  WARN: configured model '{0}' not in /models list" -f $Model)
        }
    } catch {
        W-Bad ("  GET /models failed: {0}" -f $_.Exception.Message)
        return
    }

    # POST /chat/completions tiny prompt
    $body = @{
        model            = $Model
        messages         = @(@{ role = 'user'; content = 'Reply with the single word OK and nothing else.' })
        max_tokens       = 16
        temperature      = 0
        reasoning_effort = 'none'
    } | ConvertTo-Json -Depth 6 -Compress

    try {
        $sw = [Diagnostics.Stopwatch]::StartNew()
        $resp = Invoke-RestMethod -Uri "$base/chat/completions" `
            -Method Post -ContentType 'application/json' `
            -Body $body -TimeoutSec 60 -ErrorAction Stop
        $sw.Stop()
        $ms = $sw.ElapsedMilliseconds

        $msg     = $resp.choices[0].message
        $content = if ($msg.PSObject.Properties.Name -contains 'content') { $msg.content } else { '' }
        $reason  = if ($msg.PSObject.Properties.Name -contains 'reasoning_content') { $msg.reasoning_content } else { $null }

        W-OK ("  POST /chat/completions -> {0} ms" -f $ms)
        $oneLine = ($content -replace "`r?`n", ' \n ')
        W-Info ("    content : {0}" -f $oneLine)

        if ($reason) {
            W-Warn ("    reasoning_content present ({0} chars) -- reasoning model. router asks for none, server still emitted." -f $reason.Length)
        }
        if ($content -match '^\s*<think>' -or $content -match '<think>') {
            W-Bad   "    <think> token in content -- THIS IS NOT THE GEMMA vLLM ENDPOINT"
            W-Bad   "    Likely a reasoning model leaking thoughts. Router will time out at 5s."
        }

        if ($ms -gt 5000) {
            W-Warn ("    WARN: {0} ms exceeds llm.timeout_ms=5000 in config.toml" -f $ms)
            W-Dim  '         (first call cold-loads; second call is the real measurement)'
        } else {
            W-OK   '    Latency within 5s timeout budget.'
        }
    } catch {
        W-Bad ("  POST /chat/completions failed: {0}" -f $_.Exception.Message)
    }
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

Write-Host ''
W-Head '================================================================'
W-Head '  voice-commander endpoint diagnostics'
W-Head '================================================================'
Write-Host ''

Test-Transcription -Url $TranscriptionUrl

foreach ($t in $LlmTargets) {
    Test-Llm -Url $t.Url -Model $t.Model -Label $t.Label
}

Write-Host ''
W-Head '================================================================'
W-Head '  Hints'
W-Head '================================================================'
W-Dim '  - <think> in content    -> wrong model loaded (reasoning model). Switch to gemma4:e4b on vLLM.'
W-Dim '  - latency > 5000 ms     -> bump llm.timeout_ms + llm.warmup_timeout_ms in config.toml.'
W-Dim '  - TCP UNREACHABLE       -> server down, wrong port, or firewall.'
W-Dim '  - model not in /models  -> wrong model_id in config.toml.'
Write-Host ''
