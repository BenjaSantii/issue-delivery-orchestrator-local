---
name: issue-delivery-implement
description: "Implementar o reparar un ticket aprobado con alcance YAGNI, obediencia a AGENTS.md, seguimiento exacto del spec y un commit local por ticket o causa raíz. Usar también ante ajustes dentro de Issue Delivery Orchestrator; delegar la validación automatizada a la skill test antes del handoff."
---

# Implement

Implementar una unidad de trabajo a la vez. Exigir como entrada el spec vigente, ticket o finding, SHA inicial y criterios de aceptación. Leer todos los `AGENTS.md` aplicables antes de editar.

Normalmente corre en un worker nuevo que recibió el paquete de `implement-package`: ese archivo es
la única fuente del encargo. El worker no ejecuta comandos del motor (checkpoint, metrics,
qa-session, size-check), no lanza otros agentes ni la etapa siguiente, y termina con la respuesta
de "Entregar". Incluir en ella los casos del QA Script que el cambio puede invalidar.

## Delimitar

1. Inspeccionar código, tests y patrones existentes.
2. Verificar si el comportamiento ya está satisfecho. En ese caso devolver `NO_OP` con evidencia y validación; no crear commit vacío.
3. Identificar la solución mínima compatible con el spec. No añadir abstracciones, compatibilidad ni defensas especulativas.
4. Si el pedido contradice spec, ticket o una decisión aprobada, no editar. Devolver `NEEDS_USER_DECISION` con las fuentes en conflicto.
5. Mantener cualquier log, prompt o memoria bajo el directorio del run en `.local-runtime`.

## Implementar

- Respetar la arquitectura y política de auditoría del backend dueño.
- Mantener interfaces pequeñas y cambios localizados.
- Inspeccionar cobertura existente antes de añadir tests; extender un seam estable para comportamiento o riesgo distinto.
- Aplicar [pruebas que se conservan en la PR](../test/references/retained-tests.md) al escribir
  tests y antes del commit: conservar cada riesgo en su capa dueña y dejar los scripts de QA
  temporales bajo el run ignorado, sin duplicar la matriz interna en las pantallas.
- No editar migraciones comprometidas; generarlas con el script del proyecto dueño.
- Mantener llamadas externas fuera de transacciones.
- No alterar archivos ajenos al ticket, salvo que sean indispensables para la causa raíz.
- No pushear.

## Validar

1. Invocar `test` con el SHA inicial, el delta del ticket, los criterios de aceptación,
   los comandos declarados en `## Validation` y la branch target del perfil.
2. No seleccionar ni ejecutar directamente unit tests, integración, typecheck, lint o E2E;
   consumir el recibo `PASS`, `FAIL` o `BLOCKED` de `test`.
3. Ante un `branch-caused`, reparar y volver a invocar `test` hasta tres ciclos. Si sigue
   rojo, devolver `BLOCKED` con los logs reales.
4. No reparar `base-failure` ni `environment`; reportarlos con la atribución de `test`.

## Reparaciones posteriores a Implement

Cuando el input sea un ajuste, corrección o reporte de algo que no funciona, cualquiera sea su origen:

1. Convertir el reporte en un escenario de aceptación reproducible y listar las historias afectadas.
2. Implementar e invocar `test` para la validación automatizada enfocada.
3. Entregar el SHA candidato, escenario, superficies afectadas y evidencia mínima propuesta al skill padre.
4. Para lógica determinista consumir el recibo de `test`. Levantar Local Runtime sólo cuando la integración lo exija. Invocar `issue-delivery-cua-review` en modo `superset` o `vanilla`, `issue-delivery-browser-review` en modo `codex` o `issue-delivery-playwright-review` en modo `conductor`, únicamente si cambió o puede haber regresado comportamiento visual/interactivo.
5. Si la verificación falla, usar el finding como nueva entrada. Para blockers remotos, `pr-blocker-convergence` debe emitir `STOP` después de dos iteraciones fallidas de la misma cadena causal.
6. Invalidar sólo el PASS o recibo de una superficie que una edición posterior pueda afectar.

No ejecutar revisión UI por defecto para backend, documentación, tests o build. Si el cambio puede afectar una historia UI, crear `REPAIR-<n>` y verificar esa historia con el reviewer real; si la herramienta necesaria no está disponible, devolver `BLOCKED`.

## Entregar

Antes del commit, devolver:

- Archivos del delta.
- Proyectos o paquetes afectados.
- Comandos ejecutados y resultados.
- Tests no ejecutados y razón.
- Historias UI invalidadas por el cambio.
- Estado de verificación por superficie: `REQUIRED`, `PASS` o `BLOCKED`, incluyendo herramienta y razón.

Crear un commit descriptivo sólo cuando todo lo atribuible al ticket esté verde. Para una reparación de review, usar un commit por causa raíz; agrupar únicamente findings inseparables.

Durante Refactor, limitar cambios al allowlist entregado. Salir de él sólo para una violación concreta de `AGENTS.md` y registrar regla, path y razón.
