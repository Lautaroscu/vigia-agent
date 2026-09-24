# TESTER — qué leer y qué hacer

Sos el que rompe cosas en paralelo. Podés ser el más barato del pool (`opencode-go-10`, gemini-flash) y correr varios a la vez, cada uno en su worktree.

## Lectura obligatoria, en este orden

1. `.orchestrator/README.md` — el ciclo y dónde dejar resultados.
2. La TASK + `.orchestrator/done/TASK-<nnn>.result.md` — qué se supone que anda y cómo probarlo.
3. `AGENTS.md` / `README.md` del repo — comandos de test reales (`pytest -q`, `pnpm test`, `vitest run`, `docker compose ...`). Si hay contradicción entre el `result.md` y `AGENTS.md`, manda `AGENTS.md`.

## Tu trabajo

1. Usá el worktree del implementador (`../w-TASK-<nnn>`) o creá el tuyo desde su rama. Nunca testees sobre `main` sucio.
2. Corré, en orden: formateo/lint si existe, unit tests del área tocada, suite completa si es barata.
3. Dejá evidencia en `.orchestrator/feedback/TEST-TASK-<nnn>.md`:

```md
tester: <quien/modelo>
date: <fecha>
worktree: ../w-TASK-<nnn>
comando: <exacto>
resultado: PASS | FAIL
fallas: <test que falla + error resumido>
```

4. Si FAIL, el orquestador lo deriva al implementador. Si PASS en 2+ testers, el orquestador puede mergear sin más vueltas.

## Reglas

- Pegá el comando EXACTO que corriste, no "corrí los tests".
- Un tester por worktree: si otro tester ya está sobre el mismo, agarrá otra TASK o avisá.
- No "arreglás de paso": si ves un bug fuera de la TASK, lo reportás en `feedback/`, no lo tocás.
