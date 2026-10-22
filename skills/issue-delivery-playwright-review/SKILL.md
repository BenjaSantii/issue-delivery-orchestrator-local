---
name: issue-delivery-playwright-review
description: "Verificar historias UI y reparaciones con Playwright (Chromium headless) dentro de la VM de Conductor Cloud, sobre un Local Runtime. Usar en runs de Issue Delivery Orchestrator en modo conductor; delegar historias de teléfono, teclado o gestos táctiles a mobile-web-reviewer y nunca editar código."
---

# Playwright Revision

Actuar como verificador de UI, no como implementador. Probar comportamiento observable contra el
spec aprobado y producir el mismo contrato de evidencia que Cua Revision, declarando
`provider: playwright`.
En Conductor, incluso las historias móviles se validan SIEMPRE en navegador con la skill
`issue-delivery-mobile-web-review`. No solicitar dispositivo/ADB ni devolver NATIVE-ONLY por
limitaciones de simulación; documentarlas sin afirmar que se observó el SO nativo.
Aplicar [conductor-validation.md](../issue-delivery-orchestrator/references/conductor-validation.md):
UI en localhost:3001–3004 y contratos nativos mediante Jest/scripts; adjuntar sus assertions al
caso correspondiente. No confundir un PASS de la lógica con observación del SO real.

## Preflight

En runs nuevos instrumentados, actuar desde la conversación independiente registrada por
el controlador según [fresh-qa.md](../issue-delivery-orchestrator/references/fresh-qa.md).
Además de UI, entregar al evaluador de QA la comprobación de persistencia de los casos que
escriben datos. No aprobar por toast/200 ni usar la conversación del implementador como reviewer.

1. Exigir runtime ID, URLs locales, historias `UI`, resultados esperados, worktree, run ID y SHA
   actual.
2. Confirmar mediante el estado del run que `developmentMode` sea `conductor` y `reviewerMethod`
   sea `playwright`. No sustituir modo ni método.
3. Ejecutar
   `node <plugin-root>/scripts/playwright-review/preflight.mjs --worktree <checkout> --url <url>`
   con cada URL del runtime. Si Chromium falta, aplicar el paso de navegador de
   [conductor-cloud.md](../issue-delivery-orchestrator/references/conductor-cloud.md) y repetir.
   Con `BLOCKED`, devolver `BLOCKED` con el check fallido.
4. Clasificar cada historia por viewport: escritorio o tablet (por defecto 1280×800, o el que
   indique el spec) y teléfono. Toda historia de teléfono, teclado o gesto táctil se verifica con
   el agente `mobile-web-reviewer` (o la skill `issue-delivery-mobile-web-review` si el host no
   tiene agentes), entregándole los mismos datos del run.
5. Confirmar que cada screenshot se escribirá dentro de
   `.local-runtime/issue-delivery-orchestrator/<run-id>/validation/ui/`.

## Interactuar

1. Escribir un script por historia en
   `.local-runtime/issue-delivery-orchestrator/<run-id>/validation/playwright/<story>.mjs`, que
   importe Playwright desde el checkout (`loadPlaywright` de
   `<plugin-root>/scripts/playwright-review/mobile.mjs`) y lo ejecute con `node`. Mantenerlo
   dentro del directorio ignorado del run; nunca en el código del producto.
2. Restablecer una precondición conocida usando cuentas y datos locales sembrados. Iniciar sesión
   por la UI; se puede guardar `storageState` en `browser-profile/` del run para reutilizarlo.
3. Ejecutar el flujo con acciones de usuario: `goto`, `click`/`tap`, `fill`/`type`,
   `selectOption`, `setInputFiles`, `hover` y esperas sobre estados observables
   (`expect(...).toBeVisible()`, `waitForResponse` sólo para sincronizar).
4. Usar DOM, consola o red sólo para localizar controles y diagnosticar. No ejecutar JavaScript que
   mute DOM, storage o estado de la aplicación, ni invocar APIs de negocio para fabricar el
   resultado aceptado.
5. Tras cada acción relevante, capturar un screenshot nuevo y **mirarlo** (leer el PNG) antes de
   juzgar un criterio visual. Un selector visible no prueba que el layout sea correcto.
6. Registrar errores de consola y `pageerror`; un error nuevo atribuible al cambio es un finding.
7. Probar estados vacíos, errores o permisos incluidos en la aceptación.

Si la acción ocurrió pero la UI no cumple el criterio, devolver `FAIL`; no es una limitación de la
herramienta.

## Verificar una reparación

Exigir además el reporte original y el escenario `REPAIR-<n>` o las historias afectadas.

1. Confirmar que las apps del runtime ejecuten exactamente el SHA entregado.
2. Reproducir el camino reportado desde su precondición.
3. Verificar el resultado corregido y una regresión adyacente material cuando corresponda.
4. Emitir PASS sólo con observación y screenshots posteriores al último cambio.

No sustituir la revisión por tests, inspección del código o llamadas directas a APIs. Si el
escenario no es alcanzable por UI o falta infraestructura, devolver `BLOCKED`.

## Findings

Por cada fallo, devolver story ID y criterio incumplido, pasos mínimos, resultado esperado y
observado, screenshot, severidad práctica e historias invalidadas. No editar código. Prohibir el
handoff mientras exista `FAIL` o `BLOCKED`.

## Evidencia final

Leer
[evidence-annotations.md](../issue-delivery-orchestrator/references/evidence-annotations.md).
Guardar en `validation/ui/` sólo PNG del estado final aceptado, con nombre
`<ID>-<NN>-<slug>.png`, y escribir en `validation/notes.md` una línea `<ID>: qué demuestra` por
historia. No escribir el manifiesto: lo genera `publish-run`. Incluir una captura por punto visual
relevante, sin secretos, estados fallidos intermedios ni duplicados.

Indicar el viewport en la nota cuando importe (por ejemplo 390×844). Callouts sólo cuando el
caption no baste, con bounds de `locator.boundingBox()` divididos por el viewport. Guardar ahí
también las capturas que devuelva `mobile-web-reviewer`. `headlessAssistance` no aplica: Playwright
ya es el reviewer.
