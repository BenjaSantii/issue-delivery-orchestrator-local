# Métricas de entrega

Aplica sólo si `status` devuelve `observabilityVersion: 1`. No añadir el flag a un estado
existente ni instrumentar una entrega en curso para migrarla. Los datos viven en `metrics/`
bajo el run ignorado; no pertenecen al código de producto.

## Tickets e intentos

El controlador es el único escritor de métricas/publicaciones. Antes de empezar un ticket,
con un límite de commit limpio, ejecutar:

```bash
python3 <plugin-root>/scripts/issue-delivery <issue> metrics-start \
  --unit TICKET-001 --attempt 1 --model gpt-6.1-sol --effort high
```

Después del commit validado, o al terminar un intento fallido/bloqueado, ejecutar:

```bash
python3 <plugin-root>/scripts/issue-delivery <issue> metrics-finish \
  --unit TICKET-001 --attempt 1 --status PASS --receipt <run>/metrics/receipts/TICKET-001.json
```

Estados: `PASS`, `FAIL`, `BLOCKED`, `NO_OP`. PASS/NO_OP requieren un límite limpio. Un intento
FAIL/BLOCKED con cambios sin commit conserva el tiempo y declara su delta como N/D; no commitear
código roto sólo para medirlo. No falsear un PASS para registrar tiempo. Un nuevo ciclo usa otro
`--attempt`; reintentar finish del mismo intento es idempotente, no una reparación nueva.

Recibo opcional de observaciones (omitir lo que no se haya medido):

```json
{
  "checks": {"passed": 12, "failed": 0, "blocked": 0},
  "repairCycles": 1,
  "findings": [{"id": "F-ROUTE-RACE", "severity": "high", "duplicateOf": null}],
  "waitingIntervals": [
    {"reason": "infrastructure", "startedAt": "2026-10-20T10:00:01Z", "finishedAt": "2026-10-20T10:00:09Z"}
  ]
}
```

Los intervalos deben estar dentro de la unidad y no solaparse. `human` e `infrastructure`
se separan del tiempo total; sin intervalos observados, el desglose de ejecución es N/D,
no cero. IDs de findings estables en todo el run; `duplicateOf` apunta al hallazgo ya conocido.
No aumentar conteos por repetir el mismo fallo o volver a ejecutar el mismo comando.

## Fases y tokens

Cada checkpoint registra automáticamente una unidad `phase-<fase>`, con tiempo y delta entre
sus commits. Puede recibir las observaciones con `checkpoint --metrics-receipt <ruta>`.
Los fallos y ciclos se anotan al ocurrir y se resumen en ese recibo al cerrar la fase.

Conductor publica tokens al cerrar turnos. Cuando la sesión haya respondido, importar:

```bash
python3 <plugin-root>/scripts/issue-delivery <issue> metrics-usage \
  --unit TICKET-001 --attempt 1 --session <id> --attribution dedicated
```

`dedicated` sólo sirve para una sesión contenida enteramente en el intervalo de ese ticket,
iniciada después de metrics-start y terminada antes de metrics-finish. Si comparte contexto o
trabajo con varios tickets/fases, usar `shared`: el consumo queda identificado como compartido,
sin repartirlo artificialmente. No crear sesiones extra sólo para conseguir esta atribución.
Repetir imports no suma de nuevo los mismos reportes; el total del run deduplica solicitudes.
Importar cada sesión hija de implementación/test que participó en la unidad; añadir una sesión
no reemplaza las anteriores. Si falta uso de alguna, el resumen observado queda marcado parcial.
Nunca sumar contadores acumulativos, caché o razonamiento encima de entrada+salida.

Se puede importar un export completo del CLI con `--transcript <archivo-en-el-run>`; conservar
exports bajo `logs/` o `metrics/receipts/`, no junto a los registros internos `metrics/*.json`.
El colector soporta el formato Codex `conductor_sdk_metadata.codexUsage` v1 confirmado en vivo.
Un turno abierto, un formato no soportado, una transcripción truncada o un host sin datos reales
se muestran como N/D. No estimar tokens ni deducir dinero facturado de una suscripción.
El controlador debe volver a importar el uso pendiente al terminar el turno y antes del resumen
final; el propio consumo de ese último informe puede quedar pendiente para una lectura externa.

## Linear e interpretación

metrics-finish, metrics-usage y los checkpoints actualizan un único comentario
`Métricas de entrega — <run-id>`. Con comentarios deshabilitados, usan el bloque `metrics` de la
descripción, conservando el resto. `publish-metrics` reintenta una publicación fallida desde los
datos locales. Un fallo de publicación no invalida código validado, pero debe reportarse.

El comentario separa producto/tests, adiciones/eliminaciones y el delta final contra la target
(o declara que sólo pudo medir desde la adopción). Tickets, fases y reparaciones se solapan:
no sumar sus tiempos/deltas como si fueran trabajo independiente. El total de tokens es sólo
el consumo observado de solicitudes únicas importadas, no necesariamente toda la entrega.
Comparar por modelo, esfuerzo y dificultad, junto con los fallos distintos que cada etapa
detectó. Tokens por línea y cantidad de tests no son medidas de calidad ni gates de aprobación.
