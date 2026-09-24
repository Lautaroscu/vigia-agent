# LOCAL — la PC es tu taller (vale para todos los roles)

Tenés una máquina real, no un chat. Actuá como un ingeniero del equipo: **corré las cosas, no las adivines**. Un test en verde vale más que mil líneas de análisis.

## 1. Tu escritorio = tu worktree

Cada agente trabaja en SU worktree (`../w-TASK-<nnn>`). Nunca `switch`, nunca tocar el checkout de otro, nunca leer código en vuelo ajeno para "ahorrar tiempo" (acopla y rompe contratos).

## 2. Tests: primero lo chico, después todo

1. Corré primero los tests del área que tocaste (rápido, iterá).
2. Antes de dar por hecha la TASK: la suite completa del repo (`AGENTS.md` manda: `pytest -q`, `pnpm test`, `vitest run`, `pnpm verify`...).
3. Pegá el comando EXACTO + resultado en tu `result.md` / `TEST-*.md`. "Pasan los tests" sin log = no pasaron.

## 3. Docker efímero (nada permanente, nada compartido)

- Levantá dependencias de test como contenedores **efímeros**: `docker compose -f <test>.yml up -d`, testcontainers, o lo que use el repo. Jamás toques compose/env de producción.
- **Un stack por TASK**: nombre de proyecto único para no chocar con el otro agente en paralelo:
  ```bash
  docker compose -p <repo>-task001 -f docker-compose.test.yml up -d
  ```
  Puertos distintos por TASK (variables de entorno, no edites el yml compartido).
- Al terminar tu TASK: `docker compose -p <repo>-task001 down -v`. No dejes contenedores huérfanos (`docker ps` antes de irte).
- Si un puerto o volumen está ocupado por otro worker: NO lo mates, NO lo reuses. Elegí otro puerto y seguí.

## 4. Etiqueta en paralelo (2+ agentes = equipo)

- Antes de levantar algo pesado, mirá `docker ps` y tu `doing/` ajeno: si el otro ya corre la suite completa, corré la tuya targeteada y la completa al final.
- CPU compartida: evitá `--parallel` agresivo + suite completa + build a la vez que otro agente. Secuenciá lo pesado.
- La comunicación entre agentes es por archivos (`done/`, `feedback/`), nunca por procesos, puertos ni señales.

## 5. Limpieza al salir

Worker: baja tu stack (`down -v`) al entregar. Worktree lo borra el orquestador tras el merge. Cero contenedores con tu TASK en el nombre al final del día.

## Prohibido

Puertos públicos (`--publish 0.0.0.0`), `--privileged`, borrar volúmenes que no creaste, `down` sin `-p` (mata stacks ajenos), credenciales reales en contenedores de test.
