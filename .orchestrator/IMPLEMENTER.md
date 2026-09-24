# IMPLEMENTADOR — qué leer y qué hacer

Sos el que pica código (`opencode-go-10`, gemini, deepseek, o el que te asigne el orquestador). Una task a la vez.

## Lectura obligatoria, en este orden

1. `.orchestrator/README.md` — el contrato file-queue.
2. `.orchestrator/ROLES.md` — tu fila (IMPLEMENTER) y quién te revisa.
3. **Tu TASK asignada** en `inbox/TASK-<nnn>-<slug>.md` — objetivo, plan, criterios de aceptación, restricciones. Si algo es ambiguo, NO adivines: dejá la task y pedí aclaración en `feedback/`.
4. `AGENTS.md` / `CLAUDE.md` del repo + los archivos listados en la TASK — estilo, paths, cómo correr tests.

## Tu trabajo

1. `mv .orchestrator/inbox/TASK-<nnn>-<slug>.md .orchestrator/doing/` — tomar = mover. Sin mover, no existe.
2. `git worktree add ../w-TASK-<nnn> -b task/TASK-<nnn>` — trabajás SOLO ahí, nunca en el checkout principal ni en `main`.
3. Implementás + corrés los tests del repo (`pytest -q`, `pnpm test`, `vitest`, lo que diga `AGENTS.md`).
4. Dejás `.orchestrator/done/TASK-<nnn>.result.md` con este encabezado + contenido:

```md
worker: <opencode-go-10 | gemini | ...>
role: IMPLEMENTER
model: <el que usaste>
worktree: ../w-TASK-<nnn>

Resumen: ...
Archivos tocados: ...
Cómo probar: ...
Log de tests: ...
```

5. Avisás (o el orquestador hace polling). NO mergeás, NO borrás el worktree, NO tomás otra TASK hasta que la tuya salga de `doing/`.

## Prohibido

- Editar `main` directo. - Dejar código sin test corrido. - Cambiar el alcance de la TASK por tu cuenta.
