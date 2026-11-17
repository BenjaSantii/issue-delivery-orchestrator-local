# Vía express

Para issues chicas y bien especificadas. En LOG-486, un fix de 50 líneas tardó 2h18m: el código
tomó 5 minutos y el resto fueron esperas de respuestas, un Refactor sin cambios y dos sesiones de
QA aparte con su preparación. La vía express quita esas etapas y conserva los gates que protegen
la PR.

| Etapa | Flujo normal | Express |
| --- | --- | --- |
| Grill | Entrevista, una pregunta a la vez | Sin entrevista; borrador directo y una sola aprobación |
| Tickets | Aprobación aparte | Un ticket, aprobado junto con el spec |
| Refactor | Worker de Refactor | No existe; el motor lo registra como `skipped` |
| Revisión manual | Conversación de QA nueva (`qa-session`, `qa-wait`) | El chat principal ejecuta el QA Script |
| Hallazgo obvio de QA | Se pregunta o se repara | Se repara sin preguntar y se informa |
| Paridad CI, PR, convergencia, evidencia, Linear | Igual | Igual |

## Elegibilidad

Todas estas condiciones:

- La issue de Linear ya describe el problema y el resultado esperado. Si hay que descubrir qué se
  quiere, no es express.
- Cabe en un ticket de unas 150 líneas de producto o menos.
- No toca concurrencia, migraciones, autenticación o permisos, contratos compartidos entre apps o
  backends, ni layout o navegación transversal.
- El QA Script tiene como máximo cuatro casos.

El motor rechaza al publicar un spec `Lane: express` con `concurrency` o `roles-authorization` en
`Applies: Yes` o con más de cuatro casos de QA, y unos tickets con más de un ticket o con
`Estimated size` mayor que 150. El checkpoint del Grill repite la validación de los tickets.
Migraciones, contratos y layout no tienen una columna que el motor pueda leer: los revisa el agente.

## Activación

- El usuario la pide al lanzar el run, por ejemplo "express" o "hazla express".
- O el agente la propone en una línea cuando la issue cumple la elegibilidad, y el usuario acepta.
  No activarla sin esa aceptación.

Si al leer el código la issue deja de cumplir (aparece una migración, un permiso, un segundo
ticket), volver al flujo normal, decirlo en una línea con la razón y seguir con el Grill normal.
No forzar el spec para que pase la validación.

La vía queda en el spec aprobado como `Lane: express` debajo de `Spec ID`. El motor la lee del
snapshot del spec del run, así que sobrevive a una reanudación sin estado extra; `status` la
muestra como `lane`.

## Flujo

1. **Grill express.** Seguir la sección "Express lane" de `issue-delivery-grill`: leer la issue con
   `read-linear-issue`, el código y los `AGENTS.md`; redactar de una vez el spec con
   `Lane: express`, el ticket y el QA Script, y pedir una sola aprobación para el spec y el ticket
   juntos. Preguntar sólo lo que [decision-policy.md](decision-policy.md) reserva al usuario,
   todo en esa misma pausa. Con la aprobación, publicar el spec con
   `issue-delivery-spec-publisher`, el ticket con `issue-delivery-ticket-publisher` sin pedir otra
   aprobación, registrar `size-estimate` y completar el checkpoint `grill` como siempre.
2. **Implement.** Igual que el flujo normal: un worker con `implement-package`, el recibo de `test`
   y un commit. Al completar `checkpoint --phase implement`, el motor salta a `manual-revision` y
   registra `refactor` como `skipped`. No ejecutar `refactor-package`.
3. **QA en el chat principal.** No ejecutar `qa-session`, `qa-wait` ni `qa-resume`. El chat
   principal prepara el runtime (`runtime-init`, apps necesarias, `runtime-recipe`) y ejecuta él
   mismo cada caso del QA Script según [qa-script.md](qa-script.md):
   - `UI` con el reviewer del modo, con capturas en `validation/ui/`. En Conductor y en hosts sin
     pantalla es Playwright. En `superset`, `t3` o `paseo` con escritorio, quien quiera Playwright
     lo elige al crear el run con `--reviewer playwright`; el manifiesto UI debe declarar el
     reviewer del run.
   - `API` y `command` con scripts bajo `validation/qa/` contra el Local Runtime.
   - Los casos con `DB` en Evidence se verifican con consultas de sólo lectura antes y después,
     como indica "Qué comprobar" en [fresh-qa.md](fresh-qa.md). Ese requisito no cambia.

   Escribir `validation/qa-results.json` con `"qaMode": "main-session"` en la raíz y sin
   `reviewSessionId`. El motor acepta ese modo sólo cuando el spec aprobado dice `Lane: express` y
   el run no registró una conversación de QA; en cualquier otro run lo rechaza. El comentario de QA
   en Linear y en la PR indica que lo ejecutó el chat principal.
4. **Hallazgos.** Si un caso falla por un defecto claro dentro del spec aprobado, repararlo sin
   preguntar con un worker (`implement-package --ticket <ID> --finding <ruta>`), volver a ejecutar
   sólo los casos que la reparación puede afectar e informar al usuario en una línea. Como el
   archivo debe apuntar al HEAD nuevo, reescribir `qa-results.json` con `verifiedCommit` actual:
   los casos no afectados conservan su evidencia y su `summary` empieza con
   `Sin reejecutar desde <sha12>:`. Preguntar sólo si el hallazgo exige una decisión de producto.
   Registrar en `decisions.md` cada hallazgo reparado sin preguntar.
5. **Cierre de la revisión manual.** `validation/notes.md`, `publish-run` y
   `checkpoint --phase manual-revision` con `ui-manifest` y `qa-results`, igual que el flujo normal.
6. **PR y convergencia.** Sin cambios: `size-check`, paridad con CI completa, `ensure-pr`,
   `publish-run`, `wait-review` y `review-gate`. Tras el QA, un `FIX` de blockers sigue sin
   repetir QA salvo que el usuario autorice una ronda; en express esa ronda también la ejecuta el
   chat principal.

## Mensaje final

Incluir la sección "Decisiones tomadas sin tu aprobación" de
[decision-policy.md](decision-policy.md), con los hallazgos de QA reparados sin preguntar.
