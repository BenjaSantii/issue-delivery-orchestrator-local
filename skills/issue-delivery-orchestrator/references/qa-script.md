# QA Script

El guion de QA es la lista aprobada de casos que la revisión manual debe ejecutar. Vive en el
spec bajo `## QA Script`, así que se aprueba y se publica con él. El motor lo valida antes de
publicar el spec y exige sus resultados antes de cerrar la revisión manual.

Un spec aprobado antes de existir esta sección sigue el flujo anterior: no se le exige guion ni
resultados.

El guion define lo que se ejecuta, no cuántos tests se añaden a la PR. Aplicar
[retained-tests.md](../../test/references/retained-tests.md): mantener la QA completa y conservar
sólo las pruebas permanentes con una señal de regresión distinta. Los scripts y las capturas
temporales permanecen en el run ignorado; los tests de carrera exigidos sí se commitean.

## Formato

```md
## QA Script

| QA ID | Type | Covers | Surface | Steps | Expected | Evidence | Regression test |
| --- | --- | --- | --- | --- | --- | --- | --- |
| QA-001 | happy-path | US-001 | UI | Manager abre la ruta en curso y la cierra | Ruta Finalizada con informe | Captura del detalle | — |
| QA-002 | edge-case | US-001 `edge-cases` | API | Cerrar una ruta sin paradas | 200 y completionType COMPLETE | Respuesta JSON | — |
| QA-003 | concurrency | US-001 `concurrency` | API | 10 PUT /routes/:id/finish en paralelo con el cron | 1 cierre, 1 informe, 9 respuestas idempotentes | Tabla de códigos y lectura final | `apps/x/src/routes/routes.finish.race.db.spec.ts` |
| QA-004 | authorization | US-002 `roles-authorization` | API | Vendedor llama PATCH /routes/:id/stops/reorder de otra ruta | 403 sin cambios | Respuesta y lectura | — |
```

- `QA ID`: `QA-001`, `QA-002`… únicos.
- `Type`: `happy-path`, `edge-case`, `concurrency`, `authorization` o `failure`.
- `Covers`: historias (`US-001`) y categorías del Risk Catalog en backticks.
- `Surface`: `UI` (reviewer del modo), `API` (requests contra el Local Runtime) o `command`
  (script, job o consulta).
- `Steps`: precondición y pasos concretos, con los datos sembrados que se usarán.
- `Expected`: resultado observable que decide PASS o FAIL.
- `Evidence`: qué se guardará (captura, respuesta, tabla de resultados, lectura final).
- `Regression test`: obligatorio en `concurrency`; la ruta del test de carrera que el ticket
  commitea (por ejemplo `*.race.db.spec.ts`). `—` en los demás tipos.

Cuando un spec compartido se entrega en varias issues (slices), cada issue lleva su propio
`## QA Script` con una línea `Scope:` antes de la tabla, con sus historias:

```md
## QA Script

Scope: US-001, US-002, US-003, US-004
```

Las reglas de cobertura se aplican sólo a esas historias, y un caso no puede cubrir historias
fuera del scope.

## Reglas de cobertura

El motor rechaza el spec si no se cumplen:

1. Cada historia de `## User Stories` tiene al menos un caso `happy-path`.
2. Cada categoría del Risk Catalog con `Applies: Yes` aparece en `Covers` de al menos un caso.
3. Si aplica `concurrency`, existe al menos un caso `concurrency`, por `API` o `command`, con su
   test de carrera en `Regression test`.
4. Ningún caso cubre una historia inexistente.

Además, al diseñarlo: un caso `authorization` por rol que pierde o gana acceso, un caso `failure`
por cada estado incierto del State model, y casos borde para límites, vacíos y datos inválidos
que el spec nombre. No agregar casos que no falsen un criterio del spec.

## Ejecutar

Ejecutar todos los casos sobre el SHA final y un Local Runtime registrado, en una sola pasada:

- Levantar las apps una vez con `pnpm local-runtime ensure-services` y reutilizarlas para todos
  los casos. No reiniciar servicios entre casos; restablecer la precondición de cada caso con las
  cuentas y datos sembrados.
- Ordenar al final los casos que necesitan un servicio caído (por ejemplo ERP detenido o backend
  cortado a mitad de una escritura). Bajar sólo ese servicio, ejecutar el caso y volver a
  levantarlo con `ensure-services` antes del siguiente.
- Tras una reparación, dejar que el modo watch recargue el cambio o reiniciar sólo la app
  afectada, y repetir únicamente los casos que esa reparación invalida.

- **UI**: con el reviewer del modo (`issue-delivery-cua-review`, `issue-delivery-browser-review`
  o `issue-delivery-playwright-review`) y `mobile-web-reviewer` en historias de teléfono. Usar
  el QA ID como scenario ID. Guardar las capturas en `validation/ui/`.
- **API**: scripts bajo
  `.local-runtime/issue-delivery-orchestrator/<run-id>/validation/qa/<QA-ID>.mjs` (o `.py`,
  `.sh`) que autentican como el usuario sembrado del caso y llaman a la API del runtime. Guardar
  la salida relevante (códigos, cuerpos recortados, lectura final) en
  `validation/qa/<QA-ID>.json` o `.txt`.
- **Concurrencia**: disparar las operaciones en paralelo de verdad (`Promise.all`, `xargs -P`)
  incluyendo, si aplica, el job o cron que compite. Después leer el estado final por la API o
  con una consulta de solo lectura a la base del runtime, y registrar conteos que demuestren el
  invariante (por ejemplo `reports=1`). Repetir 3 veces; un solo fallo es `FAIL`. Confirmar
  además que el test de carrera de `Regression test` existe en el commit y pasa con `test`.
- **command**: ejecutar el job, script o consulta indicado y guardar su salida.

No mutar estado por fuera del flujo del caso para fabricar el resultado. Las salidas no deben
contener tokens, cookies ni contraseñas: redactarlas. El motor rechaza evidencia con patrones de
secretos.

## Resultados

Guardar `validation/qa-results.json` dentro del run:

```json
{
  "qaVersion": 1,
  "verifiedCommit": "<sha de HEAD>",
  "runtimeId": "<runtime-id>",
  "verifiedAt": "<ISO-8601>",
  "cases": [
    {
      "id": "QA-001",
      "status": "PASS",
      "summary": "La ruta quedó Finalizada y el informe muestra 2 paradas no realizadas",
      "screenshots": [".local-runtime/issue-delivery-orchestrator/<run-id>/validation/ui/QA-001.png"]
    },
    {
      "id": "QA-003",
      "status": "PASS",
      "summary": "3 corridas: 1 cierre y 1 informe en cada una; 9 respuestas 200 idempotentes",
      "output": ".local-runtime/issue-delivery-orchestrator/<run-id>/validation/qa/QA-003.txt"
    }
  ]
}
```

- `status`: `PASS`, `FAIL`, `BLOCKED` o `WAIVED`.
- `PASS` de un caso `UI` exige al menos una captura; de `API` o `command`, su archivo de salida.
- `FAIL` y `BLOCKED` impiden cerrar la revisión manual: reparar con `issue-delivery-implement`,
  invalidar sólo los casos afectados y repetirlos.
- `WAIVED` sólo cuando el usuario decide explícitamente no exigir ese caso. Pedirlo en el chat
  con la causa y registrar `"waiver": {"reason": "...", "userInstruction": "<sus palabras>"}`.

## Publicar

```bash
python3 <plugin-root>/scripts/issue-delivery <issue> publish-qa-results \
  --results .local-runtime/issue-delivery-orchestrator/<run-id>/validation/qa-results.json
```

Valida el archivo contra el guion aprobado, sube las capturas a Linear y crea o actualiza un
comentario `Resultado QA — <run-id>` con la tabla de casos, las imágenes y un extracto de cada
salida. Con `LINEAR_COMMENTS_ENABLED=false` escribe el mismo contenido en un bloque `qa-results`
de la descripción. Si la PR ya existe, también actualiza un comentario en ella.

Publicar antes de cerrar la revisión manual, y volver a ejecutar el comando después de crear la
PR para que el resultado quede también en ella. El checkpoint exige el artefacto:

```bash
python3 <plugin-root>/scripts/issue-delivery <issue> checkpoint --phase manual-revision \
  --artifact ui-manifest=<ruta> --artifact qa-results=<ruta>
```
