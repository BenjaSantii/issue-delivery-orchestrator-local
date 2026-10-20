# Slicing en PRs más chicas

Mantener una PR por issue. Proponer dividir sólo al superar el umbral de tamaño: tocar varias
apps o dueños y desviarse de la estimación no justifican dividir por sí solos. Dividir es
siempre una **decisión del usuario**, nunca automática; mantener una sola PR sigue siendo válido.

Umbrales del perfil (`slicing`): `maxProductLines` (4000), `estimateDeviation` (1.5) y
`maxStackDepth` (3). Sólo cuentan líneas de producto; tests, migraciones generadas, lockfiles,
snapshots de contrato y Markdown se informan aparte o se excluyen.
El tamaño suma adiciones y eliminaciones del delta de producto. Exactamente 4000 líneas no
disparan la propuesta; desde 4001 sí. La desviación de estimación nunca dispara una decisión
por debajo o en el umbral de tamaño.

## Mecanismo según dependencia

| Relación entre slices | Mecanismo | Cómo corre |
| --- | --- | --- |
| Un slice necesita código de otro (`Blocked by` entre tickets de slices distintos) | **Stack**: la PR del hijo apunta a la rama del padre | Secuencial. Profundidad máxima `maxStackDepth`; más allá, secuenciar con sub-issues que esperan el merge |
| Sin dependencias entre slices | **Sub-issues independientes**: cada una con su run y PR hacia la target | En paralelo, en workspaces separados |

El issue original conserva el slice más bajo (o uno independiente). Los demás slices son
sub-issues creadas con `create-sub-issue`:

El perfil TurboShop asigna las nuevas sub-issues de Sales Force al usuario de
Linear autenticado. Shipment y las demás apps quedan sin asignar. Una indicación
explícita del usuario tiene prioridad: pasar `--assignee <ID-de-usuario>`,
`--assignee me` o `--assignee none`. Determinar la app propietaria por el alcance
real. Si un slice pertenece a una app distinta de su padre, indicar su responsable
explícitamente; para una app distinta de Sales Force, usar `--assignee none`
salvo que el usuario haya pedido otra persona. Esta regla sólo afecta tareas nuevas.

- `--relation blocks-current`: el slice nuevo va *debajo* en el stack.
- `--relation blocked-by-current`: el slice nuevo va *encima* en el stack.
- `--relation none`: slice independiente.

Cada sub-issue necesita spec y tickets propios para correr el orquestador: publicar el spec
aprobado del issue original (mismo `Spec ID`), agregando en `Further Notes` qué historias y tickets
cubre ese slice, y un bloque de tickets sólo con los suyos. No reabrir el Grill por dividir.

## 1. Al planificar (ticket publisher)

Cada ticket declara `Estimated size`. Registrar la suma con
`issue-delivery <issue> size-estimate --lines <n>`. Sólo si la suma supera `maxProductLines`,
proponer slices al presentar el desglose: tickets
por slice, dependencias entre slices, mecanismo y por qué. El usuario puede rechazarlo; registrar
entonces `size-decision --decision single-pr --reason "<motivo del usuario>"`.

## 2. Durante Implement (control principal)

Después del commit de cada ticket ejecutar `issue-delivery <issue> size-check`. Con
`NEEDS_DECISION`, detener y preguntar al usuario con los números del resultado (líneas de
producto y de tests, estimación, archivos más grandes) y dos opciones:

- **Cortar aquí.** Lo hecho forma el slice inferior; lo pendiente continúa en otro slice según su
  dependencia (ver 3).
- **Seguir en un PR.** `size-decision --decision single-pr --reason "<motivo>"`. No se vuelve a
  preguntar hasta que el delta duplique ese tamaño.

## 3. Antes de la PR (red de seguridad) y cortes

`ensure-pr` rechaza abrir una PR nueva con `NEEDS_DECISION` sin decisión registrada. Los cortes
usan los commits por ticket; ningún comando reescribe código. Crear primero la sub-issue con
`create-sub-issue`: su resultado trae la rama que Linear le asignó, que es la que usa el corte.

**Stack** (tickets dependientes, en orden): crear la sub-issue inferior con
`--relation blocks-current`. Luego `stack-cut --sha <commit del último ticket del slice inferior>
--branch <rama de esa sub-issue>` crea y pushea esa rama sobre un commit existente. Este run queda
como slice superior: `stack-link --parent-branch <esa rama>`. La sub-issue inferior corre su propio
run adoptando esa rama, con su propia paridad con CI, review previa, evidencia y PR hacia la
target.

**Independientes**: crear la sub-issue con `--relation none` y ejecutar
`slice-extract --commit <sha> [--commit <sha>…] --branch <rama de la sub-issue>`, que aplica esos commits sobre la target en una rama nueva y los retira de este branch.
Sólo funciona antes del primer push. Si un commit no aplica solo, el comando aborta sin tocar
nada: los slices no eran independientes.

Cuándo **no** cortar y mantener un PR (registrar la decisión igual):

- Un commit mezcla trabajo de dos slices y partirlo cuesta más que revisarlo junto.
- Commits de Refactor cruzan los slices; asignarlos al slice inferior sólo si quedan coherentes.
- Un slice no tendría valor ni seguridad por sí solo (por ejemplo, un endpoint sin consumidor
  que igual obliga a revisar el contrato completo).

Después de un corte, la evidencia UI aceptada sigue valiendo para el slice que contiene esas
historias; un slice sin UI usa evidencia `TEST` o `API_COMMAND`.

## 4. Operar un stack

- Iniciar un run hijo con `--stack-parent <rama del padre>` (o `stack-link` si ya existe). Su
  delta, paridad con CI, review previa y PR se miden contra la rama del padre.
- No abrir la PR del hijo hasta que la del padre exista (el motor lo exige) y, salvo decisión del
  usuario, hasta que el padre haya convergido: cada fix del padre obliga a restackear al hijo y
  dispara otra corrida del reviewer.
- `restack` es la única reescritura permitida: rebasa el hijo sobre la rama actual del padre, o
  sobre la target cuando el padre ya fue mergeado (squash-merge), hace `push --force-with-lease`,
  cambia la base de la PR a la target y registra el HEAD anterior para deshacer. Ejecutarlo
  después de cada push de convergencia del padre y después de su merge. Ante conflicto aborta y
  deja la rama intacta: resolver con el usuario.
- Después de un `restack` con push, repetir `publish-validation` sobre el nuevo HEAD.
