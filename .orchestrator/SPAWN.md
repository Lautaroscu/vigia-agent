# SPAWN — el orquestador lanza a los workers (el humano no)

Modelo operativo: **una sola sesión = el orquestador** (tu Claude abierto). Él parte el GOAL en TASKs y **lanza los workers él mismo** en background. Nada queda "escuchando" 24/7: es on-demand por GOAL, no un daemon.

## Setup único (humano, una vez por PC)

```bash
command -v claude opencode gemini codex   # los 4 tienen que existir
opencode run --dir . "decí hola"          # verifica auth + que NO pida confirmación
```

- `claude`: ya logueado con tu Pro.
- `opencode`: `opencode providers` para credenciales; política de permisos en auto-accept para que el worker no se bloquee preguntando (verificá con el run de prueba).
- `gemini` / `codex`: instalar + login cuando los sumes; comandos abajo, verificá flags con `--help` (cambian seguido).

## Cómo los lanza el orquestador (desde su sesión, herramienta Bash en background)

Un worker = un proceso en background corriendo en la carpeta del repo, con rol y nombre únicos:

```bash
# worker implementador con opencode (disponible hoy)
opencode run --dir "C:/Users/HWLScuffi/workspace/<repo>" -m "<provider>/<model>" \
  "Lee .orchestrator/START.md rol=IMPLEMENTER worker=w1 y empezá" \
  > .orchestrator/workers/w1.log 2>&1 &
```

```bash
# cuando los instales:
gemini -p "Lee .orchestrator/START.md rol=IMPLEMENTER worker=w2 y empezá"  # + flag auto-approve (ver gemini --help)
codex exec "Lee .orchestrator/START.md rol=TESTER worker=t1 y empezá"      # + política de aprobación (ver codex exec --help)
```

En Claude Code usá `run_in_background: true` en vez de `&` para poder ver el output después.

## Reglas del orquestador al lanzar

1. **Máximo 2–3 workers** en paralelo (tu PC: CPU + docker + suites compiten).
2. `worker=` único por proceso (`w1`, `w2`, `t1`...). Nunca dos procesos con el mismo nombre.
3. Log por worker en `.orchestrator/workers/<worker>.log` (recomendado: agregar `.orchestrator/workers/` al `.gitignore`).
4. El worker **termina solo** cuando su loop termina (inbox vacío + doing quieto) — el proceso muere, nada queda colgado.
5. Stray/orphan: si un proceso murió, su TASK vuelve sola a `inbox/` por heartbeat stale (ver `AUTONOMY.md`). No hay que "limpiar escuchas".
6. REVIEWER = sesión con **modelo distinto** al implementador de esa TASK.

## Respuesta a la pregunta del dueño

No tenés que dejar sesiones de gemini/opencode/codex escuchando cambios. Abrís tu Claude, le decís el objetivo ("sacá el GOAL de kibo"), y él lanza, monitorea, valida y mergea. Vos solo aparecés en los gates (`feedback/GATE-*.md`) — y en `serra-forge`, ni siquiera: solo Gate 1 y 2.
