---
name: issue-delivery-prototype
description: Armar, mostrar y hacer aprobar un prototipo HTML de una issue después del spec y antes de los tickets, cuando el spec marca `Prototype: yes` o el usuario lo pide. El prototipo es una sola página autocontenida que simula la función y muestra el antes y el después; se sirve como servicio de Paseo, se abre en una pestaña del workspace, y el HTML aprobado y sus pantallas quedan anclados en Linear. Usar desde el Grill o el orquestador de Issue Delivery Orchestrator; el usuario puede saltarlo.
---

# Prototipo de la issue

El prototipo responde una sola pregunta antes de escribir código: "¿esto es lo que quieres ver?".
No es código de producto ni una maqueta pixel-perfect. Es un HTML que simula la función con datos
falsos, lo bastante fiel como para aprobar o corregir el diseño.

## Cuándo corre

- **Sí:** el spec trae `Prototype: yes` porque hay un cambio visual importante (pantalla nueva,
  cambio de layout, flujo nuevo o reordenado, un estado nuevo que el usuario ve), o el usuario lo
  pide en cualquier momento antes de los tickets.
- **No:** cambios de backend, copy, colores puntuales o arreglos que no cambian qué ve el usuario.
- **Saltar:** si el usuario dice que lo salta, no insistas. Sigue con
  `issue-delivery-ticket-publisher` y menciona en una línea que no hay prototipo.

Va después de publicar el spec y antes de armar los tickets. Si el prototipo cambia una decisión del
spec, actualiza el spec con `issue-delivery-spec-publisher` antes de seguir.

## Dónde vive

Escribe una sola página en
`<worktree>/.local-runtime/issue-delivery-orchestrator/prototype/<ISSUE>/index.html`. Es un
directorio ignorado por git: el prototipo nunca entra en la PR.

## Cómo se ve

Sigue el estilo de las páginas de Superset: la pantalla dentro de su marco, nunca a pantalla
completa, y al lado un panel con lo que cambia.

**Parte siempre de [template.html](references/template.html).** Cópiala al directorio del
prototipo y rellena las pantallas y las notas; no cambies su estructura ni su estilo.

- **Plataformas.** Cada pantalla es una `.view` con `data-platform` (`movil` o `web`) y
  `data-state` (`antes`, `despues` o un estado del spec, como `vacio` o `error`). Deja sólo las
  plataformas que la issue toca. Con las dos, arriba aparece el selector **Teléfono / Web**.
  - Teléfono: marco de celular de 390×844 con las notas a la derecha.
  - Web: marco de navegador grande con las notas al lado; en ventanas angostas pasan abajo.
- **Estados.** Siempre **Antes** y **Después**, que se cambian con botones. Agrega los estados que
  el spec nombra (vacío, error, cargando, permiso denegado) cuando importan.
- **Notas.** En **Antes**, una línea con lo que ve hoy el usuario y dónde está el problema. En
  **Después**, la lista "Qué cambia": cada cambio lleva `data-mark="<n>"` en la pantalla y su nota
  con el mismo número, en una frase.
- **Copia la pantalla del código actual, no la inventes.** El HTML no puede usar los componentes
  reales, así que los redibujas desde el código:
  - **Antes:** lee el componente que la issue cambia y sus hijos, y reproduce su estructura, orden,
    textos, íconos, colores, tipografía y espaciados. Saca los valores del tema de la app (tokens,
    `theme`, Tailwind config), no a ojo.
  - **Después:** parte de esa misma pantalla y cambia sólo lo que dice el spec.
  - Si algo de la pantalla real no lo encuentras en el código, dibújalo lo más neutro posible y
    anótalo como supuesto en las notas. Nunca agregues elementos que la app no tiene.
- **Datos falsos creíbles.** Copy en español del producto y datos realistas (talleres, rutas,
  vendedores con nombres chilenos), nunca "Lorem ipsum".
- **Autocontenido.** Todo el CSS y el JS van dentro del archivo. Nada de `<script src>` ni hojas
  de estilo remotas, ni `fetch`, ni `eval`. Las imágenes van como SVG en línea o `data:`. Tiene
  que abrir igual con la red apagada.
- **Interacción mínima.** Lo justo para entender el flujo: abrir un modal, marcar un ítem. Sin
  lógica de negocio.
- **Anclas para capturar.** `#<plataforma>-<estado>` abre esa vista directo, por ejemplo
  `#movil-despues` o `#web-antes`.

## Mostrarlo

1. Sírvelo como servicio de Paseo:

   ```bash
   python3 <plugin-root>/scripts/issue-delivery <issue> prototype-serve
   ```

   Devuelve `url`. Ábrela para el usuario con la herramienta `browser_new_tab` de Paseo y dile que
   el prototipo está en una pestaña del workspace. Dale también la URL por si la quiere abrir en
   otro navegador. Si `browser_new_tab` no está disponible, pídele que active **Browser tools** en
   Settings → su host → Agents.
2. Con cada cambio que pida, edita el mismo `index.html` y recarga la pestaña con
   `browser_reload`. No crees páginas nuevas por versión.
3. Fuera de Paseo, `prototype-serve` se bloquea: entrega la ruta del archivo y pídele al usuario que
   lo abra.

## Aprobarlo

El usuario aprueba mandando por el chat capturas de las pantallas que acepta, o diciéndolo. Paseo no
guarda en disco las imágenes que te manda, así que no las subas: úsalas para saber qué pantallas
aprobó.

1. Captura tú esas mismas pantallas desde el servidor del prototipo, una PNG por vista aprobada,
   en el directorio del prototipo (`movil-antes.png`, `movil-despues.png`, `web-despues.png`...).
   Con el Playwright del repo:

   ```bash
   node -e '
   const { chromium } = require("playwright");
   (async () => {
     const [url, dir, ...views] = process.argv.slice(1);
     const browser = await chromium.launch();
     const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
     for (const view of views) {
       await page.goto(`${url}#${view}`);
       await page.waitForTimeout(300);
       await page.screenshot({ path: `${dir}/${view}.png`, fullPage: true });
     }
     await browser.close();
   })();' "<proxyUrl de prototype-serve>" "<directorio del prototipo>" movil-antes movil-despues
   ```

   Usa `proxyUrl` y no `url`: Playwright corre en el host del daemon.
2. Muéstrale al usuario las capturas y confirma que son las pantallas que aprobó.
3. Ancla la aprobación en Linear con sus palabras:

   ```bash
   python3 <plugin-root>/scripts/issue-delivery <issue> prototype-publish --approval "<palabras del usuario>"
   ```

   Sube al bloque `prototype` de la issue las PNG del directorio, el HTML como archivo para descargar,
   su SHA-256 y la aprobación. Linear muestra las capturas pero no el HTML. Si después cambias el
   HTML, la aprobación deja de valer: vuelve a mostrarlo y a publicarlo.

## Después

Sigue con `issue-delivery-ticket-publisher`. Los tickets de UI nombran `PROTO-<ISSUE>` como
referencia visual. Implement, la revisión manual y el QA comparan la UI contra las pantallas del
bloque `prototype`: una diferencia con el prototipo aprobado es un hallazgo, salvo que el usuario
haya cambiado de opinión después.
