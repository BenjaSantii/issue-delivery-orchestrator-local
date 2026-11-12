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

Sigue el estilo de las páginas de Superset: un documento propio, sobrio y legible, que no parezca
generado.

- **Autocontenido.** Todo el CSS y el JS van dentro del archivo. Nada de `<script src>` ni hojas
  de estilo remotas, ni `fetch`, ni `eval`. Las imágenes van como SVG en línea o `data:`. Tiene
  que abrir igual con la red apagada.
- **Estructura.** Arriba, el código de la issue, el título y una línea que dice qué cambia. Debajo,
  pestañas por estado: siempre **Antes** y **Después**, y además los estados que el spec nombra
  (vacío, error, cargando, permiso denegado) cuando importan. Al lado o debajo de cada pantalla,
  una lista corta "Qué cambia" que apunta a lo nuevo.
- **Pantallas creíbles.** Para apps de teléfono, dibuja la pantalla dentro de un marco de
  390×844; para web, dentro de un marco de navegador al ancho real. Usa el copy en español del
  producto y datos falsos realistas (talleres, rutas, vendedores con nombres chilenos), nunca
  "Lorem ipsum".
- **Fiel a la app.** Antes de dibujar, lee los colores, la tipografía y los componentes de la app
  que cambia (su tema, tokens o Tailwind config) y úsalos. "Antes" debe parecerse a la pantalla real
  de hoy; si no la conoces bien, revisa el componente actual.
- **Diseño deliberado.** Una paleta, una escala tipográfica con contraste real entre título y
  texto, espacio en blanco para agrupar en vez de bordes en todo. Evita degradados morado-azul,
  todo centrado, esquinas de píldora en cada elemento y emojis como íconos.
- **Interacción mínima.** Lo justo para entender el flujo: cambiar de pestaña, abrir un modal,
  marcar un ítem. Sin lógica de negocio.
- **Cada estado tiene su ancla.** `#antes`, `#despues` y `#<estado>` abren esa pestaña directo.
  Lo usas para las capturas.
- Define tus colores como variables en `:root` y dale a `body` un fondo explícito.

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

1. Captura tú esas mismas pantallas desde el servidor del prototipo, una PNG por estado aprobado,
   en el directorio del prototipo (`antes.png`, `despues.png`, `<estado>.png`). Con el Playwright
   del repo:

   ```bash
   node -e '
   const { chromium } = require("playwright");
   (async () => {
     const [url, dir, ...states] = process.argv.slice(1);
     const browser = await chromium.launch();
     const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
     for (const state of states) {
       await page.goto(`${url}#${state}`);
       await page.waitForTimeout(300);
       await page.screenshot({ path: `${dir}/${state}.png`, fullPage: true });
     }
     await browser.close();
   })();' "<proxyUrl de prototype-serve>" "<directorio del prototipo>" antes despues
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
