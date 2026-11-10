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
  y en `~/.paradev/bin/superset`.
- El host destino en línea en `superset hosts list`, con el repositorio adoptado como proyecto, el
  plugin instalado en el agente que se lance y el setup del workspace configurado.
- La carpeta de worktrees del host marcada como confiable en el `.claude.json` de cada perfil
  (`projects["<home>/.paradev/worktrees"].hasTrustDialogAccepted`). Si no, el agente se detiene
  en el aviso de confianza de carpeta y no procesa el mensaje inicial.
- Opcional en el `.env` del orquestador: `ISSUE_DELIVERY_SUPERSET_HOST` (ID o nombre del host),
  `ISSUE_DELIVERY_SUPERSET_AGENT` (preset o UUID de la configuración de agente del host) y
  `ISSUE_DELIVERY_SUPERSET_MODEL` (por defecto `opus`).

## Flujo

1. Obtener el plan sin crear nada:

   ```bash
   python3 <plugin-root>/scripts/issue-delivery <issue> [--base <branch>] \
     [--stack-parent <branch>] [--implement-model <alias>] superset-launch \
     [--host <id-o-nombre>] [--agent <preset-o-uuid>] [--model <id>] \
     [--superset-project <id>]
   ```

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
   con `--mode superset` y da por aprobados el spec y los tickets publicados.
5. Informar el ID del workspace y su `webUrl`. El run sigue en ese host; las decisiones HITL y los
   permisos de LocalReviewer se responden en ese chat.

No lanzar issues sin spec y tickets aprobados: para esas, el siguiente paso es el Grill
(`issue-delivery-grill`) en una sesión interactiva.
