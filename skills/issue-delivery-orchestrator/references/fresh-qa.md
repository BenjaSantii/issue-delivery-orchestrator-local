# QA en conversación nueva y consecuencias persistidas

Aplica sólo a runs con `observabilityVersion: 1`. No modificar el contrato ni los chats de
ejecuciones existentes. Complementa la revisión de código. LocalReviewer no es requerido en
Conductor; fuera de Conductor conserva sus permisos y gates. La calibración con ejemplos
humanos queda fuera de esta implementación.

## Abrir el reviewer

Tras Implement/Refactor, preparar una vez el Local Runtime con sus cuentas y datos sembrados.
El implementador entrega spec, tickets, SHA, runtime y URLs; no un relato de por qué su trabajo
debería aprobarse. Mantener el código quieto mientras QA verifica ese candidato.

En Conductor, desde el controlador con acceso al CLI autenticado del host:

```bash
python3 <plugin-root>/scripts/issue-delivery <issue> qa-session \
  --workspace <workspace-id> --implementation-session <session-id> \
  --url http://localhost:<puerto> --model gpt-6.1-sol --effort high
```

El comando crea otra sesión en el mismo workspace, sin resume ni historial heredado. Registra
su ID y fija el commit y los hashes de los artefactos aprobados. Reintentar el comando recupera
la sesión de ese candidato, sin duplicarla. Un cambio de código o del contrato exige otra sesión.
Si el CLI no está disponible, ejecutar este paso desde el controlador del host con acceso al
estado del run; no sustituirlo por `codex exec`, `claude --print` o autoevaluación del implementador.

En Codex/Superset/Vanilla, usar el mecanismo del host para abrir un chat o reviewer con contexto
nuevo, en el mismo worktree/runtime, y registrar su recibo con `qa-register --receipt <ruta>`:

```json
{"host": "codex", "freshContext": true, "implementationSessionId": "impl-id", "reviewSessionId": "qa-id"}
```

El controlador obtiene esos IDs del host; no inventarlos ni renombrar el mismo chat como QA.
Conductor exige la creación/verificación real con qa-session y no admite este registro manual.
Si el host no puede crear un contexto independiente, devolver BLOCKED. Entregar al nuevo chat
las entradas anteriores y esta referencia. No crear otro worktree ni levantar otro stack.

## Qué comprobar

El reviewer no edita producto ni repara: ejecuta el guion aprobado sobre la app real, con el
reviewer web/nativo del modo y el especialista móvil cuando corresponda. Guarda scripts y
evidencia bajo el run ignorado. Comprueba el efecto de cada operación y su lectura posterior,
no sólo el clic, selector, toast o código HTTP.
En Conductor esto SIEMPRE significa web y simulación de teléfono con la skill mobile-web-review;
no pedir teléfono/ADB ni bloquear por su ausencia. Declarar las limitaciones de SO/GPS/permisos
como no observadas y usar simulación/tests de callbacks para las reglas verificables.

Para casos que escriben, reintentan, rechazan o revierten persistencia, verificar la DB local:

- capturar antes y después mediante queries que proyecten los campos del caso;
- comprobar valores, IDs, relaciones, auditoría, ausencia de duplicados y ausencia de cambios
  indebidos en rechazos/rollback, según el contrato;
- consultar con transacciones READ ONLY y registrar la salida de `SHOW transaction_read_only`;
- usar la conexión de la DB del runtime registrado, nunca producción. No publicar DSN, tokens,
  datos reales ni credenciales. No escribir directamente en DB para fabricar el resultado;
- guardar los estados como JSON, query redactada y prueba del destino local/read-only.

Al diseñar el guion, marcar `DB` en Evidence para todo caso que requiera esta comprobación.
La concurrencia siempre la exige. Falta de acceso a DB es BLOCKED, no NOT_APPLICABLE. Sólo
un caso visual/sin consecuencia persistida puede declarar NOT_APPLICABLE con una razón concreta.

## Resultado

El reviewer escribe `validation/qa-results.json` y el manifiesto UI del modo, y devuelve findings
reproducibles al controlador. Añadir `reviewSessionId` al nivel raíz y, en cada caso PASS:

```json
{
  "persistence": {
    "status": "VERIFIED",
    "runtimeId": "<runtime-id>",
    "expectation": "Una sola evaluación asociada a la visita correcta",
    "before": "<run>/validation/qa/QA-001-before.json",
    "after": "<run>/validation/qa/QA-001-after.json",
    "query": "<run>/validation/qa/QA-001.sql",
    "readOnlyProof": "<run>/validation/qa/QA-001-readonly.json",
    "expectedAfter": {"visitId": "seeded-visit", "count": 1, "score": 7}
  }
}
```

El archivo after debe contener exactamente la proyección JSON esperada. La prueba read-only
se construye con el destino del runtime y el resultado observado de la consulta del servidor:

```json
{"runtimeId": "<runtime-id>", "target": "local-runtime", "transactionReadOnly": true}
```

Para un caso sin DB: `"persistence": {"status": "NOT_APPLICABLE", "reason": "Sólo cambia el espaciado"}`.
No se acepta esta excepción en concurrencia ni cuando el guion marca Evidence con DB.

El controlador consume los archivos, publica con publish-qa-results y completa el checkpoint.
Se rechazan resultados del implementador, de otro reviewer, de otro runtime/commit/contrato o
con estado persistido distinto del esperado. El motor valida el recibo y los artefactos; la
ejecución real de las queries y la procedencia del contexto nativo se auditan con la sesión del
host, no se prueban criptográficamente con un JSON. Mantener FAIL/BLOCKED y waivers del usuario.
Reparar mediante Implement y repetir los casos invalidados desde una conversación independiente.
