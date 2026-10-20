# Contrato operativo

## Fases y checkpoints

| Fase | Entrada obligatoria | Salida |
|---|---|---|
| `grill` | Issue, repo, AGENTS, diseños | Spec y tickets aprobados/publicados |
| `implement` | Spec y tickets | Commits locales o NO_OP por ticket |
| `refactor` | Diff de implementación | Cumplimiento AGENTS y gates de arquitectura validado |
| `manual-revision` | Runtime y stories | Evidencia por story y findings cerrados |
| `pr-creation` | Commits, evidencias, paridad CI y review previa del HEAD (`record-pre-review`) | PR no draft hacia `<target>` con bloque de validación del HEAD (`publish-validation`) |
| `review-convergence` | PR, bots y Actions | PR lista para reviewer humano, con bloque de validación del HEAD final |

Completar una fase con:

```bash
python3 <plugin-root>/scripts/issue-delivery <issue> checkpoint --phase <fase> --artifact <clave>=<ruta>
```

Todo artifact debe estar dentro del worktree. El motor rechaza paths externos.

## Límites

- Verificar blockers significa inspeccionar y clasificar el feedback existente. No autoriza
  reparaciones, tests, commits, push o LocalReviewer, incluso si el run ya está en convergencia.
- Cada uso o ciclo de LocalReviewer requiere permiso explícito y acotado. Por defecto cubre una
  ejecución. Un ciclo requiere alcance y número máximo explícitos. No ampliar ese permiso ni
  revocar una cancelación por un "continúa". Los gates de review no conceden autorización.

- Reparación por ticket: 3 ciclos.
- Revisión UI-reparación: 5 ciclos.
- Review remoto: hasta 4 rondas. TurboShop usa 600 segundos de quietud, 1200 segundos de espera
  máxima por ronda y polling cada 15 segundos.
- Target de PR: siempre `git.prTarget` del perfil activo.
- El flujo puede refrescar referencias remotas para inspección, pero no ejecuta `rebase`, `merge`,
  `cherry-pick` ni `pull` para integrar ramas.
  Excepción: `restack` (sólo slices hijos de un stack, `--force-with-lease`) y `slice-extract`
  (commits propios antes del primer push).
- Tamaño: sólo superar `slicing.maxProductLines` (4000 líneas de producto) puede pausar para una
  decisión de división; exactamente 4000 no pausa. `slicing.estimateDeviation` (1.5x sobre
  `Estimated size`) no dispara una decisión por debajo de ese umbral. Nunca dividir solos. Stack de
  profundidad máxima `slicing.maxStackDepth` (3).

Al alcanzar un límite, detener procesos propios, conservar estado y bloquear.

## Gate obligatorio de reparación

Toda petición de ajuste/corrección y todo reporte de comportamiento roto abre un ciclo de reparación, sin importar la fase actual.

El ledger debe declarar la superficie afectada y la evidencia mínima. El recibo de verificación debe registrar:

- Provider `static`, `test`, `api-command`, `local-runtime`, `cua-driver`, `codex-browser`, `playwright` o `adb` nativo autorizado según [native-adb-evidence.md](native-adb-evidence.md).
- Texto o ID del reporte.
- Escenario `REPAIR-<n>` o historias afectadas.
- SHA exacto verificado.
- Runtime ID cuando corresponda.
- Resultado `PASS`.
- Timestamp y paths de evidencia final.

Un cambio posterior invalida sólo la evidencia de las superficies que pueda afectar. CUA/browser es obligatorio únicamente para comportamiento visual o interactivo; no usarlo por defecto para backend, documentación, tests o build. No hacer handoff sin recibos válidos para el HEAD actual. Si el método necesario no puede probar el escenario, el estado correcto es `blocked`, no `completed`; no degradar la cobertura silenciosamente.

## Decisiones de review

- `FIX`: problema válido, material, causado o agravado por la branch y compatible con el spec.
- `SKIP`: no corresponde cambiar esta PR; usar un motivo principal `YAGNI`, `OUT_OF_SCOPE`, `EXTERNAL_CHANGE`, `OVER_ENGINEERING`, `NOT_REPRODUCIBLE`, `BASELINE_OR_COVERED` o `PRIOR_DECISION`.
- `NEEDS_USER_DECISION`: decisión real de negocio, arquitectura o cambio de acuerdo aprobado. La dificultad alta por sí sola sigue siendo trabajo del modelo.
- `STOP`: la misma cadena causal sigue abierta después de dos iteraciones fallidas. No intentar un tercer parche ni cerrar el thread.

Usar `pr-blocker-convergence` como contrato único. Sus SKIP y decisiones de usuario son pegajosos por causa raíz. Guardar la memoria compacta dentro de `.local-runtime/issue-delivery-orchestrator/<run-id>/blocker-triage/`: `state.json` y `dashboard.md` para el estado actual, `index.jsonl` para búsquedas exactas e `history/round-<NN>.json` para auditoría. No cargar el índice ni el historial completos en cada ronda. Incluir plan de herramientas, apariciones, `cycle-key` y motivo público breve sin información sensible.

Después de cerrar cada comentario general de un `blockerBots` con `FIX` validado/pusheado, ejecutar
`acknowledge-blocker --decision FIX`. El comando valida autor, PR y presencia de blockers reales,
añade `+1` idempotente como la identidad GitHub configurada y persiste `reviewAcknowledgements`.

Cuando todos los blockers automatizados restantes sean `SKIP`, publicar primero un único resumen visible mediante `publish-skip-summary --input <ruta-json>`. El JSON debe contener `skips`, con `commentId`, `title` y `reason` por decisión. El comando:

1. vuelve a consultar comentarios generales y `reviewThreads`;
2. exige que los IDs coincidan exactamente con todo feedback automatizado pendiente;
3. publica o actualiza idempotentemente un comentario general de la PR;
4. persiste el recibo bajo `review/skip-summary.json`.

Sólo después se pueden resolver los threads inline con `SKIP` y ejecutar `acknowledge-blocker --decision SKIP` para comentarios generales. El reconocimiento se rechaza si ese ID no aparece en el resumen público. No usar estos comandos para `Ninguno`, sugerencias ni decisiones pendientes.

Antes de completar `review-convergence`, ejecutar `review-gate`. El gate vuelve a consultar
GitHub en vivo y exige simultáneamente:

- cero `reviewThreads` inline no resueltos que contengan comentarios de bots configurados;
- cero comentarios generales accionables de los `blockerBots` sin reacción `+1` de la identidad
  GitHub configurada;
- cero reconocimientos `SKIP` que no estén incluidos en el resumen público.

Los comentarios humanos quedan fuera del ciclo automático. El propio
`checkpoint --phase review-convergence` repite este gate, guarda el recibo en
`review/final-gate.json` y rechaza el cierre si cualquiera de las dos superficies tiene feedback
pendiente. Un snapshot de ronda, por sí solo, nunca autoriza la finalización.

## Evidencia

Para Android nativo autorizado, seguir [native-adb-evidence.md](native-adb-evidence.md). El provider ADB es una excepción explícita por evidencia y no cambia el reviewer web del run.

El manifiesto de screenshots debe ser JSON:

```json
{
  "evidenceVersion": 2,
  "verification": {
    "status": "PASS",
    "provider": "cua-driver",
    "verifiedCommit": "<sha>",
    "runtimeId": "<runtime-id>",
    "verifiedAt": "<ISO-8601>",
    "scenarioIds": ["US-1"]
  },
  "screenshots": [
    {
      "storyId": "US-1",
      "title": "Resultado observable",
      "caption": "Qué demuestra la captura",
      "path": ".local-runtime/issue-delivery-orchestrator/<run-id>/validation/ui/US-1.png",
      "callouts": [
        {
          "kind": "highlight",
          "caption": "Elemento nuevo verificado",
          "bounds": {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.1}
        }
      ]
    }
  ]
}
```

Leer [evidence-annotations.md](evidence-annotations.md). Incluir sólo PNG originales finales, sin
secretos ni datos sensibles. Preparar una copia anotada determinista y conservar ambas. Rechazar
como obsoleto un manifiesto cuyo `verifiedCommit` no sea el HEAD final.

`publish-evidence` debe:

1. Subir copias anotada y original a Linear y actualizar `## UI enhancements`.
2. Subir ambas copias a `git.evidenceBranch` mediante Git Data API, sin tocar la branch de la issue.
3. Usar en el comentario idempotente sólo los paths relativos devueltos por el motor.
4. Rechazar cualquier body de PR que dependa de `uploads.linear.app`.

Las evidencias de GitHub permanecen restringidas por los permisos del repositorio. `repair-evidence-links` migra recibos antiguos usando los mismos archivos locales, sin reinterpretar su resultado UI.

## Persistencia

- Todo run nuevo exige `--worktree`. Un `--mode` explícito tiene prioridad. Sin él, el CLI detecta
  Conductor Cloud mediante `CONDUCTOR_IS_LOCAL=0`, y Codex o Superset mediante
  `SUPERSET_WORKSPACE_PATH`, raíces configuradas o componentes
  inequívocos de la ruta; sin coincidencias selecciona Vanilla con
  `modeSource=vanilla-fallback`. Una detección contradictoria bloquea. El modo no cambia después de
  crear el estado.
- Modo `codex`: la app crea primero un worktree del chat, normalmente detached; el CLI lo adopta,
  conecta la rama local/remota de Linear o crea la rama desde `origin/<base>`, y fija
  `reviewer.method=codex-browser`. Fijar el chat y no archivarlo ni hacer Handoff a Local antes del
  merge y cleanup; el estado ignorado del run permanece en ese worktree.
- Modo `superset`: Superset crea primero el worktree en la rama de Linear; el CLI lo adopta mediante
  `--worktree` o `SUPERSET_WORKSPACE_PATH` y fija `reviewer.method=cua-driver`.
- Modo `vanilla`: el usuario o su herramienta crea/prepara el checkout o worktree y ejecuta su
  setup local antes del loop. El CLI lo adopta mediante `--worktree`, permite partir desde la rama
  base, un detached HEAD seguro o la rama de la issue, conecta la rama de Linear y fija
  `reviewer.method=cua-driver`. Si el modo fue inferido, bloquear antes de descartar cambios
  trackeados o archivos no ignorados; exigir un checkout limpio o selección explícita de Vanilla.
- Modo `conductor`: Conductor Cloud crea el workspace en su propia rama inicial desde la base
  elegida. El CLI lo adopta mediante `--worktree`, acepta esa rama si su historia está contenida en
  la base o en la rama de la issue, conecta la rama de Linear y fija `reviewer.method=playwright`.
  Preparar la VM según [conductor-cloud.md](conductor-cloud.md).
- Claude Code usa modo `vanilla` y `cua-driver` salvo que el checkout contenga una señal válida de
  Superset o de Conductor Cloud. Nunca seleccionar `codex` sólo porque el plugin también incluya manifiesto para Codex.
- El orquestador nunca crea worktrees ni delega el run a un thread que solicite otro. Prohibir
  `create_thread` con entorno worktree, `git worktree add` y mecanismos equivalentes. Si el actual
  no puede adoptarse, bloquear y pedir al usuario que abra manualmente otro para que la superficie
  ejecute su setup local.
- La adopción valida mismo repositorio y `.local-runtime` ignorado. Superset exige branch exacta de
  Linear o prefijo truncado con el mismo ID. Codex sólo permite detached HEAD o esa misma branch.
  Vanilla permite además partir desde la branch base declarada. Antes de iniciar un run nuevo,
  descartar cambios trackeados y archivos no trackeados no ignorados; preservar `.env`,
  dependencias, `.local-runtime` y cualquier otro archivo ignorado. Registrar el snapshot previo en
  `discardedInitialStatus` y exigir un status limpio después. No ejecutar esta limpieza al reanudar.
- Browser sólo opera dentro de un run modo Codex abierto en la app. Cua opera en modo Superset o
  Vanilla. Playwright es el reviewer principal sólo en modo Conductor. En los demás modos, Playwright headless puede asistir exclusivamente una story con brecha demostrada de
  `file-upload` o `hover`, sobre el mismo SHA/runtime y con recibo bajo el run; el provider continúa
  siendo el reviewer principal. No cambiar provider o modo silenciosamente. Leer
  [headless-assistance.md](headless-assistance.md) y mantener compatibilidad con
  `uploadAssistance` v1 y recibos v2 de Codex Browser.
- `adoptedHead` y `adoptedStatus` fijan el baseline limpio posterior a la adopción;
  `discardedInitialStatus` conserva la auditoría de lo eliminado.
- `python3 <plugin-root>/scripts/issue-delivery <issue>` descubre runs actuales en todos los worktrees Git
  registrados, y reanuda el más reciente preservado.
- Un run existente nunca cambia de worktree por una actualización del orquestador.
- Después de iniciar o reanudar, anunciar inmediatamente en el chat `modeDecision.mode`,
  `modeDecision.source`, `modeDecision.reviewer` y `modeDecision.worktree`. No continuar al Grill
  sin ese anuncio. Para `vanilla-fallback`, explicar que no se detectaron señales Codex/Superset.
- `--new-run` crea otro sólo si la branch no está asociada a un worktree.
- `runtime-init --fresh` permite registrar otro runtime sin limpiar los anteriores.
- Al finalizar el loop sólo se detienen procesos.
- `cleanup` posterior al merge limpia todos los runtimes registrados y el perfil del navegador, no el worktree ni la branch.
