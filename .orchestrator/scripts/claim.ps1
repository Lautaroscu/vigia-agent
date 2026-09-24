# claim.ps1 — reclama la TASK más vieja de inbox/ de forma atómica.
# Uso: .orchestrator\scripts\claim.ps1 [worker] [role]
# Imprime la ruta en doing\ o EMPTY si no hay.
param([string]$Worker = "unknown", [string]$Role = "IMPLEMENTER")
$ErrorActionPreference = "Stop"
$orch = ".orchestrator"
$task = Get-ChildItem "$orch\inbox\TASK-*.md" -ErrorAction SilentlyContinue | Sort-Object Name | Select-Object -First 1
if (-not $task) { "EMPTY"; exit 2 }
$dest = Join-Path "$orch\doing" $task.Name
try {
  Move-Item $task.FullName $dest -ErrorAction Stop
  if ($task.Name -match 'TASK-(\d+)') {
    $nnn = $Matches[1]
    $stamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    "{`"task`":`"$($task.Name)`",`"worker`":`"$Worker`",`"role`":`"$Role`",`"claimed_at`":`"$stamp`"}`" | Out-File "$orch\doing\.claim-TASK-$nnn.json" -Encoding utf8
    $stamp | Out-File "$orch\doing\.heartbeat-TASK-$nnn" -Encoding utf8
  }
  $dest
} catch { "TAKEN"; exit 3 }
