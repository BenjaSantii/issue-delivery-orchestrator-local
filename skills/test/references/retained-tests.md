# Pruebas que se conservan en la PR

La validación de una entrega puede ser más amplia que su suite de regresión. Ejecutar todos
los casos del guion de QA no obliga a convertir cada caso en un test permanente.

## Elegir qué conservar

- Reutilizar primero los tests existentes. Añadir o extender una prueba cuando proteja un
  comportamiento importante que todavía no tenga una señal de fallo suficiente.
- Asignar cada invariante a la capa más baja que pueda verificarlo de forma fiable. Las capas
  superiores sólo necesitan pruebas adicionales para riesgos propios de su integración.
- Conservar las regresiones de bugs corregidos y la cobertura necesaria de concurrencia,
  idempotencia, permisos y persistencia. Una misma prueba puede cubrir varios casos QA; no crear
  un archivo o una suite por QA ID. Respetar los tests expresamente aprobados en el spec/ticket.
- En hooks/services, comprobar transiciones, rollback, reintentos y respuestas fuera de orden.
  En pantallas, comprobar sólo comportamiento visible y conexión: acciones, mensajes y estados
  de controles. No repetir allí toda la matriz interna del hook/service, salvo que exista un
  fallo de integración distinto que la prueba inferior no pueda detectar.
- Para cada prueba nueva poder explicar qué regresión observable detecta y por qué no basta
  la cobertura existente. Evitar pruebas de detalles privados, casos triviales y combinaciones
  especulativas sin relación con el cambio.

## Validación que no se commitea

Guardar exploraciones, scripts de diagnóstico, pruebas de fallos puntuales, capturas y salidas
del guion bajo `.local-runtime/issue-delivery-orchestrator/<run-id>/validation/`, ignorado por Git.
Publicar el resultado y la evidencia mediante el flujo de QA en Linear/PR; no añadir estos
artefactos al diff. Las pruebas de carrera que exige el spec sí son tests permanentes.

No mover un test importante a una carpeta ignorada sólo para reducir el diff, ni dejar tests
de producto modificados fuera del commit mientras se usa su PASS para aprobar la PR. Las
validaciones permanentes deben pasar sobre los archivos que realmente se van a entregar.

## Revisar antes del commit y de la PR

Revisar el delta de tests aparte del código de producto. Anotar brevemente los riesgos que
quedan protegidos, su capa dueña y las comprobaciones que se realizaron sólo como QA. El
volumen de líneas sirve para detectar repetición, no como límite ni proporción obligatoria.

Reducir duplicados introducidos por la entrega conservando la aserción y la cobertura de su
dueño. No borrar cobertura previa ajena al cambio, debilitar expectativas para ponerlas verdes,
crear infraestructura para esconder líneas ni separar los tests necesarios en otra PR.
Si el recorte contradice una prueba expresamente aprobada, señalar la discrepancia antes de
retirarla. Esta revisión es parte de Implement/cierre de la PR; no amplía el Refactor de producto
a los tests excluidos de esa fase.

Después del recorte, ejecutar la validación focal sobre el candidato final y actualizar sólo
la evidencia afectada. La QA amplia, los checks requeridos por el repo y la paridad con CI se
mantienen aunque el diff de tests permanentes sea menor.
