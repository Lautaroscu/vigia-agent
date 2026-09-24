# START — una tecla y a trabajar

Este archivo es la única instrucción que necesita cada agente. El humano solo escribe el QUÉ (`GOAL.md` o una TASK en `inbox/`); de ahí en más, los agentes corren solos.

## Orquestador (1 sesión por repo)

```bash
claude "Lee .orchestrator/START.md rol=ORCHESTRATOR worker=orq-1 y empezá"
# o: gemini "Lee .orchestrator/START.md rol=ORCHESTRATOR worker=orq-1 y empezá"
# o: opencode run "Lee .orchestrator/START.md rol=ORCHESTRATOR worker=orq-1 y empezá"
```

## Worker (N sesiones en paralelo, distinto `worker=` cada una)

```bash
claude "Lee .orchestrator/START.md rol=IMPLEMENTER worker=w1 y empezá"
claude "Lee .orchestrator/START.md rol=TESTER worker=t1 y empezá"
# REVIEWER lo hace el orquestador u otra sesión con modelo distinto al implementador
```

> Autonomía total = correr con auto-accept de permisos (ej: `claude --dangerously-skip-permissions`).
> Hacelo solo en worktrees o repos donde un error sea barato; el orquestador nunca pushea, solo mergea local.

## Qué hace el agente al leer esto (no saltear pasos)

1. Lee `.orchestrator/README.md`, `.orchestrator/ROLES.md`, `.orchestrator/config.json`.
2. Lee su guía: `ORCHESTRATOR.md` / `IMPLEMENTER.md` / `REVIEWER.md` / `TESTER.md`.
3. Lee `.orchestrator/AUTONOMY.md` + `.orchestrator/LOCAL.md` (la PC es tu taller: tests, docker efímero, etiqueta en paralelo) + su loop (`ORCHESTRATOR_LOOP.md` o `WORKER_LOOP.md`).
4. Lee `GOAL.md` (si existe) y lista `inbox/ doing/ done/ feedback/`.
5. Entra al loop de su rol y **no para** hasta la condición de fin (inbox vacío + doing quieto + done validado). No pide confirmaciones: decide y deja rastro en `feedback/`.

## Flujo diario SDD (humano)

- **Feature nueva**: escribí `GOAL.md` (de `GOAL.TEMPLATE.md`) → lanzá 1 orquestador + 2 workers → volvé cuando esté mergeado.
- **Refactor de módulo**: `GOAL.md` con alcance + `owns` del módulo → mismo loop (los workers no tocan fuera de `owns`).
- **Bug**: una TASK en `inbox/` con reproducción + criterio → 1 worker + tester la cierran solos.
