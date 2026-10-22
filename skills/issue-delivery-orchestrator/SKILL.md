---
name: issue-delivery-orchestrator
description: "Orquestar de extremo a extremo una issue de Linear en modo Codex, Superset, Vanilla o Conductor Cloud: adoptar el worktree, usar la rama de Linear, publicar spec/tickets, implementar, refactorizar, verificar la superficie afectada, crear la PR y converger blockers y Actions sin integrar otras ramas. Usar también para ajustes posteriores; cada reparación exige evidencia nueva proporcional al riesgo antes del handoff."
---

# Issue Delivery Orchestrator

Operar como plano de control conversacional. Usar el CLI privado como motor determinista; no ejecutar un segundo Grill interactivo mediante `codex exec`. Mantener toda decisión humana en la conversación actual.

Leer [workflow-contract.md](references/workflow-contract.md) antes de iniciar o reanudar una ejecución.

Una petición de verificar blockers limita el trabajo a inspeccionar y clasificar el feedback existente, según `pr-blocker-convergence`. No convertirla en reparación o validación automática. Cada uso o ciclo de LocalReviewer requiere permiso explícito y acotado; la invocación del orquestador no lo concede. Conservar cualquier cancelación previa hasta una autorización nueva que mencione LocalReviewer. Aplicar las reglas de alcance y permiso de `pr-blocker-convergence` en todas las fases.

Si el usuario pide modificar o actualizar **este plugin** en vez de entregar una issue, leer
[plugin-maintenance.md](references/plugin-maintenance.md): editar siempre el repositorio fuente y
nunca el snapshot instalado en `plugins/cache/`.

Cuando se nombre otra skill incluida, invocarla con la sintaxis nativa del host: `$<skill>` en
Codex o `/issue-delivery-orchestrator-local:<skill>` en Claude Code. Resolver `<plugin-root>` como
el directorio instalado del plugin; en Claude Code corresponde a `${CLAUDE_PLUGIN_ROOT}`. Claude
Code nunca selecciona modo `codex`: usar `conductor` dentro de Conductor Cloud, `superset` cuando
exista su señal válida y `vanilla` en cualquier otro checkout preparado.

Para iniciar issues con spec y tickets aprobados en Conductor Cloud desde la máquina local, usar
`issue-delivery-cloud-launch`. Dentro del workspace cloud, leer
[conductor-cloud.md](references/conductor-cloud.md) antes de iniciar el run.

## Iniciar o reanudar

1. Resolver `<plugin-root>` como el directorio que contiene `.codex-plugin/plugin.json` o
   `.claude-plugin/plugin.json`. Ejecutar
   `python3 <plugin-root>/scripts/issue-delivery config` y bloquear si faltan
   `LINEAR_EXPECTED_EMAIL`, `GITHUB_EXPECTED_LOGIN` o no puede resolverse el repositorio objetivo
   desde la configuración o el worktree actual.
2. Exigir una issue existente de Linear por ID o URL. Para un run nuevo, permitir un modo explícito
   del usuario o detectarlo desde el worktree actual:

   - `codex`: iniciar el chat en la app de Codex con **Worktree** seleccionado y elegir la base
     solicitada, o la `defaultBase` del perfil. Codex crea el worktree antes de ejecutar el loop.
     Fijar el chat hasta el merge y cleanup final.
   - `superset`: crear en Superset el worktree sobre la rama entregada por Linear y abrir la sesión
     dentro de ese workspace.
   - `vanilla`: iniciar Codex CLI o Claude Code directamente en un checkout o worktree ya preparado
     por el usuario o su herramienta habitual. Ejecutar el setup local del repositorio antes del
     loop y seleccionar siempre `--mode vanilla`. En Claude Code, elegir este modo salvo que el
     checkout tenga una señal válida de Superset o de Conductor Cloud.
   - `conductor`: workspace de Conductor Cloud (`CONDUCTOR_IS_LOCAL=0`), normalmente creado con
     `issue-delivery-cloud-launch`. Preparar la VM según
     [conductor-cloud.md](references/conductor-cloud.md) y seleccionar `--mode conductor`.

3. Antes de cambiar de directorio, ejecutar `git rev-parse --show-toplevel` en el workspace actual y
   conservar esa ruta absoluta. No iniciar modo `codex` desde Local, modo `superset` desde otro
   checkout ni modo `vanilla` fuera del checkout que se desea adoptar. No crear, solicitar ni
   delegar otro worktree mediante `create_thread`, subagentes, `git worktree add` o cualquier
   mecanismo equivalente. Si el worktree actual no es adoptable, bloquear en la misma sesión y
   pedir al usuario que abra manualmente otro con su setup local.
4. Ejecutar, agregando `--mode <codex|superset|vanilla|conductor>` sólo cuando el usuario indique
   uno o el checkout sea de Conductor Cloud:

   ```bash
   python3 <plugin-root>/scripts/issue-delivery <issue> --worktree <ruta> \
     [--mode <codex|superset|vanilla|conductor>] \
     [--base <branch>] [--stack-parent <rama-del-slice-padre>] [--new-run]
   ```

   Usar `--stack-parent` sólo para un slice hijo de un stack (ver
   [slicing.md](references/slicing.md)): el run parte de esa rama y su PR apunta a ella.

5. Confirmar `modeSource` y `developmentMode` en la respuesta. La detección usa, en orden:
   `CONDUCTOR_IS_LOCAL=0` (Conductor Cloud), `SUPERSET_WORKSPACE_PATH` coincidente, raíces configuradas y componentes inequívocos de la ruta
   como `.codex` o `superset-worktrees`. Si no encuentra ninguna señal, elegir `vanilla` con
   `modeSource: vanilla-fallback`; si detecta señales contradictorias, pedir el modo al usuario. El
   modo determina el workspace y reviewer durante todo el run:

   - `codex` adopta el worktree del chat, acepta su `detached HEAD`, lo conecta a la rama de Linear
     y usa `issue-delivery-browser-review`.
   - `superset` adopta el worktree ya conectado a la rama de Linear y usa `issue-delivery-cua-review`. También
     acepta `SUPERSET_WORKSPACE_PATH`.
   - `vanilla` adopta el checkout/worktree indicado, permite partir desde `origin/<base>`,
     `detached HEAD` seguro o la rama de la issue, conecta la rama de Linear y usa
     `issue-delivery-cua-review`.
   - `conductor` adopta el checkout cloud desde la rama inicial que crea Conductor, siempre que su
     historia esté contenida en la base o en la rama de la issue, conecta la rama de Linear y usa
     `issue-delivery-playwright-review`.

6. Publicar inmediatamente en el chat, para runs nuevos y reanudados, los valores exactos de
   `modeDecision` devueltos por el motor. Usar un mensaje autocontenido como:

   ```text
   Modo decidido: <mode> (fuente: <source>).
   Reviewer: <reviewer>.
   Worktree adoptado: <worktree>.
   ```

   No continuar al Grill ni presentar el modo sólo en logs o handoff final. Si `source` es
   `vanilla-fallback`, decir expresamente que se eligió Vanilla porque no hubo señales Codex o
   Superset. Si el motor bloquea la adopción del fallback por un checkout dirty, anunciar igualmente
   la decisión incluida en el error antes de pedir que se preserven o limpien los cambios.
7. Usar la branch base del perfil por defecto. En modo Codex, crear una rama inexistente desde el
   último `origin/<base>`; reutilizar la rama local o remota de la issue cuando exista.
8. No cambiar modo, worktree ni reviewer después de crear el run. Obedecer `currentPhase`,
   `developmentMode` y `reviewerMethod`; no recrear un run preservado.
   En modo Codex, no usar Handoff a Local: el estado ignorado bajo `.local-runtime` debe permanecer
   en el mismo worktree/chat.
9. Trabajar exclusivamente en el worktree retornado. Guardar prompts, snapshots, logs y evidencias
   bajo `.local-runtime/issue-delivery-orchestrator/<run-id>/`.
10. No guardar tokens, cookies ni secretos en el worktree. El CLI valida las identidades Linear y
    GitHub configuradas antes de mutar servicios.
11. Toda lectura y mutación de Linear debe pasar por el CLI autenticado con la API key personal.
    Antes del Grill y cada vez que se necesite contexto remoto actualizado, ejecutar:

    ```bash
    python3 <plugin-root>/scripts/issue-delivery <issue> read-linear-issue
    ```

    Usar su descripción y comentarios como fuente remota. No invocar `$linear`, `linear:linear`,
    `codex_apps.linear` ni ninguna app o conector MCP de Linear, ni siquiera para lectura. Si el
    comando falla, bloquear con su error; no recurrir al OAuth compartido como fallback.
12. Ejecutar también todas las escrituras mediante las acciones específicas del CLI. Respetar
    `LINEAR_COMMENTS_ENABLED`; cuando sea `false`, no crear comentarios en Linear en ninguna fase.

Para TurboShop, asignar las nuevas tareas y sub-issues de Sales Force al usuario
de Linear autenticado. Shipment y las demás apps quedan sin asignar. Una
indicación explícita del usuario sobre el responsable tiene prioridad. En
`create-sub-issue`, usar `--assignee <ID-de-usuario>`, `--assignee me` o
`--assignee none` para esa excepción o cuando el slice pertenezca a otra app que
su padre. Determinar la app por el alcance real, sin inferirla de una mención
incidental. No cambiar asignaciones de tareas existentes por esta preferencia.

Los runs anteriores sin `mode` persistido conservan su worktree y reviewer históricos; exponer
`developmentMode=codex` sólo para `codex-browser` y `developmentMode=superset` para `cua-driver`,
sin migrar estado, ramas ni procesos. No inferir `vanilla` para estados legacy. Las sesiones que ya
están ejecutando el loop continúan sobre ese contrato.

Toda adopción exige el mismo repositorio y `.local-runtime` ignorado. Superset exige la rama exacta
de Linear o un prefijo truncado con el mismo ID. Codex admite el `detached HEAD` creado por la app.
Para todo run nuevo, el CLI descarta cambios trackeados y archivos no trackeados del worktree antes
de adoptar; preservar archivos ignorados como `.env`, dependencias y `.local-runtime`, y registrar
las rutas descartadas en `discardedInitialStatus`. No abandonar commits propios: bloquear si el
`HEAD` contiene commits que no estén preservados en la branch de la issue o la base remota. Nunca
limpiar al reanudar un run existente.

Excepción de seguridad: si `vanilla` fue elegido mediante `vanilla-fallback`, exigir un checkout
sin cambios trackeados ni archivos no ignorados y bloquear antes de limpiar si está dirty. Permitir
la limpieza normal sólo después de que el usuario preserve/limpie esos cambios o seleccione
explícitamente `modo vanilla`.

Si una fase se bloquea, registrar y detener procesos propios:

```bash
python3 <plugin-root>/scripts/issue-delivery <issue> block --reason "<causa>"
```

Agregar `--decision` cuando se requiera una elección del usuario. Tras resolverla:

```bash
python3 <plugin-root>/scripts/issue-delivery <issue> resume
```

## Invariante de reparación y handoff

Aplicar esta regla en cualquier fase y también después de `completed_preserved`. Aplicarla a toda modificación correctiva posterior al inicio de Implement, sin importar si nace de un reporte del usuario, finding de Cua, review humano o automatizado, Action fallida, conflicto o descubrimiento del agente.

Considerar ciclo de reparación toda instrucción que pida ajustar, corregir o cambiar una implementación existente, o que reporte que algo no funciona, se ve mal o no cumple lo esperado. No tratarla como una edición aislada.

1. Reanudar el mismo run y worktree. Capturar el reporte exacto como escenario de aceptación de reparación.
2. Identificar las historias afectadas. Si el caso no estaba en el spec, crear una historia temporal `REPAIR-<n>` dentro del estado ignorado del run con precondición, pasos y resultado esperado; no ampliar el spec remoto por un bug compatible con su intención.
3. Invocar `issue-delivery-implement`, que debe delegar la validación automatizada enfocada a `test`, y dejar la branch en su estado final.
4. Declarar en el ledger del blocker la superficie afectada y la evidencia mínima necesaria: `STATIC`, `TEST`, `API_COMMAND`, `LOCAL_RUNTIME` o `UI`.
5. Levantar Local Runtime sólo cuando la integración o el estado ejecutándose sean parte del riesgo. Para `UI`, invocar el reviewer fijado por el modo: `issue-delivery-cua-review` en `superset` o `vanilla`, `issue-delivery-browser-review` en `codex` o `issue-delivery-playwright-review` en `conductor`.
6. No invocar CUA/browser para cambios exclusivamente backend, documentación, tests, build o lógica no observable en UI. Si una reparación puede invalidar una historia UI aceptada, repetir sólo esa historia con el reviewer fijado.
7. Si la verificación detecta un fallo, volver a `issue-delivery-implement` y repetir. Delegar la detección de ciclos y el límite de dos iteraciones fallidas a `pr-blocker-convergence`.
8. Considerar obsoleta sólo la evidencia cuya superficie pueda verse afectada por el último cambio. Actualizar capturas publicadas cuando corresponda.

Prohibir el handoff hasta disponer de evidencia posterior al último cambio para cada superficie afectada, con SHA verificado. Para UI, exigir PASS del reviewer del modo; para las demás, exigir el test, comando o análisis declarado en el ledger. Si la herramienta necesaria no está disponible, bloquear y explicar el impedimento; no degradar la cobertura.

## 1. Grill

1. Leer la issue, el repositorio y todos los `AGENTS.md` aplicables.
2. Invocar `issue-delivery-grill` en esta conversación. Si ya existe spec/tickets, tratarlos como base y reconciliar los mismos bloques e IDs.
3. Si existe Figma, inspeccionarlo mediante su MCP. Si no es accesible, bloquear antes de implementar, salvo que el usuario entregue explícitamente un PNG o PDF como fallback.
4. Etiquetar cada user story con una superficie verificable: `UI`, `API-assembled` o `command-test`.
   Construir en el spec la sección `## QA Script` según [qa-script.md](references/qa-script.md):
   caminos felices, casos borde, permisos, fallos y concurrencia que la revisión manual ejecutará.
   El usuario la aprueba junto con el spec y el motor la valida al publicarlo.
5. Obtener aprobación explícita del spec y del desglose.
6. Invocar, en orden, `issue-delivery-spec-publisher` y `issue-delivery-ticket-publisher`.
   Registrar la suma de `Estimated size` de los tickets con `size-estimate --lines <n>`. Si el
   desglose propuso slices, seguir [slicing.md](references/slicing.md); dividir o no es decisión
   del usuario.
7. Informar inmediatamente todo ticket HITL: ID, título, justificación, acción humana, dependencias y momento aproximado de pausa. No usar HITL para tareas difíciles que un agente sí puede resolver.
8. Guardar snapshots aprobados dentro del run y completar:

   ```bash
   python3 <plugin-root>/scripts/issue-delivery <issue> checkpoint --phase grill \
     --artifact spec=<ruta> --artifact tickets=<ruta>
   ```

## 2. Implement

En runs con `observabilityVersion: 1`, leer [run-metrics.md](references/run-metrics.md).
El controlador registra metrics-start antes de cada ticket y metrics-finish tras su intento,
actualizando el comentario de Linear. Usar otro intento para reparaciones y no inventar tokens
si aún no terminó el turno del host. Los checkpoints registran las fases automáticamente.

Procesar tickets en su orden aprobado. Antes de cada uno, entregar a `issue-delivery-implement` el spec completo, el ticket, el SHA inicial y la validación declarada para que la delegue a `test`.

- Terminar primero los tickets AFK no bloqueados.
- Pausar justo antes de un HITL y pedir la acción mínima al usuario.
- Crear un commit descriptivo por ticket validado, sin push. Un commit no debe mezclar tickets:
  los commits por ticket son los puntos de corte si luego hay que dividir.
- Después de cada commit de ticket ejecutar `size-check`. Con `NEEDS_DECISION`, pausar y
  preguntar al usuario si cortar aquí o seguir en un PR, según
  [slicing.md](references/slicing.md). Nunca dividir sin su decisión.
- Si ya está satisfecho, registrar `NO_OP` con evidencia y validación; no crear commits vacíos.
- Limitar a tres ciclos implementación-reparación por ticket. Bloquear si sigue rojo.
- Incluir commits preexistentes adoptados en la revisión del diff completo, sin reescribirlos.

Completar `implement` sólo con todos los tickets aceptados:

```bash
python3 <plugin-root>/scripts/issue-delivery <issue> checkpoint --phase implement
```

## 3. Refactor

Leer [refactor-review.md](references/refactor-review.md) completamente. Revisar el diff de
implementación contra los `AGENTS.md` aplicables y ejecutar cada gate de esa referencia.

- Allowlist: archivos cambiados por la implementación de este run y por commits preexistentes adoptados de la issue.
- Salir del allowlist sólo para corregir una violación concreta de `AGENTS.md`; registrar regla, archivo y razón.
- No hacer refactors especulativos.
- Invocar `test` después del último cambio de cada commit de refactor y consumir su recibo.
- Guardar el recibo de revisión bajo el directorio ignorado del run. No completar la fase con
  criterios `FAIL` o sin evidencia.

Completar la fase con:

```bash
python3 <plugin-root>/scripts/issue-delivery <issue> checkpoint --phase refactor \
  --artifact refactor-review=<ruta-del-recibo>
```

El orquestador puede ejecutar `git fetch` para consultar referencias remotas, pero nunca integra
ramas base u objetivo mediante `rebase`, `merge`, `cherry-pick` o `pull`. La PR se construye desde
la historia existente de la branch adoptada y se dirige a `prTarget` del estado: la rama del slice
padre en un stack, o `pr_target_branch` en cualquier otro caso.

Única excepción, autorizada por el usuario: el comando `restack` del motor rebasa un slice hijo
sobre su padre (o sobre la target después del merge del padre) con `--force-with-lease`, y
`slice-extract` reordena commits propios antes del primer push. No reproducir estas operaciones
con git a mano.

## 4. Revisión manual

Si `observabilityVersion: 1`, leer [fresh-qa.md](references/fresh-qa.md) y abrir/registrar una
conversación nueva de QA, con el mismo runtime y los artefactos aprobados fijados al SHA.
En Conductor hereda agente, modelo y esfuerzo del chat que llama al QA, sin fijar Codex.
Los pasos de verificación siguientes los ejecuta ese reviewer, no el implementador. El
controlador espera sus resultados, publica la evidencia y completa el checkpoint. Verificar
consecuencias persistidas en DB, además del comportamiento UI/API. Los runs anteriores mantienen
su flujo previo; no añadirles el flag ni actualizar sus chats para aplicar estas reglas.

En modo Conductor, la validación móvil siempre es simulación web según
`issue-delivery-mobile-web-review`; no requiere teléfono, ADB ni emulador nativo. Registrar las
limitaciones del SO como informativas, sin afirmar validación nativa ni bloquear por dispositivo.
Aplicar [conductor-validation.md](references/conductor-validation.md): vistas web en
localhost:3001–3004 y contratos nativos validados con Jest o scripts, con sus assertions como
evidencia del caso. No exigir teléfono ni WAIVED si la validación acordada pasa.
Fuera de Conductor, si el usuario pide validación Android nativa y autoriza ADB, aplicar
[native-adb-evidence.md](references/native-adb-evidence.md), que permite provider=adb sin cambiar
el reviewer web persistido y conserva los demás gates.

1. Inicializar un runtime persistente:

   ```bash
   python3 <plugin-root>/scripts/issue-delivery <issue> runtime-init
   ```

2. Levantar sólo las apps necesarias mediante `pnpm local-runtime` o `pnpm agent:*`, registrar sus PID y usar el runtime activo.
3. Ramificar exclusivamente por `developmentMode`:

   - `superset` o `vanilla`: invocar `issue-delivery-cua-review` desde el checkout adoptado.
     Usar el escritorio local (host) por defecto y conservar `reviewerMethod=cua-driver`:
     ejecutar `launch-browser --url <url-local>` para abrir el navegador del run.
     Usar Docker sólo cuando el usuario lo indique, creando o reanudando sólo el sandbox del run
     según [driver-contract.md](../issue-delivery-cua-review/references/driver-contract.md).
   - `codex`: no ejecutar `launch-browser`; exigir la app de Codex y Browser disponible,
     e invocar `issue-delivery-browser-review`. Permitir Playwright headless sólo para una story
     con brecha demostrada de `file-upload` o `hover`, sin cambiar reviewer ni modo.
   - `conductor`: no ejecutar `launch-browser`; invocar `issue-delivery-playwright-review` dentro
     de la VM y conservar `reviewerMethod=playwright`. Delegar las historias de teléfono, teclado o
     gestos táctiles al agente `mobile-web-reviewer` (o a `issue-delivery-mobile-web-review` en
     hosts sin agentes).

   En cualquier modo, una historia de teléfono puede sumar el diagnóstico de
   `mobile-web-reviewer`; fuera de `conductor` sus `FAIL` son findings, pero el PASS sigue saliendo
   del reviewer del modo.

4. Verificar las historias `UI` con el reviewer seleccionado y las demás mediante su superficie declarada.
   Si el spec aprobado tiene `## QA Script`, ejecutar **todos** sus casos según
   [qa-script.md](references/qa-script.md): los `UI` con el reviewer del modo y los `API` o
   `command` con scripts del run contra el Local Runtime, incluida la concurrencia real. Guardar
   `validation/qa-results.json` y publicarlo con `publish-qa-results` (comentario en Linear con
   capturas y salidas). Un `FAIL` o `BLOCKED` se repara antes de seguir; sólo el usuario puede
   convertirlo en `WAIVED`.
5. Ningún reviewer edita código. Entregar findings a `issue-delivery-implement`, reparar y repetir sólo las historias invalidadas con el mismo método.
6. Permitir como máximo cinco ciclos revisión-reparación.
7. Leer
   [evidence-annotations.md](references/evidence-annotations.md). Conservar únicamente capturas
   originales del estado final aceptado y producir un manifiesto v2 con `provider` igual al método
   seleccionado. Exigir callouts honestos sobre lo nuevo o `annotationReason` para cambios globales.
8. Ejecutar
   `python3 <plugin-root>/scripts/issue-delivery <issue> prepare-evidence --manifest <ruta>`,
   inspeccionar la copia anotada y corregir bounds o captions antes del checkpoint. El motor
   preserva el original.

Al terminar la revisión final o bloquear el run, cerrar sólo el navegador propio del run en host
o, si se usó Docker, retirar sólo su sandbox Cua, y registrar la limpieza según el contrato.
`stop-processes` y el cleanup del Local Runtime no limpian el contenedor. En `conductor`, cerrar
los procesos de Chromium que lanzaron los scripts del run. Conservar el entorno mientras haya
ciclos revisión-reparación activos.

Fuera de `conductor`, leer [headless-assistance.md](references/headless-assistance.md); en
`conductor` no aplica porque Playwright ya es el reviewer. Mantener el reviewer del modo como
primera opción para toda story. Si no puede ejecutar `file-upload` o mantener/demostrar un `hover`
real, demostrar la brecha y activar Playwright headless sólo para esa story. Un resultado
incorrecto de la app es `FAIL`, no una brecha de capacidad. Bloquear cuando Playwright tampoco
pueda cubrirla. No degradar cobertura ni cambiar automáticamente de reviewer o modo.

Completar la fase con:

```bash
python3 <plugin-root>/scripts/issue-delivery <issue> checkpoint \
  --phase manual-revision --artifact ui-manifest=<ruta> \
  [--artifact qa-results=<ruta>]
```

El checkpoint vuelve a preparar y validar las anotaciones. Con un QA Script aprobado exige además
`qa-results`, todos sus casos en `PASS` o `WAIVED` y su publicación para el HEAD actual.

## 5. PR

Leer [pr-quality.md](references/pr-quality.md) completamente antes de esta fase. El motor exige los
gates siguientes. La revisión local está sujeta al permiso explícito del usuario; un gate pendiente
no autoriza su ejecución. No abrir la PR para "ver qué dice el bot".

0. **Tamaño.** Ejecutar `size-check`. Con `NEEDS_DECISION` sin decisión registrada, preguntar al
   usuario y dividir o registrar `size-decision` antes de seguir; `ensure-pr` rechaza una PR nueva
   en ese estado.
1. **Paridad con CI.** Antes de invocar `test`, revisar el delta de tests según
   [retained-tests.md](../test/references/retained-tests.md); delegar el recorte de duplicados
   nuevos a Implement y conservar los casos del QA Script. No usar el Refactor de producto
   para ampliar o reducir cobertura. Invocar `test` en modo paridad con CI sobre el delta completo
   del candidato final contra `origin/<prTarget>`. Reparar todo `branch-caused` con
   `issue-delivery-implement` antes de seguir. Nunca pushear una branch que ya se sabe roja en
   lint, typecheck, tests o chequeos de
   contrato/migraciones.
2. **Review previa a la PR (fuera de Conductor).** En modo `conductor`, omitir LocalReviewer y
   continuar tras QA y paridad CI: no pedir permisos, login, tokens ni un recibo para esta etapa.
   Declarar en la PR `LocalReviewer: NOT_RUN — no requerido en Conductor`; no fabricar PASS.
   Fuera de Conductor, pedir permiso para una ejecución de `local-reviewer` sobre la branch,
   indicando SHA, alcance y provider/modo. Ejecutarla sólo con autorización vigente, según
   `pr-blocker-convergence` y `pr-quality.md`. Una repetición exige permiso nuevo o una ejecución
   restante dentro de un ciclo explícitamente autorizado. Procesar sus findings con
   `pr-blocker-convergence` como ronda `00` local. Registrar el recibo contra el HEAD exacto:

   ```bash
   python3 <plugin-root>/scripts/issue-delivery <issue> record-pre-review --receipt <ruta>
   ```

   Fuera de Conductor, `ensure-pr` rechaza crear una PR nueva sin un recibo `PASS` o `SKIP_ONLY`
   del HEAD actual. Las revisiones posteriores de GitHub se mantienen en todos los modos.
3. **PR y validación visible.** Crear el body dentro del run y ejecutar:

   ```bash
   python3 <plugin-root>/scripts/issue-delivery <issue> ensure-pr --body-file <ruta>
   python3 <plugin-root>/scripts/issue-delivery <issue> publish-validation --receipt <ruta>
   python3 <plugin-root>/scripts/issue-delivery <issue> publish-evidence --manifest <ruta>
   python3 <plugin-root>/scripts/issue-delivery <issue> publish-qa-results --results <ruta>
   ```

   Repetir `publish-qa-results` cuando exista QA Script para que el resultado quede también como
   comentario en la PR.

   `publish-validation` reemplaza el bloque marcado de validación del body con el recibo del HEAD
   pusheado. Los checkpoints `pr-creation` y `review-convergence` bloquean si ese bloque no
   corresponde al HEAD actual.

Publicar cada PNG anotado en dos destinos distintos:

- Linear: copia privada para `## UI enhancements`.
- GitHub: copia en la evidence branch del perfil, fuera de la branch y diff de producto.

Publicar además el PNG original en ambos destinos y enlazarlo bajo la imagen anotada. Las
anotaciones son evidencia explicativa; nunca reemplazan la captura original auditable.

El comentario de la PR debe usar exclusivamente las rutas relativas devueltas por el motor. No
insertar URLs privadas de uploads de Linear: el proxy de imágenes de GitHub no puede renderizarlas.

Si un run antiguo ya publicó links rotos, repararlos sin repetir la validación funcional:

```bash
python3 <plugin-root>/scripts/issue-delivery <issue> repair-evidence-links
```

Este comando debe rehostear exactamente las PNG ya aceptadas y actualizar el comentario idempotente; no autoriza a sustituirlas por capturas distintas.

La PR debe ser normal, no draft, siempre hacia la branch target del perfil, sin asignar reviewers.
Reutilizar una PR abierta compatible. Una PR cerrada sin merge requiere decisión; una ya mergeada
exige issue/branch de seguimiento.

Completar la fase con `python3 <plugin-root>/scripts/issue-delivery <issue> checkpoint --phase pr-creation`.

## 6. Convergencia remota

Consultar el feedback remoto no requiere ejecutar LocalReviewer. La reparación de blockers y la
paridad con CI tampoco lo autorizan. Respetar el modo de sólo verificación cuando el usuario lo
pida, y mantener separadas las pruebas focales de una revisión de código.

Invocar `pr-blocker-convergence` como único contrato de clasificación, ledger, selección de herramientas y detección de ciclos. En un stack: si este run es hijo, ejecutar `restack` al inicio de cada ronda y
después del merge del padre, y volver a publicar la validación si hubo push; si es padre, informar
al usuario tras cada push que los hijos deben restackear. Ejecutar automáticamente hasta cuatro
rondas:

```bash
python3 <plugin-root>/scripts/issue-delivery <issue> wait-review
```

Cada ronda usa los tiempos y autores automatizados declarados por el perfil. En el perfil TurboShop,
esperar diez minutos de quietud desde el último cambio relevante, con un máximo de veinte minutos
por ronda y polling cada quince segundos. Procesar sólo problemas concretos de los bots configurados
y sólo la sección `BLOCKERS` de los `blockerBots` (x100 en TurboShop); ignorar summaries, walkthroughs, sugerencias
opcionales y comentarios humanos durante el ciclo automático.

1. Entregar el snapshot completo a `pr-blocker-convergence`; esa skill agrega nuevos blockers, deduplica por causa raíz y decide `FIX`, `SKIP`, `NEEDS_USER_DECISION` o `STOP`.
2. Reparar automáticamente cada `FIX/MODEL` mediante `issue-delivery-implement`. Usar un commit por causa raíz, validar con las herramientas declaradas, repetir la paridad con CI sobre el delta completo y hacer un único push por ronda. Inmediatamente después del push, regenerar el recibo y ejecutar `publish-validation` antes de `wait-review`, para que cada corrida del reviewer vea la validación del SHA que revisa.
3. Si una solicitud exige negocio, arquitectura incompatible o cambiar un acuerdo vigente, registrar `NEEDS_USER_DECISION`, completar primero trabajo independiente seguro y pausar.
4. Si una cadena acumula dos iteraciones de reparación fallidas, registrar `STOP`, no intentar otro parche y avisar al usuario con la línea temporal y alternativas.
5. Si el usuario mantiene el spec, guardar `SKIP/PRIOR_DECISION`. Si acepta al reviewer, reconciliar primero spec y tickets en Linear y recién después editar.
6. Después de validar y pushear cada `FIX`, resolver su thread inline o reconocer su comentario
   general de un `blockerBots` con:

   ```bash
   python3 <plugin-root>/scripts/issue-delivery <issue> acknowledge-blocker --comment-id <id> --decision FIX
   ```

7. Cuando todo feedback automatizado todavía pendiente tenga decisión `SKIP` —sin `FIX`, `NEEDS_USER_DECISION` ni `STOP` restantes— crear dentro del run:

   ```json
   {
     "skips": [
       {
         "commentId": 123,
         "title": "Título breve",
         "reason": "Motivo público, concreto y autocontenido."
       }
     ]
   }
   ```

   Publicar una explicación visible antes de cerrar o reconocer esos blockers:

   ```bash
   python3 <plugin-root>/scripts/issue-delivery <issue> publish-skip-summary --input <ruta-json>
   ```

   El comando consulta ambas superficies en vivo y exige que el archivo cubra exactamente todos los
   blockers automatizados pendientes. Crea o actualiza idempotentemente un único comentario general
   de la PR bajo la identidad GitHub configurada. No incluir datos privados del ledger.
8. Sólo después de publicar el resumen, resolver los threads inline con `SKIP` y reconocer cada
   comentario general de un `blockerBots`:

   ```bash
   python3 <plugin-root>/scripts/issue-delivery <issue> acknowledge-blocker --comment-id <id> --decision SKIP
   ```

   `acknowledge-blocker` añade idempotentemente `+1` bajo la identidad configurada, registra el
   recibo y rechaza un `SKIP` sin resumen público.
9. Monitorear Actions. Reparar sólo fallos causados o agravados por la branch.
10. Aplicar la invariante de reparación según la superficie declarada. Usar CUA/browser sólo para comportamiento UI afectado; registrar `ninguna` cuando no se requiera herramienta especial.

Terminar como `completed_with_base_failures` cuando sólo queden fallos demostrablemente heredados. Terminar antes si el gate queda limpio. Después de la cuarta ronda, no pedir extensiones ni seguir reparando automáticamente: informar el estado completo, incluidos blockers nuevos de esa ronda. Un `STOP` o `NEEDS_USER_DECISION` bloquea el checkpoint hasta decisión explícita.

Antes de aceptar la ronda final, ejecutar un refetch determinista de ambas superficies de
review:

```bash
python3 <plugin-root>/scripts/issue-delivery <issue> review-gate
python3 <plugin-root>/scripts/issue-delivery <issue> checkpoint --phase review-convergence
```

`review-gate` consulta en vivo los `reviewThreads` inline mediante GraphQL y los comentarios
generales de la PR. Debe bloquear mientras exista un thread automatizado sin resolver o un
comentario general accionable de un `blockerBots` sin `+1` de la identidad GitHub configurada; los comentarios humanos
no participan del gate automático. El checkpoint de `review-convergence` vuelve a ejecutar el
mismo gate y no puede omitirse aunque el snapshot de una ronda esté incompleto o desactualizado.
También rechaza todo `SKIP` reconocido que no figure en un resumen público. Procesar todo
resultado pendiente con `pr-blocker-convergence` antes de reintentar. El informe final debe
indicar ronda, SHA, cantidades por disposición, blockers nuevos versus repetidos, checks, gate,
herramientas planificadas y herramientas realmente usadas.

El CLI detiene procesos propios y conserva worktree, rama, runtimes y estado.

## Review humano posterior y cleanup

Ignorar review humano salvo petición explícita. Para atenderlo, reanudar el mismo run en `review-convergence`, procesar únicamente feedback autorizado y aplicar íntegramente la invariante de reparación y handoff, además de los gates de spec, validación y evidencia.

Después del merge y sólo por petición explícita:

```bash
python3 <plugin-root>/scripts/issue-delivery <issue> cleanup
```

Eliminar recursos de todos los runtimes y el perfil del navegador. Preservar worktree y branch local. Rechazar cleanup antes del merge salvo `--force` explícito del usuario.
