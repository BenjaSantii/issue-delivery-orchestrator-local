---
name: issue-delivery-browser-review
description: "Verificar historias UI y reparaciones mediante el Browser integrado de la app de Codex sobre un Local Runtime, usando Playwright headless sólo ante una brecha demostrada de file-upload o hover. Usar en runs de Issue Delivery Orchestrator en modo Codex; bloquear fuera del worktree/chat de Codex, sin Browser o ante UI nativa no automatizable."
---

# Codex Browser Revision

Actuar como verificador de UI, no como implementador. Probar el comportamiento observable contra
el spec aprobado y producir el mismo contrato de evidencia que Cua Revision, declarando
`provider: codex-browser`.

## Preflight

1. Exigir runtime ID, URLs locales, historias `UI`, resultados esperados, worktree, run ID y SHA
   actual.
2. Confirmar mediante el estado del run que `developmentMode` sea `codex` y `reviewerMethod` sea
   `codex-browser`. Para estados legacy sin modo, aceptar sólo `codex-browser`.
3. Exigir que la sesión ejecute en la app de Codex y tenga disponible
   `$browser:control-in-app-browser`. Bloquear sin cambiar automáticamente a Cua.
4. Usar Browser como revisor principal, con un binding persistente propio. No usar la extensión de
   Chrome ni una sesión personal.
5. Confirmar que el Local Runtime sea alcanzable desde Browser y que el sitio tenga permiso.
6. Clasificar antes de interactuar qué stories exigen `file-upload` o un estado `hover`. Leer
   [headless-assistance.md](../issue-delivery-orchestrator/references/headless-assistance.md), pero
   mantener Browser como primera opción. No iniciar Playwright sólo porque una story contiene
   hover.
7. Bloquear directamente sólo si la story exige otra aplicación, cámara, diálogo o UI nativa que
   Browser y Playwright headless tampoco puedan cubrir.
8. Confirmar antes de probar que cada screenshot podrá persistirse como PNG bajo
   `.local-runtime/issue-delivery-orchestrator/<run-id>/validation/ui/`. Si la superficie disponible
   sólo devuelve una imagen efímera y no permite guardarla allí, devolver `BLOCKED`.

## Interactuar

1. Invocar `$browser:control-in-app-browser` y leer su documentación completa antes de usar el
   Browser por primera vez en la sesión.
2. Restablecer una precondición conocida usando cuentas y datos locales sembrados.
3. Ejecutar el flujo mediante acciones equivalentes a las del usuario: navegar, hacer clic,
   escribir, seleccionar y esperar estados observables.
4. Usar snapshots, DOM, consola o red sólo para localizar controles y diagnosticar. No ejecutar
   JavaScript para mutar DOM, storage o estado de aplicación, invocar APIs de negocio ni fabricar
   el resultado aceptado.
5. Confirmar cada resultado mediante una observación nueva posterior a la acción. Contrastar el
   estado interactivo con un screenshot cuando el criterio sea visual.
6. Probar estados vacíos, errores o permisos incluidos en la aceptación.
7. Mantener una sola pestaña principal por flujo; renovar el binding si la navegación reemplaza o
   cierra la pestaña.

Si Browser no puede ejecutar una operación requerida, aplicar el protocolo de brecha de capacidad.
No confundir un resultado incorrecto de la aplicación con una limitación del Browser: si la acción
sí ocurrió pero la UI no cumple el criterio, devolver `FAIL` y reparar el producto.

## Verificar una reparación

Exigir además el reporte original y el escenario `REPAIR-<n>` o las historias afectadas.

1. Confirmar que Browser y las apps ejecuten exactamente el SHA entregado.
2. Reproducir el camino reportado desde su precondición.
3. Verificar el resultado corregido y una regresión adyacente material cuando corresponda.
4. Emitir PASS sólo con una observación y screenshots posteriores al último cambio.
5. Invalidar el PASS ante cualquier edición posterior capaz de afectar el flujo.

No sustituir la revisión por tests, inspección del código o llamadas directas a APIs. La única
excepción es la asistencia headless definida para brechas demostradas de `file-upload` o `hover`.
Debe interactuar con la UI real, producir un recibo ligado al SHA/runtime y conservar evidencia
visual. Si Browser y esa asistencia no pueden alcanzar o probar el escenario, devolver `BLOCKED`;
el skill no cambia de modo, worktree ni reviewer.

## Findings

Por cada fallo, devolver:

- Story ID y criterio incumplido.
- Pasos mínimos de reproducción.
- Resultado esperado y observado.
- Estado o screenshot.
- Severidad práctica.
- Historias invalidadas.

No editar código. Entregar findings a `issue-delivery-implement` y repetir sólo las historias invalidadas sobre el
estado final de la branch. Prohibir el handoff mientras exista `FAIL` o `BLOCKED`.

## Evidencia final

Leer
[evidence-annotations.md](../issue-delivery-orchestrator/references/evidence-annotations.md).
Guardar en `validation/ui/` sólo PNG del estado final aceptado, con nombre
`<ID>-<NN>-<slug>.png`, y escribir en `validation/notes.md` una línea `<ID>: qué demuestra` por
historia. No escribir el manifiesto: lo genera `publish-run`. Incluir una captura por punto visual
relevante, sin secretos, estados fallidos intermedios ni duplicados.

Callouts sólo cuando el caption no baste, con bounds del mismo estado DOM/visual de la captura.
Si hubo asistencia headless, escribir el manifiesto manual con `headlessAssistance` según la
referencia.
