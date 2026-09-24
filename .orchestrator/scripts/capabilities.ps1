# capabilities.ps1 — detecta qué CLIs de agentes hay en esta PC.
# Uso: .orchestrator\scripts\capabilities.ps1
foreach ($c in @('claude', 'gemini', 'opencode', 'codex')) {
  $cmd = Get-Command $c -ErrorAction SilentlyContinue
  if ($cmd) { $ver = (& $c --version 2>$null | Select-Object -First 1); "OK $c :: $ver" }
  else { "MISSING $c" }
}
