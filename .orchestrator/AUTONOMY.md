# AUTONOMÍA — reglas para operar sin humano

Aplica a todos los roles. El objetivo: el humano escribe `GOAL.md` una vez y el pool trabaja solo hasta terminarlo. Leé este archivo después de tu guía de rol.

## 1. Reclamo atómico (obligatorio con 2+ agentes)

Nunca `mv` a mano si hay otro agente corriendo. Usá los helpers — el `mv`/`Move-Item` en el mismo disco es atómico y solo uno gana:

```bash
# Git Bash / Linux / Mac
sh .orchestrator/scripts/claim.sh        # imprime doing/TASK-xxx o "EMPTY"
sh .orchestrator/scripts/heartbeat.sh TASK-001   # actualiza latido
```

```powershell
# PowerShell
.orchestrator\scripts\claim.ps1          # imprime doing\TASK-xxx o "EMPTY"
.orchestrator\scripts\heartbeat.ps1 TASK-001
```

Si el helper dice `EMPTY`, dormí 60s y reintentá. Si dos agentes reclaman a la vez, el perdedor recibe `TAKEN` y pide otra. Nunca edites una TASK que no reclamaste vos.

## 2. Heartbeat (prueba de vida)

Todo task en `doing/` tiene que tener latido fresco: archivo `doing/.heartbeat-TASK-<nnn>` actualizado al menos cada **15 min** mientras trabajás. Sin latido por **2h** u ocupación de **24h**, el orquestador lo devuelve a `inbox/` sin preguntar y libera el claim (borra el `.claim-TASK-<nnn>.json`).

## 3. Una task por agente, siempre

Nunca tengas 2 tasks en `doing/` a tu nombre. Terminá o liberá (devolvé a `inbox/` + nota en `feedback/`) antes de reclamar otra.

## 4. Bloqueos sin humano

Si algo es ambiguo o te faltan datos: NO adivines y NO te quedes colgado.
1. Escribí `.orchestrator/feedback/BLOCKED-TASK-<nnn>.md` (qué te falta, opciones, tu recomendación).
2. Devolvé la TASK a `inbox/`.
3. Seguí con la próxima. El orquestador resuelve el bloqueo (o lo parte en otra TASK) en su loop.

Máximo **3 intentos** por TASK; al tercero, escala a `feedback/` y pasa a otra.

## 5. Auto-merge (solo orquestador)

Merge automático a `main` solo si: `done/TASK-<nnn>.result.md` + `review: APROBADO` (de modelo distinto al implementador) + `feedback/TEST-TASK-<nnn>.md` con `PASS`. Si falta algo, no mergea: deja nota y sigue el loop.

## 6. Loops autónomos

- Orquestador: seguí `.orchestrator/ORCHESTRATOR_LOOP.md` en tu proceso (o terminal dedicada) hasta que `GOAL.md` esté completo.
- Implementador/Revisor/Tester: seguí `.orchestrator/WORKER_LOOP.md` — el loop no termina cuando acabás una TASK, termina cuando `inbox/` lleva 10 min vacío Y `doing/` ajeno también está quieto.
