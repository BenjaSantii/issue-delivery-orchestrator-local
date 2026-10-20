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
     [--stack-parent <branch>] cloud-launch [--agent claude|codex] [--model <id>]
   ```

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
      instala con `pnpm conductor:toolbox`) el plugin 0.12.0 o mayor y las credenciales, y
      responde `IDO-BOOTSTRAP READY` o `IDO-BOOTSTRAP FAILED: <causa>`;
   2. lee esa sesión desde aquí hasta encontrar la respuesta (`--wait-minutes`, 25 por defecto);
   3. con `READY`, crea en el mismo workspace la sesión **Entrega** con el mensaje del
      orquestador. Al ser un proceso nuevo del agente, arranca con las skills del toolbox ya
      cargadas; no hace falta abrir otro chat a mano.
   Lanzar varias issues en secuencia: cada comando espera su propia preparación.
5. Entregar `workspaceId` y la sesión de entrega. Ante `FAILED`, mostrar la causa; ante timeout,
   el comando imprime cómo reanudar: `cloud-launch --confirm --workspace <id>`, que retoma la
   espera y nunca crea una segunda sesión Entrega.
6. Si la sesión de preparación se queda esperando sin avanzar, revisar el comando que está
   corriendo. Un `pgrep -f <texto>` sin corchetes dentro de un loop se encuentra a sí mismo y nunca
   termina (ver "Esperas sobre procesos" en
   [conductor-cloud.md](../issue-delivery-orchestrator/references/conductor-cloud.md)). Cancelar
   ese turno con `conductor session cancel <sesión>` y enviar a la misma sesión la instrucción
   corregida con `conductor message create --session <sesión> --message-file -`. El lanzador sigue
   leyendo esa sesión y crea la Entrega cuando llegue `IDO-BOOTSTRAP READY`.
7. Para seguir un workspace: `conductor workspace status <id>` y
   `conductor workspace session <id>`; no enviar mensajes a sus sesiones sin que el usuario lo pida.

## Qué recibe el workspace

- `--branch <base o rama del padre>`, `--agent`, `--name "[ID] título"`.
- Entorno: `CODEX_TOOLBOX_PROFILE`, `LINEAR_EXPECTED_EMAIL`, `GITHUB_EXPECTED_LOGIN`. El setup del
  repositorio (`pnpm conductor:setup`) instala el toolbox con ellos.
- La sesión de preparación descrita arriba y, tras su `READY`, la sesión Entrega, que prepara la VM
  según [conductor-cloud.md](../issue-delivery-orchestrator/references/conductor-cloud.md) e inicia
  el orquestador con `--mode conductor`, indicando que el spec y los tickets ya fueron aprobados.

No lanzar issues sin spec y tickets aprobados: para esas, el siguiente paso es el Grill
(`issue-delivery-grill`) en una sesión interactiva.
