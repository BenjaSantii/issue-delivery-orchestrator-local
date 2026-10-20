---
name: issue-delivery-mobile-web-review
description: "Verificar historias de teléfono en web con Playwright: viewports de celular con toque real, teclado en pantalla que desplaza o tapa contenido y gestos táctiles fluidos (arrastrar, deslizar, scroll). Usar desde mobile-web-reviewer o issue-delivery-playwright-review en runs de Issue Delivery Orchestrator; nunca editar código."
---

# Mobile Web Revision

Actuar como verificador de UI móvil, no como implementador. Probar la app web en dimensiones de
teléfono con eventos táctiles reales y devolver evidencia reproducible.

## Alcance y autoridad

- En modo `conductor` los resultados son evidencia del run (`provider: playwright`).
- En `superset`, `vanilla` o `codex` son diagnóstico adicional: un `FAIL` es un finding válido, pero
  el PASS de la historia sigue saliendo del reviewer fijado por el modo.
- En modo `conductor`, la validación móvil es SIEMPRE simulación web. No solicitar teléfono,
  ADB, dispositivo físico ni emulador nativo como requisito de cierre. Usar los perfiles de
  navegador, teclado simulado y gestos de esta skill, también si el producto es una app Expo.
- Web no reproduce el sistema operativo ni certifica teclado, permisos, GPS de fondo, hápticos
  o rendimiento nativos. Registrar esas limitaciones como observaciones informativas
  (`nativeValidation: not-executed`), sin convertirlas en `BLOCKED`/`NATIVE-ONLY` en Conductor.
  Verificar reglas y callbacks mediante simulación y tests focalizados cuando corresponda;
  no afirmar que se probó el comportamiento real del SO.
- Fuera de Conductor, ADB sólo se usa si el usuario pide validación nativa y autoriza el acceso,
  según [native-adb-evidence.md](../issue-delivery-orchestrator/references/native-adb-evidence.md).
  La falta de un dispositivo no bloquea una historia acordada como simulación web.

## Preflight

1. Exigir runtime ID, URL web de la app, historias, resultados esperados, worktree, run ID y SHA.
2. Ejecutar `node <plugin-root>/scripts/playwright-review/preflight.mjs --worktree <checkout>
   --url <url> --phone android-small`. Con `BLOCKED`, devolver `BLOCKED`.
3. Escribir los scripts en
   `.local-runtime/issue-delivery-orchestrator/<run-id>/validation/mobile/<story>.mjs` y las
   capturas en `validation/ui/`.
4. En TurboShop, el vendedor puede probarse en Expo web con el mecanismo existente
   `EXPO_PUBLIC_E2E_WEB_AUTH=true`, sólo en el proceso del runtime local/E2E. Inspeccionar
   `apps/sales-force-app/src/utils/web-e2e-auth.ts`, `domain/services/web-session.ts` y el launcher
   `scripts/sales-force-proposals-runtime.js` antes de declararlo inaccesible por rol. El launcher
   separa vendedor (`true`) y manager web (`false`) en puertos del mismo runtime. El flag debe
   estar presente antes de compilar el bundle; si falta, reiniciar únicamente ese Expo con su
   configuración de QA. No cambiar guards, roles ni builds de producción para habilitar la prueba.

## Dispositivos

Probar cada historia al menos en estos dos perfiles de `mobile.mjs`, más los que pida el spec:

| Perfil | Viewport | Plataforma simulada |
| -- | -- | -- |
| `android-small` | 360×800 | Android: el teclado achica el viewport |
| `iphone` | 390×844 | iOS: el teclado tapa la parte inferior |

Si el spec nombra otros tamaños (por ejemplo 800 dp de tablet), agregarlos con un perfil propio en
el script, sin cambiar los anteriores.

## Qué verificar

### Layout en teléfono

- Sin scroll horizontal del documento (`scrollWidth <= innerWidth`), textos sin cortar y
  controles táctiles de al menos 44×44 px.
- Revisar el screenshot de cada estado, no sólo los selectores.

### Teclado

Para cada campo de texto de la historia:

1. Tocarlo con `locator.tap()` y abrir el teclado con `showKeyboard(page, phone)`.
2. `inspectFocused(page, keyboard)`: `visible` debe ser `true` y `coveredByOther` `false`. En
   `android-small` la app debe reacomodarse sola sobre el teclado. En `iphone`, Safari desplaza la
   vista sobre el teclado, así que un campo detrás de él sigue siendo usable en web; ese caso trae
   `nativeRisk: true`.
3. `inspectReachable(page, accionPrincipal, keyboard)`: el botón principal (guardar, enviar,
   continuar) debe ser alcanzable con el teclado abierto. `how` indica cómo: `visible`, `scroll`
   (con swipes del dedo; nunca scroll programático) o `pan` (sólo iOS, desplazando la vista).
   `hidden` es `FAIL`.
4. `nativeRisk: true` es una limitación de extrapolar la simulación a nativo, no un FAIL ni un
   bloqueo de web. Reportarlo como observación con la captura y la posición del elemento;
   no inferir que la app nativa falla ni pedir un teléfono para convertirlo en PASS.
5. Escribir con `locator.pressSequentially()` y confirmar que el texto y el layout no saltan;
   luego `hideKeyboard(page, phone)` y confirmar que el layout vuelve al estado inicial.
6. Capturar un screenshot con el teclado abierto para cada campo crítico. El teclado no se dibuja:
   indicar en el caption dónde empieza (`keyboardTop`).

### Gestos táctiles

Para cada elemento arrastrable, deslizable o lista con scroll:

1. `touchDrag(page, { from, to, track })` para arrastres: el elemento debe moverse y seguir al dedo
   (`tracking.medianLagPx <= 24`), sin tareas largas de 100 ms o más.
2. `touchSwipe(page, { from, to, scroller })` para scroll y swipes: el contenedor debe desplazarse
   en la dirección esperada.
3. Repetir cada gesto 3 veces desde la misma precondición. Reportar la mediana. Si sólo una de tres
   corridas sale `janky`, informar `inconclusive` con las métricas, no `FAIL`.
4. `verdict: janky` en la mayoría de corridas es `FAIL` con las métricas (`frames.p95Ms`,
   `longTasks.maxMs`, `tracking`). `inconclusive` por pocos frames muestreados no es PASS: repetir
   con `durationMs` mayor o declarar la limitación.
5. Confirmar el estado final con un screenshot (por ejemplo la parada reordenada o el pin movido) y
   que el cambio persista tras recargar cuando la historia lo exige.

## Ejemplo

```js
import { openPhone, showKeyboard, hideKeyboard, inspectFocused, inspectReachable, touchDrag }
  from '<plugin-root>/scripts/playwright-review/mobile.mjs';

const { browser, page, phone, consoleErrors } =
  await openPhone({ worktree: '<checkout>', phone: 'android-small', url: '<url>' });
await page.getByPlaceholder('Notas').tap();
const keyboard = await showKeyboard(page, phone);
const field = await inspectFocused(page, keyboard);
const save = await inspectReachable(page, page.getByRole('button', { name: 'Guardar' }), keyboard);
await page.screenshot({ path: '<run>/validation/ui/US-2-teclado-android.png' });
await hideKeyboard(page, phone);
const card = page.getByTestId('stop-2');
const box = await card.boundingBox();
const drag = await touchDrag(page, {
  from: { x: box.x + box.width / 2, y: box.y + box.height / 2 },
  to: { x: box.x + box.width / 2, y: box.y - 160 },
  track: card,
});
console.log(JSON.stringify({ field, save, drag, consoleErrors }, null, 2));
await browser.close();
```

## Resultado

Devolver por historia y perfil simulado: `PASS`, `FAIL` o `BLOCKED`, con las métricas
de teclado y gestos, los screenshots (rutas bajo `validation/ui/`, con viewport en el caption) y,
para cada `FAIL`, pasos mínimos, resultado esperado y observado e historias invalidadas. No editar
código ni el manifiesto de evidencia: lo integra `issue-delivery-playwright-review`.
Identificar el alcance como simulación web y `nativeValidation: not-executed`. En Conductor,
BLOCKED se reserva para impedimentos reales de la prueba web (runtime, navegador, datos o acceso),
no para la ausencia de teléfono. No aprobar fallos observados en la app ni relajar permisos para
evitar un bloqueo. Conservar las limitaciones nativas como notas no bloqueantes.
