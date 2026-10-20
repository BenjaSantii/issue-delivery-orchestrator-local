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
4. Con la confirmación, repetir el comando con `--confirm` y entregar el enlace (`deepLink`) o el ID
   que devuelve Conductor para cada workspace.
5. Para seguir un workspace: `conductor workspace status <id>` y
   `conductor workspace session <id>`; no enviar mensajes a sus sesiones sin que el usuario lo pida.

## Qué recibe el workspace

- `--branch <base o rama del padre>`, `--agent`, `--name "[ID] título"`.
- Entorno: `CODEX_TOOLBOX_PROFILE`, `LINEAR_EXPECTED_EMAIL`, `GITHUB_EXPECTED_LOGIN`. El setup del
  repositorio (`pnpm conductor:setup`) instala el toolbox con ellos.
- Un primer mensaje que verifica el plugin (o lo instala con `pnpm conductor:toolbox`), prepara la
  VM según
  [conductor-cloud.md](../issue-delivery-orchestrator/references/conductor-cloud.md) e inicia el
  orquestador con `--mode conductor`, indicando que el spec y los tickets ya fueron aprobados.

No lanzar issues sin spec y tickets aprobados: para esas, el siguiente paso es el Grill
(`issue-delivery-grill`) en una sesión interactiva.
