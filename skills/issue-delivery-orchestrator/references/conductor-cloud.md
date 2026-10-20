# Conductor Cloud

Aplica a runs en modo `conductor`: un workspace de Conductor Cloud (`CONDUCTOR_IS_LOCAL=0`),
normalmente creado con `issue-delivery-cloud-launch`. La VM es Linux, sin escritorio de macOS ni
Docker, así que el reviewer fijo es `playwright` (Chromium headless dentro de la VM).

## Preparar el workspace

Ejecutar en orden desde la raíz del checkout y bloquear con la causa concreta ante el primer fallo.
No imprimir valores de secretos; comprobar sólo su presencia.

1. **Plugin.** Confirmar que `issue-delivery-orchestrator-local` esté instalado y habilitado
   (`claude plugin list` o `codex plugin list`). Si falta, ejecutar
   `CODEX_TOOLBOX_PROFILE=<perfil> pnpm conductor:toolbox` con el perfil indicado en el mensaje
   inicial. Si lo instalaste en esta sesión, sus skills no están cargadas: leer los `SKILL.md`
   desde el directorio instalado y usarlo como `<plugin-root>`.
2. **Identidad y credenciales.** Exigir `LINEAR_API_KEY`, `LINEAR_EXPECTED_EMAIL` y
   `GITHUB_EXPECTED_LOGIN` en el entorno (`test -n "$LINEAR_API_KEY"`). La key vive en las
   variables de entorno del proyecto en Conductor; si falta, pedir al usuario que la configure ahí y
   abra un workspace nuevo. Nunca pedirla por el chat.
3. **Configuración.** `python3 <plugin-root>/scripts/issue-delivery config` debe resolver el
   repositorio a este checkout y las identidades esperadas.
4. **GitHub.** `gh auth status` debe estar autenticado con `GITHUB_EXPECTED_LOGIN`. Si no, pedir al
   usuario que ejecute `gh auth login` en una terminal del workspace que él mismo abra; no dejar un
   proceso esperando credenciales en una terminal del agente.
5. **Servicios.** `pnpm cloud:services:ensure` (Postgres y Redis) antes de cualquier
   `pnpm local-runtime ensure-services`. Tras un cambio de `package.json`, ejecutar
   `corepack pnpm install --frozen-lockfile`.
6. **Navegador.** Ejecutar
   `node <plugin-root>/scripts/playwright-review/preflight.mjs --worktree <checkout>`. Si Chromium
   falta, instalarlo con `corepack pnpm exec playwright install chromium` y repetir. En esta VM
   desechable el reviewer sí puede instalar Chromium.

## Iniciar el run

```bash
python3 <plugin-root>/scripts/issue-delivery <issue> \
  --worktree "$(git rev-parse --show-toplevel)" --mode conductor \
  [--base <branch>] [--stack-parent <rama-del-slice-padre>]
```

Conductor crea su propia rama de trabajo desde la base elegida. El modo `conductor` acepta esa rama
inicial siempre que su historia esté contenida en la base o en la rama de la issue, y luego conecta
la rama de Linear. Publicar `modeDecision` en el chat como en cualquier run.

## Spec y tickets aprobados

Cuando el mensaje inicial declara que el usuario ya aprobó el spec y los tickets publicados,
reconciliar esos bloques con el repositorio en la fase Grill. Si no hay diferencias de alcance,
registrar esa aprobación y completar el checkpoint de `grill` sin repetir la entrevista. Si la
reconciliación cambia el alcance, pedir aprobación explícita en el chat antes de Implement.

## Revisión manual

Invocar `issue-delivery-playwright-review` para toda historia `UI`. Las historias en viewport de
teléfono, con teclado o gestos táctiles, se delegan además al agente `mobile-web-reviewer`, o a la
skill `issue-delivery-mobile-web-review` en hosts sin agentes. `launch-browser` no aplica en este
modo. Las capturas y recibos siguen el mismo contrato de evidencia, con `provider: playwright`.

## Límites

- Una historia que sólo puede verificarse en un dispositivo nativo (teclado nativo, permisos,
  cámara, notificaciones) se devuelve `BLOCKED` con la causa; no se aprueba por inferencia desde
  web.
- El preview público de Conductor (`conductor preview set`) no es necesario: Playwright corre en la
  misma VM que el Local Runtime.
