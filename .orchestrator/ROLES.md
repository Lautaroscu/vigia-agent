# Roles y pool de agentes

Pool actual del usuario:
- `claude-pro-20` (Claude Pro $20) — razonamiento fuerte, ideal **orquestador / revisor**
- `gemini-pro-20` (Gemini Pro $20) — contexto largo, ideal **orquestador alterno / implementador**
- `openai-20` (futuro, OpenAI $20) — ideal **orquestador / tester e2e**
- `opencode-go-10` (futuro, OpenCode Go $10) — barato, ideal **implementador / tester en paralelo**

## Matriz sugerida

| Rol | Quien | Modelos sugeridos | Responsabilidad |
|---|---|---|---|
| ORCHESTRATOR | el mas inteligente disponible | claude-opus/sonnet, gemini-pro, gpt-5 | parte specs en TASKs, asigna, valida `done/`, mergea |
| IMPLEMENTER | pool barato/paralelo | opencode, deepseek, gemini-flash, gpt-mini | toma 1 TASK de `inbox/`, implementa en worktree, deja `done/` |
| REVIEWER | modelo distinto al implementador | claude si implemento gemini y viceversa | revisa diff, deja nota en `feedback/`, aprueba o rechaza |
| TESTER | cualquiera con comandos del repo | mismo que implementador u otro | corre `pytest` / `vitest` / `pnpm test`, adjunta log en `result.md` |

Regla anti-sesgo: **el que implementa no aprueba**. Siempre otro rol/modelo revisa.

## Routing por defecto (este repo puede ajustarlo en config.json)

1. Tasks de diseno/arquitectura -> ORCHESTRATOR (claude o gemini-pro).
2. Tasks de codigo mecanico, CRUD, tests -> IMPLEMENTER (opencode-go / gemini).
3. Review de seguridad, pagos, auth -> REVIEWER claude.
4. E2E / QA repetitivo -> TESTER barato en paralelo (2+ a la vez).

## Como declarar quien hizo que

Al tomar una task, el worker antepone en el `result.md`:

```md
worker: opencode-go-10
role: IMPLEMENTER
model: <el que uso>
worktree: ../w-TASK-001
```

Asi el orquestador sabe a quien pedirle fix sin que el usuario intervenga.
