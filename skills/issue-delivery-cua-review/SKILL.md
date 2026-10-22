---
name: issue-delivery-cua-review
description: "Verificar historias UI y reparaciones mediante Cua Driver en el escritorio local (host) por defecto, sobre una instancia Local Runtime. Usar en runs de Issue Delivery Orchestrator en modo Superset o Vanilla; usar un escritorio Docker aislado sólo cuando el usuario lo indique y nunca editar código."
---

# Cua Revision

Actuar como verificador de UI, no como implementador. Probar comportamiento observable contra el spec aprobado y devolver evidencia reproducible.

Leer [driver-contract.md](references/driver-contract.md) antes de usar Cua Driver.

Usar el escritorio local (host) por defecto. Usar Docker sólo cuando el usuario lo indique.
Mantener `reviewerMethod` y `provider` como `cua-driver`; el entorno cambia dónde corre el
navegador, no el reviewer. Si el entorno elegido no está disponible (permisos del host, Docker o
Cua), devolver `BLOCKED` con la causa concreta. No cambiar de entorno como fallback ni abrir
ventanas personales.

## Preflight

1. Exigir runtime ID, URLs, historias `UI`, resultados esperados, worktree y run ID.
2. Confirmar mediante el estado del run que `developmentMode` sea `superset` o `vanilla` y
   `reviewerMethod` sea `cua-driver`. Para estados legacy sin modo, aceptar sólo `cua-driver`; no
   sustituir modo ni método silenciosamente.
3. En host (por defecto), comprobar `cua-driver --version`, `cua-driver doctor` y
   `cua-driver permissions status`. En Docker indicado por el usuario, comprobar `docker info`,
   `cua --version` y `cua runtime doctor`; crear o reanudar el sandbox del run según el contrato
   y comprobar el escritorio y la URL desde el contenedor.
4. Bloquear si falta una dependencia o la URL no es alcanzable desde el entorno elegido.
   Accessibility y Screen Recording del Mac se exigen sólo en host. No instalar, actualizar
   ni abrir diálogos de permisos desde este reviewer.
5. En host, exigir el navegador y perfil dedicados del run, su PID y `window_id`. En Docker, usar
   únicamente el Chromium y perfil del sandbox del run. Registrar el entorno y sus identificadores
   según el contrato.
6. Confirmar que cada screenshot se escribirá dentro de `.local-runtime/issue-delivery-orchestrator/<run-id>/validation/ui/`.
7. Leer
   [headless-assistance.md](../issue-delivery-orchestrator/references/headless-assistance.md) si una
   story puede requerir `file-upload` o `hover`; mantener Cua como primera opción.

## Verificar una reparación

Exigir además el reporte original, escenario `REPAIR-<n>` o historias afectadas y SHA actual.

1. Confirmar que el navegador y las apps ejecutan exactamente ese SHA.
2. Reproducir el camino reportado desde su precondición, no sólo inspeccionar el componente modificado.
3. Verificar el resultado corregido y una regresión adyacente material cuando corresponda.
4. Emitir PASS sólo con una observación posterior a la acción y evidencia del estado final.
5. Registrar en el recibo `verifiedCommit`, `runtimeId`, escenario/historias, timestamp y screenshots.

No reutilizar evidencia anterior ni aceptar tests automatizados como sustituto. Playwright
headless sólo puede asistir una brecha demostrada conforme al protocolo compartido y debe conservar
evidencia visual. Si el flujo no es alcanzable mediante UI o falta infraestructura, devolver
`BLOCKED`; no devolver PASS por inferencia. Todo cambio relevante posterior al SHA verificado
invalida el recibo.

## Verificar historias

Para cada historia UI:

1. Restablecer una precondición conocida usando cuentas y datos locales sembrados.
2. Obtener una captura nueva de la ventana del run y contrastarla con su árbol de accesibilidad
   cuando esté disponible. En host, usar `get_window_state(pid, window_id)`; en Docker, la
   captura del escritorio del sandbox.
3. Preferir `element_token` cuando esté disponible. Usar coordenadas del PNG exacto para las
   acciones visuales; nunca mezclar coordenadas del escritorio, ventana y viewport del navegador.
4. Ejecutar el flujo completo de usuario, incluidos estados vacíos, errores o permisos que formen parte de la aceptación.
5. Confirmar el resultado mediante una observación nueva, no sólo por el éxito de la acción.
6. Registrar `PASS` o `FAIL`, pasos, resultado observado y evidencia.
7. Tomar PNG únicamente después de alcanzar el estado final aceptado. Evitar secretos, tokens y datos sensibles.

Si Cua no puede ejecutar `file-upload` o mantener/demostrar un `hover` necesario, distinguir primero
esa limitación de un fallo real de la aplicación. Aplicar Playwright sólo a la story afectada y
añadir `headlessAssistance` al manifiesto; no cambiar provider ni reviewer.

No editar código ni aceptar diferencias visuales basándose sólo en intención. Cuando exista Figma accesible, comparar layout, contenido, jerarquía y estados relevantes; exigir la mayor fidelidad razonable.

## Findings

Devolver cada fallo con:

- Story ID y criterio incumplido.
- Pasos mínimos de reproducción.
- Resultado esperado y observado.
- Screenshot o estado de ventana.
- Severidad práctica.
- Indicación de qué historias quedan invalidadas.

El skill padre entrega estos findings a `issue-delivery-implement`. Después de una reparación, volver a ejecutar sólo las historias invalidadas, pero siempre sobre el estado final de la branch.

No entregar control al usuario para que verifique mientras exista un `FAIL` o `BLOCKED`. El handoff sólo queda habilitado con PASS sobre el SHA final.

## Evidencia final

Leer
[evidence-annotations.md](../issue-delivery-orchestrator/references/evidence-annotations.md).
Guardar en `validation/ui/` sólo PNG del estado final aceptado, con nombre
`<ID>-<NN>-<slug>.png`, y escribir en `validation/notes.md` una línea `<ID>: qué demuestra` por
historia. No escribir el manifiesto: lo genera `publish-run`. Incluir una captura por punto visual
relevante, sin secretos, estados fallidos intermedios ni duplicados.

Callouts sólo cuando el caption no baste, con bounds del árbol de accesibilidad de la misma
captura o, si no expone una región útil, de las coordenadas del PNG exacto.

En host, cerrar al terminar sólo el navegador y la sesión propios del run. En Docker, conservar el
sandbox durante los ciclos revisión-reparación y retirar sólo el sandbox propio al terminar la
revisión final o al bloquear el run. Conservar las capturas y el recibo del entorno.

