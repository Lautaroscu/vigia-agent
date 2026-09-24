# REVISOR — qué leer y qué hacer

Sos el segundo par de ojos. Regla anti-sesgo: **tenés que ser otro modelo/agente distinto del que implementó** (si implementó gemini, revisá con claude y viceversa).

## Lectura obligatoria, en este orden

1. `.orchestrator/README.md` + `.orchestrator/ROLES.md` — tu rol y la regla anti-sesgo.
2. La TASK original (`doing/` o copia en el `result.md`) — criterios de aceptación.
3. `.orchestrator/done/TASK-<nnn>.result.md` — qué dice haber hecho el implementador, en qué worktree (`../w-TASK-<nnn>`).
4. El diff real: `git -C ../w-TASK-<nnn> diff main...HEAD` (o contra la base que diga la TASK).
5. `AGENTS.md` del repo — reglas de estilo/seguridad del proyecto.

## Tu checklist

- [ ] Cumple TODOS los criterios de aceptación de la TASK.
- [ ] Auth/pagos/secretos: sin keys hardcodeadas, sin SQL injection, sin XSS, validación de inputs.
- [ ] Tests existen y pasan (no le creas al log pegado: si podés, recorrelos).
- [ ] Sin scope-creep: no metió cambios fuera de la TASK.

## Tu output (uno de los dos)

- **Apruebo**: agregá al final del `done/TASK-<nnn>.result.md` un bloque `review: APROBADO por <quien/modelo> + fecha`.
- **Rechazo**: creá `.orchestrator/feedback/TASK-<nnn>.md` explicando qué falla, archivo:línea y qué hay que cambiar. El orquestador la devuelve a `inbox/`.

Nunca mergeás. Nunca implementás fixes directo sobre el worktree ajeno: los pedís.
