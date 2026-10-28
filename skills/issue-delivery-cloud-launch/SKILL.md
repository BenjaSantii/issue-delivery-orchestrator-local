---
name: issue-delivery-cloud-launch
description: "Iniciar en Conductor Cloud una o más issues de Linear que ya tienen spec y tickets aprobados: verificar que estén listas y no bloqueadas, y crear un workspace cloud con las skills del toolbox y un primer mensaje que arranca Issue Delivery Orchestrator en modo conductor. Usar desde la máquina local cuando el usuario pida mandar, lanzar o iniciar issues en Conductor o en la nube."
---

# Lanzar issues en Conductor Cloud

Se ejecuta en la máquina del usuario, no dentro de un workspace cloud. Cada workspace creado corre
el run completo (Implement → PR) en una VM de Conductor con el reviewer `playwright`.

## Requisitos

- CLI de Conductor autenticada (`conductor auth status`). Se busca en `CONDUCTOR_CLI`, en el `PATH`
  y en `/Applications/Conductor.app/Contents/Resources/bin/conductor`.
- `LINEAR_EXPECTED_EMAIL` y `GITHUB_EXPECTED_LOGIN` configurados para el orquestador. Se envían al
  workspace porque el instalador del toolbox los exige.
- `ISSUE_DELIVERY_TOOLBOX_PROFILE` (por ejemplo `benjasanti`) o `--toolbox-profile`: el perfil de
  `software-skills` que instala este plugin en la VM.
- `LINEAR_API_KEY` configurada en las variables de entorno del proyecto en Conductor. El
  lanzador nunca la envía ni la lee.

## Flujo

1. Para cada issue pedida, obtener el plan sin crear nada:

   ```bash
   python3 <plugin-root>/scripts/issue-delivery <issue> [--base <branch>] \
     [--stack-parent <branch>] [--implement-model <alias>] \
     [--qa-model <id> [--qa-effort <esfuerzo>]] cloud-launch \
     [--agent claude|codex] [--model <id>]
   ```

   Sin `--model`, Claude usa `opus-5-5-1m` y Codex `gpt-6.1-sol`. Las sesiones siempre llevan un
   modelo explícito porque, sin modelo de QA, el QA hereda el del chat que lo llama.

   El modelo del chat de QA se elige en este orden: `--qa-model` del lanzamiento, las líneas
   `QA Model:`/`QA Effort:` del spec (recomendación aprobada en el grill) y, con Claude, el
   default `sonnet-5-5-1m` con esfuerzo `high` (`medium` si el spec dice `Lane: small`). Con
   Codex, sin flag ni spec, el QA hereda. El plan muestra `qa` con su `source`; el primer mensaje
   pasa `--qa-model`/`--qa-effort` al run.

   Cada ticket lo implementa un worker nuevo que hereda el modelo del chat principal, salvo
   `--implement-model` (`sonnet`, `opus`, `haiku` o `fable` en Claude). El plan lo muestra como
   `implementModel` y, si se indicó, el primer mensaje lo pasa al run.

   El comando bloquea si la issue está cerrada, no tiene bloque de spec (`Spec ID`) o de tickets
   (`Ticket ID`), o tiene un bloqueador abierto. Un bloqueador que además es su issue padre se
   trata como stack: el workspace parte de la rama del padre y el run usa `--stack-parent`; esa
   rama debe existir en origin.
2. Revisar `workspaceBaseBehindPrTarget`. Si la base va detrás de la rama objetivo de las PRs y el
   spec depende de trabajo que sólo está en esa rama, proponer `--base <prTarget>`.
3. Mostrar al usuario, por issue: spec y tickets detectados, base o stack, agente y el mensaje
   inicial. Pedir confirmación explícita: cada workspace cloud consume recursos y arranca un
   agente que trabajará la issue hasta abrir su PR.
4. Con la confirmación, repetir el comando con `--confirm`. El comando:
   1. crea el workspace con una **sesión de preparación** que espera el setup, verifica (o
      instala con `pnpm conductor:toolbox`) el plugin 0.12.1 o mayor y las credenciales, y
      responde `IDO-BOOTSTRAP READY` o `IDO-BOOTSTRAP FAILED: <causa>`;
   2. lee esa sesión desde aquí hasta encontrar la respuesta (`--wait-minutes`, 25 por defecto);
   3. con `READY`, crea en el mismo workspace la sesión **Generador** con el mensaje del
      orquestador. Al ser un proceso nuevo del agente, arranca con las skills del toolbox ya
      cargadas; no hace falta abrir otro chat a mano.
   Lanzar varias issues en secuencia: cada comando espera su propia preparación.
5. Entregar `workspaceId` y la sesión de entrega. Ante `FAILED`, mostrar la causa; ante timeout,
   el comando imprime cómo reanudar: `cloud-launch --confirm --workspace <id>`, que retoma la
   espera y nunca crea una segunda sesión Generador. Reconoce también el nombre anterior
   `Entrega` al reanudar para no duplicar sesiones existentes.
6. Si la sesión de preparación se queda esperando sin avanzar, revisar el comando que está
   corriendo. Un `pgrep -f <texto>` sin corchetes dentro de un loop se encuentra a sí mismo y nunca
   termina (ver "Esperas sobre procesos" en
   [conductor-cloud.md](../issue-delivery-orchestrator/references/conductor-cloud.md)). Cancelar
   ese turno con `conductor session cancel <sesión>` y enviar a la misma sesión la instrucción
   corregida con `conductor message create --session <sesión> --message-file -`. El lanzador sigue
   leyendo esa sesión y crea Generador cuando llegue `IDO-BOOTSTRAP READY`.
7. Para seguir un workspace: `conductor workspace status <id>` y
   `conductor workspace session <id>`; no enviar mensajes a sus sesiones sin que el usuario lo pida.

## Retomar chats detenidos por límite de uso

El límite de uso es de la cuenta: cuando se agota, el Generador y su QA se detienen juntos y
ninguno puede retomar al otro. Los retoma un agente externo desde la máquina local:

```bash
python3 <plugin-root>/scripts/issue-delivery all cloud-recover            # sólo el plan
python3 <plugin-root>/scripts/issue-delivery LOG-376 cloud-recover --confirm  # envía el mensaje
```

Revisa el último chat que implementa y el último QA de cada workspace activo (`all`) o de una
issue. Detecta el corte con el último evento de límite de la conversación (ventana de 5 horas o
semanal al 98 % o más, o rechazada) y, como respaldo, con el aviso de límite. El plan muestra el
uso de cada ventana y su hora de reinicio: si no cambiaste de cuenta y la ventana aún no se
reinicia, retomar ahora vuelve a cortarse. Si alguno quedó cortado, envía un mensaje al chat que implementa:
revisar el runtime, retomar el QA con `qa-resume` y volver a `qa-wait`. No escribe a un workspace
cuyo chat que implementa sigue trabajando. Ejecutarlo cuando el límite ya esté disponible (cambio
de cuenta o reinicio); el plan muestra la hora de reinicio que dio cada chat. Lee las
conversaciones completas, así que con muchos workspaces tarda unos minutos.

## Reanudar una sesión que murió

Si la sesión Entrega (u otra del run) no puede seguir en su chat (por ejemplo su contexto ya es
demasiado largo), retomar el run en el mismo workspace con una sesión nueva:

```bash
python3 <plugin-root>/scripts/issue-delivery <issue> cloud-resume            # sólo el plan
python3 <plugin-root>/scripts/issue-delivery <issue> cloud-resume --confirm  # crea la sesión
```

Busca el workspace `[<issue>] …`, bloquea si alguna de sus sesiones sigue `working` (para que
dos agentes no compartan el worktree) y crea `Reanudación <n>` con `claude` / `opus-5-5-1m` por
defecto (`--agent`, `--model` y `--workspace` lo cambian). El mensaje retoma el run existente sin
`--new-run` desde `nextAction`, conserva cambios sin commit y vuelve a pedir permisos como
LocalReviewer.

## Qué recibe el workspace

- `--branch <base o rama del padre>`, `--agent`, `--name "[ID] título"`.
- Entorno: `CODEX_TOOLBOX_PROFILE`, `LINEAR_EXPECTED_EMAIL`, `GITHUB_EXPECTED_LOGIN`. El setup del
  repositorio (`pnpm conductor:setup`) instala el toolbox con ellos.
- La sesión de preparación descrita arriba y, tras su `READY`, la sesión Generador, que prepara la VM
  según [conductor-cloud.md](../issue-delivery-orchestrator/references/conductor-cloud.md) e inicia
  el orquestador con `--mode conductor`, indicando que el spec y los tickets ya fueron aprobados.

No lanzar issues sin spec y tickets aprobados: para esas, el siguiente paso es el Grill
(`issue-delivery-grill`) en una sesión interactiva.
