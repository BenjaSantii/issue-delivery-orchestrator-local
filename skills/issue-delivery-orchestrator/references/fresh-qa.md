# QA en conversación nueva y consecuencias persistidas

Aplica sólo a runs con `observabilityVersion: 1`. No modificar el contrato ni los chats de
ejecuciones existentes. Complementa la revisión de código. LocalReviewer no es requerido en
Conductor; fuera de Conductor conserva sus permisos y gates. La calibración con ejemplos
humanos queda fuera de esta implementación.

## Abrir el reviewer

Tras Implement/Refactor, preparar una vez el Local Runtime con sus cuentas y datos sembrados.
El chat que implementa se llama `Generador`; el evaluador se llama `QA <run> <commit> <contrato>`.
El implementador entrega spec, tickets, SHA, runtime y URLs; no un relato de por qué su trabajo
debería aprobarse. Mantener el código quieto mientras QA verifica ese candidato.

En Conductor, desde el controlador con acceso al CLI autenticado del host:

```bash
python3 <plugin-root>/scripts/issue-delivery <issue> qa-session \
  --workspace <workspace-id> --implementation-session <session-id> \
  --url http://localhost:<puerto> [--model <modelo> [--effort <esfuerzo>]]
```

Si el run se creó con `--qa-model`/`--qa-effort` (los pone `cloud-launch` en el primer mensaje),
`qa-session` usa esa configuración cuando no recibe `--model`; si no la hay, usa `QA Model`/
`QA Effort` del spec aprobado del run. El Generador no elige el modelo.

`--implementation-session` identifica el chat que llama al QA. Con `--model`, el QA usa ese
modelo del catálogo de Conductor y `--effort`, o el `defaultEffort` del agente si se omite;
el usuario o el lanzamiento eligen así un modelo distinto del Generador. `--effort` sin
`--model` se rechaza. Un modelo o esfuerzo que no esté en el catálogo bloquea; no se cae en
silencio a la herencia. Sin `--model`, el comando hereda agente, modelo y esfuerzo del chat que
llama, desde la configuración real de Conductor; no fija Codex.
Al heredar, si la sesión no expone el modelo, seleccionarlo en Conductor antes de repetir; no
adivinarlo. Conductor no guarda el esfuerzo mientras el chat usa el default del agente: en ese
caso el comando toma el `defaultEffort` del catálogo, que es el que el chat ejecuta.
El estado registra `configurationSource` (`explicit` o `inherited`). El comando verifica la
configuración del chat creado y no reutiliza silenciosamente un QA con otro modelo o esfuerzo.

El comando crea otra sesión en el mismo workspace, sin resume ni historial heredado. Registra
su ID y fija el commit y los hashes de los artefactos aprobados. Reintentar el comando recupera
la sesión de ese candidato, sin duplicarla. Un cambio de código o del contrato exige otra sesión.
Si el CLI no está disponible, ejecutar este paso desde el controlador del host con acceso al
estado del run; no sustituirlo por `codex exec`, `claude --print` o autoevaluación del implementador.

Antes de crear el QA, escribir en el run un brief operativo (por ejemplo
`validation/qa-brief.md`) y pasarlo con `qa-session ... --brief <ruta>`. Contiene sólo hechos para
operar: URLs por rol, cuentas de prueba, datos sembrados con sus IDs, la ruta o pantalla de cada
historia y cómo llegar a cada estado del QA Script. No incluye por qué el trabajo debería aprobarse
ni el relato de la implementación. Sin él, el QA lee código del producto para descubrir esos datos,
lo que en runs medidos fue entre 16 % y 21 % de su consumo.

Después de crear el QA, el Generador espera bloqueado, sin turnos de polling:

```bash
python3 <plugin-root>/scripts/issue-delivery <issue> qa-wait [--timeout-minutes 360]
```

Espera a que el chat registrado deje de estar `working` y devuelve su estado, un resumen de
`validation/qa-results.json` (existe, `reviewSessionId`, casos por estado) y `state`:
`complete` si el QA escribió todos los casos pendientes, o `stopped` con `missing`, `reason`
(`usage-limit` y su hora `resets`, o `unknown`). Inactivo no es terminado: un límite de uso o un
reinicio de la VM también lo dejan inactivo. No valida: el checkpoint lo hace. Con `stopped` por
límite, terminar el turno sin `sleep`; lo retoma un agente externo con `cloud-recover`. Si el
Generador está corriendo y el QA sigue detenido, retomarlo con `qa-resume`. En Claude ejecutarlo con Bash en segundo plano (`run_in_background`) y
terminar el turno; el host vuelve a invocar al Generador cuando el comando termina. En hosts
sin notificación, ejecutarlo en primer plano con un timeout mayor que el suyo. No consultar el
chat de QA con `session status`, `sleep` ni mensajes mientras espera. Si vence, devuelve
BLOCKED y se vuelve a ejecutar; si el QA quedó inactivo sin resultados, revisar su chat antes
de reintentar.

### Si el QA se detiene

El QA actualiza `qa-results.json` después de cada caso, así que un corte no borra lo resuelto.
Cuando su chat se detiene sin terminar (límite de uso, reinicio de la VM, caída del runtime):

1. Volver a levantar el runtime sobre el mismo SHA y comprobar que responde.
2. Retomar **el mismo chat** con
   `qa-resume [--note <ruta-en-el-run>]`. El mensaje le pide seguir donde quedó, sin repetir
   casos ya resueltos; la nota cuenta qué se restauró. Un chat cortado por límite se retoma igual
   después de cambiar de cuenta o de que el límite se reinicie: no hace falta otro.
3. Sólo si ese chat no puede continuar (borrado, contexto agotado o falla al retomarlo), crear un
   reemplazo con `qa-session ... --replace`. Hereda los casos que el QA anterior ya resolvió en
   ese commit y ejecuta sólo los pendientes. También sirve cuando la sesión que implementa cambió
   (por ejemplo una reanudación).

Nunca crear sesiones de QA a mano con `conductor session create` ni registrarlas con scripts
propios: se pierden la recuperación por nombre y la herencia de casos. Si `qa-session` o
`qa-resume` bloquean, detenerse y avisar al usuario con el error.

### QA largo

Con más de seis casos por ejecutar, el chat de QA coordina: agrupa 3 a 5 casos por historia o
superficie y los ejecuta con subagentes nuevos, uno a la vez, porque comparten runtime, DB y datos
sembrados. Él junta los resultados y escribe `qa-results.json` después de cada grupo. Así su
contexto no acumula las capturas y salidas de todos los casos.

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
Aplicar [conductor-validation.md](conductor-validation.md): usar localhost en puertos 3001–3004
para las vistas web; comprobar GPS, Alert y otros contratos nativos con Jest o scripts. Un caso
respaldado por esas assertions y su evidencia web aplicable puede ser PASS sin dispositivo ni
WAIVED. Conservar la distinción entre lógica probada y comportamiento del SO no observado.

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

El controlador consume los archivos, publica con `publish-run` y completa el checkpoint.
Se rechazan resultados del implementador, de otro reviewer, de otro runtime/commit/contrato o
con estado persistido distinto del esperado. El motor valida el recibo y los artefactos; la
ejecución real de las queries y la procedencia del contexto nativo se auditan con la sesión del
host, no se prueban criptográficamente con un JSON. Mantener FAIL/BLOCKED y waivers del usuario.
Reparar mediante Implement y repetir los casos invalidados desde una conversación independiente.

## Reejecución parcial tras una reparación

Un commit nuevo exige otra conversación de QA, pero no repetir todo el guion. Al crearla, el
Generador lista los casos que su reparación puede afectar (los mismos que
`issue-delivery-implement` declara invalidados) y los pasa con `--rerun`:

```bash
python3 <plugin-root>/scripts/issue-delivery <issue> qa-session \
  --workspace <workspace-id> --implementation-session <session-id> \
  --url http://localhost:<puerto> --rerun QA-002 --rerun QA-005
```

- Se reejecutan los casos pedidos, todo caso que no quedó `PASS` y los que falten. Los `PASS`
  restantes se heredan con su commit y su evidencia; una cadena de reparaciones conserva el
  commit original de cada caso.
- El motor archiva el resultado anterior como `validation/qa-results-<commit12>.json` y registra
  en `qaSession.carried` el origen de cada caso heredado. El nuevo QA escribe sólo los casos que
  ejecuta y no toca la evidencia de los heredados.
- Sólo se reutiliza si el spec y los tickets no cambiaron, el resultado anterior es del QA
  registrado y su commit, y ese commit sigue siendo ancestro de HEAD. Si no, `qa-session`
  devuelve BLOCKED y se repite sin `--rerun`.
- Ante duda, `--rerun all`. Migraciones, schemas o contratos compartidos, auth/permisos y
  cambios transversales (layout, navegación, datos sembrados) siempre usan `all`.
- El checkpoint valida los heredados contra su archivo archivado (capturas, salidas y
  persistencia) y el comentario de Linear indica qué casos se heredaron y de qué commit.

## Tras un rebase o restack

Un `restack` o un rebase cambia el commit aunque el parche de la rama sea el mismo, y el QA está
fijado a un commit. No repetir el QA ni marcar casos como WAIVED por eso. Con el QA terminado:

```bash
python3 <plugin-root>/scripts/issue-delivery <issue> qa-rebase [--rerun <QA-ID>] [--old-base <sha>]
```

El motor compara el parche propio de la rama antes y después (`git patch-id` contra su base) y, si
es idéntico y el spec y los tickets no cambiaron, fija el QA al nuevo HEAD: cada PASS se hereda
del commit equivalente y el comentario lo indica. Antes, revisar qué cambió entre la base vieja y
la nueva (`git diff --stat <base-vieja> <base-nueva>`): si toca la superficie de un caso, pasarlo
con `--rerun` y ejecutarlo con `qa-session ... --replace`. Si el parche cambió, el comando bloquea
y el QA corre de nuevo para HEAD. Los runs que registraron el QA antes de esta versión no guardan
la base: pasarla con `--old-base` (la punta de la rama padre o el merge-base de entonces).

