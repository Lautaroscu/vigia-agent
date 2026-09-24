# Orquestador file-queue SDD casero (por repo)

Esta carpeta es el contrato entre el **orquestador** (el mas inteligente) y el **pool de agentes** (implementadores / revisores / testers). Todo por archivos, sin APIs obligatorias. Funciona con Claude Pro $20, Gemini Pro $20, futuro OpenAI $20 y OpenCode Go $10.

## Ciclo de vida

```
inbox/    <- orquestador escribe TASK-xxx.md (estado: pendiente)
doing/    <- worker lo mueve aqui al tomarlo (estado: en curso, 1 worker por task)
done/     <- worker deja TASK-xxx.result.md + diff/resumen (estado: hecho)
feedback/ <- orquestador o revisor deja observaciones, si hay que reabrir
```

Reglas:
1. Nunca editar un archivo en `inbox/` directamente. Mover a `doing/` con `git mv` o `mv`.
2. Un TASK = una rama/worktree. Ej: `git worktree add ../w-TASK-012 -b task/TASK-012`.
3. El worker nunca mergea a `main`. Solo deja resultado en `done/`.
4. El orquestador valida contra los criterios de aceptacion y mergea o devuelve a `inbox/` con nota en `feedback/`.
5. Nombres fijos: `TASK-<nnn>-<slug-corto>.md` y `TASK-<nnn>.result.md`.

## Como orquestar (Claude / Gemini / Codex / OpenCode)

Cualquier CLI con acceso a la carpeta puede ser orquestador o worker. Ejemplo con Claude Code:

```bash
# Orquestador deja tarea
cp .orchestrator/TASK.TEMPLATE.md .orchestrator/inbox/TASK-001-login.md
# Worker (en otra terminal / agente background) la toma
mv .orchestrator/inbox/TASK-001-login.md .orchestrator/doing/
# ... trabaja en worktree ...
# ... al terminar deja resultado
cp /tmp/TASK-001.notas.md .orchestrator/done/TASK-001.result.md
```

Polling simple (cada 15s) para workers sin MCP:

```bash
while true; do ls .orchestrator/inbox/; sleep 15; done
```

En Windows PowerShell:

```powershell
while ($true) { Get-ChildItem .orchestrator\inbox; Start-Sleep 15 }
```

## Integracion con Headroom (:8787)

Este repo puede pasar por el proxy para ahorrar tokens. Si usas un SDK OpenAI-compatible apuntalo a `http://127.0.0.1:8787/v1`. El dashboard muestra por proyecto el ahorro. No es obligatorio para el file-queue.

## Limpieza

`doing/` con mas de 24h se considera abandonado: el orquestador puede devolverlo a `inbox/`.
`done/` y `feedback/` no se borran, son auditoria.
