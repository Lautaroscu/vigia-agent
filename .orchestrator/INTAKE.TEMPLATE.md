# INTAKE — del pedido a la spec (el arquitecto refina, el humano contextualiza)

Mientras más contexto le das al arquitecto, mejor spec sale. Este archivo tiene las dos mitades: lo que VOS le das y lo que ÉL hace con eso.

## A. Context pack (humano: pegalo con tu pedido, aunque sea desordenado)

```
Objetivo: <qué querés, 1-2 líneas>
Por qué: <problema real, no pedido literal>
Repo/módulo: <repo + paths si los sabés>
Alcance SÍ: ...
Alcance NO: ... (lo más valioso: qué NO tocar)
Restricciones: <stack, reglas, no-gos>
Ejemplos: <algo parecido en el repo u otro lado>
Terminado = : <cómo sabés que está hecho>
```

Todo lo que falte acá el arquitecto lo infiere del repo o lo pregunta UNA vez junta (no 10 preguntas sueltas).

## B. Refinamiento (arquitecto: obligatorio antes de publicar TASKs)

1. **Explorar**: `estado`/baseline del repo, leer el código del área (no especular APIs: verificar que existen).
2. **Borrador**: cada TASK nace en `drafts/TASK-<nnn>-<slug>.DRAFT.md` (nunca directo en `inbox/`).
3. **Checklist de spec** (todo tiene que ser SÍ):
   - ¿Cabe en 1 rama reviewable? Si no, partir.
   - ¿Cada criterio es un test o un comando exacto? Prosa = no publicable.
   - ¿`owns`/`reads_only`/`forbidden` completos y verificables?
   - ¿Los nombres de archivos/funciones/APIs existen de verdad en el repo?
   - ¿Un worker nuevo entiende QUÉ + DÓNDE + CÓMO PROBAR sin preguntar?
4. **Publicar**: recién ahí `drafts/` → `inbox/`. Lo refinado una vez se reutiliza: decisiones a `DECISIONS.md`.

Regla: worker que recibe spec floja no adivina — la devuelve a `feedback/` y el arquitecto la refina. El costo de refinar es minutos; el de una mala spec en paralelo, días.
