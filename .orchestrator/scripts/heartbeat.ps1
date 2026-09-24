# heartbeat.ps1 — actualiza el latido de una TASK en doing/.
# Uso: .orchestrator\scripts\heartbeat.ps1 TASK-001
param([Parameter(Mandatory = $true)][string]$Task)
if ($Task -match 'TASK-(\d+)') {
  (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ") | Out-File ".orchestrator\doing\.heartbeat-TASK-$($Matches[1])" -Encoding utf8
  "heartbeat $($Matches[0]) ok"
} else { "uso: heartbeat.ps1 TASK-<nnn>"; exit 1 }
