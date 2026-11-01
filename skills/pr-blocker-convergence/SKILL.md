---
name: pr-blocker-convergence
description: "Clasificar, registrar y hacer converger automáticamente blockers de una PR durante hasta cuatro rondas. Usar para deduplicar feedback humano o automatizado, decidir FIX, SKIP, NEEDS_USER_DECISION o STOP con YAGNI y alcance explícitos, seleccionar sólo las herramientas de verificación necesarias y detectar ciclos de reparación antes de seguir parchando."
---

# PR Blocker Convergence

Operar como triage y memoria acumulativa de la PR. Respetar primero el alcance pedido por el usuario, incluso dentro del orquestador.

## Alcance y permiso para LocalReviewer

- "Verificar blockers", "revisar si hay blockers" y pedidos equivalentes significan inspeccionar y clasificar el feedback existente. No editar producto, ejecutar pruebas, crear commits, pushear, responder o resolver comentarios ni iniciar LocalReviewer como parte de esa verificación.
- Reparar requiere autorización para resolver los blockers o continuar una entrega completa ya autorizada. Una petición posterior limitada a verificar prevalece sobre el flujo automático. Invocar esta skill después de pedir una verificación mantiene ese alcance; por sí solo no autoriza reparaciones.
- Con reparaciones autorizadas, validar cada cambio mediante las pruebas focales necesarias. Explicar si se están leyendo comentarios, ejecutando tests, comprobando UI o haciendo una revisión de código. No llamar "reviewer local" al agente que ejecuta tests.
- Antes de usar LocalReviewer, pedir permiso explícito indicando alcance, SHA, provider/modo y número máximo de ejecuciones. La aprobación del spec, la implementación, la PR, la convergencia o los tests no autoriza LocalReviewer.
- El permiso cubre una ejecución por defecto. Si el usuario autoriza un ciclo, registrar su alcance y límite explícito, y consumir sólo las ejecuciones autorizadas. No volver a pedir permiso para una ejecución ya autorizada ni extender el ciclo automáticamente. Una nueva ejecución fuera de ese permiso requiere otra aprobación.
- Si el usuario detiene o prohíbe LocalReviewer, conservar esa decisión. "Continúa", "corrige los blockers", un push o una skill de convergencia no la revocan. Sólo una autorización nueva que mencione expresamente LocalReviewer permite reanudarlo.
- Sin permiso, continuar el trabajo independiente autorizado y declarar la revisión local pendiente o cancelada. Un gate que exige review no autoriza a ejecutarla ni permite fabricar un recibo PASS.

Leer [ledger-memory.md](references/ledger-memory.md) sólo al inicializar, migrar o compactar la memoria. Leer [review-research.md](references/review-research.md) sólo cuando un blocker sea incierto, dependa de una práctica externa o entre en ciclo.

## Contrato automático

Aplicar este contrato sólo cuando las reparaciones y publicaciones estén autorizadas. En modo de verificación terminar tras la inspección y clasificación, conservando los blockers pendientes.

- Ejecutar hasta cuatro rondas de review. Terminar antes si `review-gate` queda limpio.
- Descubrir blockers nuevos en cada ronda; no limitarse a la lista inicial.
- Procesar primero todo trabajo autónomo seguro. Pausar sólo por `NEEDS_USER_DECISION`, `STOP`, falta de acceso o riesgo no autorizado.
- Hacer un solo push por ronda después de validar todos los `FIX` de esa ronda.
- Informar el estado completo al converger, al completar la cuarta ronda o al pausar antes.
- No ampliar el alcance para satisfacer al reviewer. Una observación no se vuelve válida por repetición.
- La review previa a la PR del orquestador se registra como ronda `00`, con el mismo ledger y los mismos `cycle-key`. Un blocker remoto que coincide con uno de la ronda `00` es una reaparición, no uno nuevo, y cuenta para la detección de ciclos.

## Memoria compacta

Usar una sola raíz de memoria por PR:

- Dentro del orquestador: `<run-root>/blocker-triage/`.
- Uso standalone: `<worktree>/.context/planning/<ISSUE-ID>-pr-blockers/`.
- Sin número de PR, reemplazar el ID por `branch-<branch-normalizada>`.

Mantener cuatro capas:

- `state.json`: estado operativo actual; cargarlo al comenzar cada ronda.
- `dashboard.md`: vista humana de blockers abiertos, decisiones y resumen; objetivo máximo 250 líneas.
- `index.jsonl`: índice compacto append-only por comentario y `cycle-key`; consultarlo con búsquedas exactas, nunca leerlo completo.
- `history/round-<NN>.json`: evidencia e historia completa de cada ronda; cargar sólo el archivo exacto al reabrir, auditar o investigar un ciclo.

No releer toda la historia. Al iniciar una ronda cargar únicamente `state.json`, `dashboard.md` y el snapshot nuevo. Para cada blocker nuevo, derivar su `cycle-key` y buscar coincidencias exactas en `index.jsonl`; abrir el evento histórico referenciado sólo si la coincidencia puede cambiar la decisión.

Mantener `state.json` orientado al presente: blockers abiertos, `NEEDS_USER_DECISION`, `STOP`, ciclos activos, contadores y referencias a decisiones pegajosas. Al cerrar un blocker, mover su detalle al evento de la ronda y conservar sólo su entrada mínima en el índice. Compactar el dashboard al superar 250 líneas; nunca perder evidencia del historial.

Considerar el mismo blocker cuando coincida el comentario o cuando pida el mismo cambio sobre el mismo flujo por la misma causa raíz. Incrementar `aparición`; no crear otro blocker. Relacionar como `successor-of` un error nuevo causado por el intento de reparar otro blocker.

## Procedencia del comentario

Clasificar por **marcadores del cuerpo**, nunca por el login del autor. Una cuenta de aspecto
humano puede publicar reviews automatizadas; tratarla como bot cuando el cuerpo lleve una firma
automatizada (`Codex Code Review`, `Claude Code Review`, `Task: <ISSUE>`, tags de coderabbit u
otra firma configurada en `review.automatedSignatures` del perfil). Un login que termina en
`[bot]`, `-bot` o `_bot`, o que figura en `review.botNames`, también es automatizado.

Sin ninguna de esas señales, el comentario es **humano**: no clasificarlo, no repararlo ni
resolverlo por cuenta propia. Detener el loop y entregarlo a una persona; el gate final bloquea
mientras quede feedback humano sin resolver, aunque todo lo automatizado esté convergido.

Mencionar un bot no convierte un comentario en automatizado. La firma debe abrir el cuerpo, no
aparecer citada dentro de una frase.

## Clasificación cerrada

Separar siempre `disposición`, `motivo` y `owner`.

### Disposición

- `FIX`: defecto válido, alcanzable, material y perteneciente al alcance actual.
- `SKIP`: no corresponde cambiar esta PR; exige un motivo público concreto.
- `NEEDS_USER_DECISION`: existe una decisión real de negocio, producto o arquitectura que el código y los acuerdos vigentes no resuelven.
- `STOP`: una cadena de reparación no converge tras dos iteraciones fallidas o continuar supera el límite/riesgo autorizado.

### Motivos de SKIP

Usar exactamente uno como motivo principal:

- `YAGNI`: capacidad, abstracción, compatibilidad o defensa futura sin requisito ni riesgo material actual.
- `OUT_OF_SCOPE`: cambio válido pero no requerido por la issue y no introducido ni agravado por la PR.
- `EXTERNAL_CHANGE`: exige modificar otro producto, servicio, contrato, migración o iniciativa fuera de la issue actual. No usarlo si ese cambio es indispensable para que la issue funcione con seguridad.
- `OVER_ENGINEERING`: la solución propuesta agrega capas, generalidad o superficie desproporcionada cuando el comportamiento actual ya es correcto o admite una solución local menor.
- `NOT_REPRODUCIBLE`: el escenario no es alcanzable bajo invariantes actuales o no existe evidencia suficiente tras una inspección razonable.
- `BASELINE_OR_COVERED`: problema preexistente no agravado, ya resuelto, duplicado o cubierto por tests/controles actuales.
- `PRIOR_DECISION`: reaparece un `SKIP` o una decisión del usuario ya registrada sin nueva causa o impacto material.

No usar `SKIP` para regresiones de la PR, requisitos vigentes, seguridad, pérdida/corrupción de datos ni fallos materiales. Si el feedback fuera de scope merece seguimiento, registrarlo como candidato a otra issue sin crearla automáticamente.

### Owner

- `MODEL`: cualquier modelo capaz puede resolverlo con el repositorio, las herramientas permitidas y los acuerdos actuales. La dificultad alta no lo convierte en HITL.
- `USER`: sólo negocio, arquitectura con alternativas incompatibles, cambio explícito de spec o autoridad externa.
- `NONE`: `SKIP` ya decidido o blocker resuelto.
- `JOINT`: `STOP`; el modelo entrega diagnóstico y alternativas, y el usuario decide si autoriza otra dirección.

## Evaluación

Para cada causa raíz:

1. Leer el diff completo, código relevante, tests, `AGENTS.md`, issue, spec, tickets, ADR y decisiones previas.
2. Reproducir o demostrar el mecanismo cuando sea razonable. No asumir que el reviewer tiene razón.
3. Evaluar validez, efecto observable, severidad, cambio mínimo, complejidad, riesgo de regresión y pertenencia al alcance `Now`.
4. Deduplicar antes de decidir. Un comentario con causas independientes se divide.
5. Aplicar la clasificación cerrada y registrar evidencia verificable.
6. Mantener pegajosos `SKIP` y decisiones del usuario. Reabrir sólo ante nueva causa, nuevo impacto material o instrucción explícita.

Si un bot contradice un acuerdo vigente y no existe decisión previa, usar `NEEDS_USER_DECISION`; citar ambos lados y formular una sola elección mínima. No editar mientras siga abierta.

## Detección de ciclos

Asignar un `cycle-key` estable a la causa raíz. Después del primer `FIX`, contar como iteración fallida cada ronda posterior que presente:

- la misma causa raíz;
- el mismo fallo reformulado o movido;
- una regresión causada por el parche anterior; o
- un nuevo blocker `successor-of` que conserva abierta la misma brecha de aceptación.

Al completar la segunda iteración fallida, cambiar inmediatamente a `STOP`. No intentar un tercer parche, no reconocer ni resolver el thread y no disfrazarlo como `SKIP`. Registrar la línea temporal de commits, errores y validaciones, y avisar al usuario con diagnóstico, riesgo, opciones y la decisión mínima necesaria.

No contar como ciclo un blocker independiente descubierto más tarde ni una falla heredada de la base.

## Selección de herramientas

Registrar para cada blocker `superficie`, `herramienta`, `requerida: sí/no` y `por qué`. Elegir la evidencia mínima que pueda falsar el riesgo:

- `STATIC`: lectura/diff; para reachability, contratos simples y alcance. Sin runtime.
- `TEST`: test unitario/integración/typecheck existente o enfocado; para lógica determinista.
- `API_COMMAND`: request o comando reproducible; para backend y flujos ensamblados sin UI.
- `LOCAL_RUNTIME`: sólo si el comportamiento depende de integración o estado ejecutándose.
- `BROWSER` o `CUA`: sólo si cambió o puede haber regresado comportamiento visual/interactivo que no cubre una evidencia menor.
- `PLAYWRIGHT`: sólo ante una brecha demostrada de capacidad del reviewer UI permitido, como upload o hover; no como reviewer alternativo general.

Durante PR y convergencia de blockers no se ejecuta QA ni revisión UI de forma automática: la evidencia de cada `FIX` son los tests focales y la paridad con CI. Si un `FIX` puede cambiar algo que un caso del QA Script o una historia UI observa, registrarlo en el ledger, nombrar esos casos y pedir al usuario que autorice una ronda; sólo con su autorización correr `qa-session ... --rerun <casos> --user-approved "<sus palabras>"`. Nunca repetir el QA completo por blockers salvo que el usuario lo pida. Si no necesita herramientas especiales, escribir `ninguna` explícitamente.

## Loop de hasta cuatro rondas

En cada ronda:

1. Obtener snapshot fresco de threads inline, comentarios generales accionables y Actions.
2. Leer sólo `state.json`, `dashboard.md` y el snapshot nuevo. Consultar `index.jsonl` por IDs o `cycle-key`; no cargar el historial completo.
3. Clasificar todo antes de editar. Aplicar `STOP` antes de planear otro parche.
4. Con autorización para reparar, resolver los `FIX/MODEL` por causa raíz mediante `issue-delivery-implement`, que delega a `test` cuando el plan declara `TEST`. Esto no autoriza una ejecución de LocalReviewer.
5. Publicar y reconocer `SKIP` sólo con su resumen público; reconocer `FIX` sólo después de validar y pushear.
6. Hacer un único push y esperar la siguiente ronda cuando hubo cambios.
7. Ejecutar `review-gate` cuando no queden `FIX`, `NEEDS_USER_DECISION` ni `STOP`.

Detener el loop al converger o tras la cuarta ronda. Si hay `NEEDS_USER_DECISION` o `STOP`, completar primero los blockers independientes que sean seguros y luego pausar sin consumir rondas artificiales. Nunca solicitar extensiones automáticas más allá de cuatro rondas.

## Informe

Entregar cantidades por disposición y distinguir nuevos versus repetidos. Para cada blocker abierto incluir:

```markdown
### <ID> — <título>
- Disposición / motivo / owner:
- Mecanismo e impacto:
- Alcance y evidencia:
- Apariciones e intentos fallidos:
- Verificación y herramientas: <requerida sí/no + razón>
- Siguiente acción:
- Motivo público: <obligatorio para SKIP>
```

Cerrar con ronda alcanzada, SHA, checks, blockers nuevos de la última ronda, herramientas realmente usadas, estado del gate y una sola pregunta concreta si se necesita al usuario.

## Aprendizaje controlado

Investigar en internet sólo cuando falte evidencia normativa/técnica, una tecnología sea desconocida o exista un ciclo. Priorizar documentación oficial, estándares y papers; registrar fecha, consulta, enlaces, conclusión y cómo cambió —o no— la decisión. La evidencia genérica externa nunca reemplaza el spec ni amplía el scope.

Al final, registrar en `Candidatos de mejora` patrones repetidos, falsos positivos y herramientas innecesarias. Proponer una edición pequeña y reusable a esta skill; no autoeditarla durante una PR ni convertir una experiencia aislada en regla sin aprobación explícita del usuario.

## Seguridad

- No commitear ni borrar la memoria o su historial.
- No responder, resolver ni reaccionar en GitHub durante triage standalone sin autorización.
- No marcar `SKIP` o `STOP` como resuelto silenciosamente.
- No usar investigación externa para enviar código o datos privados.
