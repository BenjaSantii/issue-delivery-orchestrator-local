---
name: issue-delivery-superset-launch
description: "Iniciar en un host remoto de Superset (por ejemplo una VPS) una issue de Linear que ya tiene spec y tickets aprobados: verificar que esté lista y no bloqueada, y crear en ese host un workspace sobre la rama de Linear cuyo agente arranca Issue Delivery Orchestrator en modo superset sin repetir el Grill. Usar desde la máquina local cuando el usuario pida mandar, lanzar o iniciar una issue en la VPS, en otro host o en Superset después del Grill."
---

# Lanzar una issue en un host de Superset

Se ejecuta en la máquina del usuario, normalmente justo después del Grill. El Grill publica el spec
y los tickets en Linear; el host remoto los lee de ahí y sigue con Implement → PR en modo
`superset`. En un host sin pantalla el motor fija el reviewer `playwright`.

## Requisitos

- CLI de Superset autenticada (`superset auth whoami`). Se busca en `SUPERSET_CLI`, en el `PATH`
  en `~/.paradev/bin/superset` y en `~/superset/bin/superset` (instalación manual en Linux).
- El host destino en línea en `superset hosts list`, con el repositorio adoptado como proyecto, el
  plugin instalado en el agente que se lance y el setup del workspace configurado.
- Opcional en el `.env` del orquestador: `ISSUE_DELIVERY_SUPERSET_HOST` (ID o nombre del host),
  `ISSUE_DELIVERY_SUPERSET_AGENT` (preset, UUID o etiqueta de la configuración de agente del
  host), `ISSUE_DELIVERY_SUPERSET_MODEL` (por defecto `opus`) e `ISSUE_DELIVERY_SUPERSET_EFFORT`.

## Flujo

1. Obtener el plan sin crear nada:

   ```bash
   python3 <plugin-root>/scripts/issue-delivery <issue> [--base <branch>] \
     [--stack-parent <branch>] [--implement-model <alias>] \
     [--qa-agent <agente>] [--qa-model <modelo> [--qa-effort <esfuerzo>]] superset-launch \
     [--host <id-o-nombre>] [--agent <agente>] [--model <modelo>] [--effort <esfuerzo>] \
     [--superset-project <id>]
   ```

   `<agente>` es un preset (`claude`), el UUID de una configuración del host o su etiqueta, como
   `Claude dev4`. Los modelos son `haiku`, `sonnet`, `opus`, `fable` o un ID `claude-*`; el
   comando rechaza otro valor antes de crear nada, porque Superset no lo valida. El QA del run
   corre en otra terminal del mismo workspace: sin `--qa-agent` usa el agente del Generador, y sin
   `--qa-model` el `QA Model` del spec o `sonnet` con esfuerzo `high`.

   Bloquea si la issue está cerrada, no tiene bloque de spec (`Spec ID`) o de tickets
   (`Ticket ID`), tiene un bloqueador abierto, el host está fuera de línea o ya existe un workspace
   activo sobre la rama de la issue en ese host. Un bloqueador que es la issue padre se trata como
   stack, igual que en `issue-delivery-cloud-launch`.
2. Revisar `workspaceBaseBehindPrTarget`. Si la base va detrás de la rama objetivo y el spec
   depende de trabajo que sólo está ahí, proponer `--base <prTarget>`.
3. Mostrar al usuario el spec y los tickets detectados, el host, la base, el agente, el modelo y
   el mensaje inicial. Pedir confirmación explícita.
4. Con la confirmación, repetir el comando con `--confirm`. Crea el workspace `[<issue>] <título>`
   sobre la rama de Linear y arranca el agente con el mensaje inicial, que inicia el orquestador
   con `--mode superset`, pasa la configuración del QA y da por aprobados el spec y los tickets.
   Claude Code pide confiar en cada worktree nuevo y no lee el mensaje hasta que alguien
   responde: el comando detecta ese aviso en la terminal, lo acepta y espera a que el agente
   empiece a trabajar.
5. Informar el ID del workspace y su `webUrl`. El run sigue en ese host; las decisiones HITL y los
   permisos de LocalReviewer se responden en ese chat.

No lanzar issues sin spec y tickets aprobados: para esas, el siguiente paso es el Grill
(`issue-delivery-grill`) en una sesión interactiva.
