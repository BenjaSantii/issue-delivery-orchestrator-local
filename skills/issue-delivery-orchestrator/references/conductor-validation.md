# Validación local en Conductor

Esta es la política del usuario para Conductor: UI móvil por simulación de navegador y
comportamientos nativos mediante Jest o scripts. No exigir teléfono, ADB ni emulador nativo.
Complementa [fresh-qa.md](fresh-qa.md) y aplica también al diseñar el guion en Grill.

## Orígenes web

Las URLs que abre el navegador deben usar estos orígenes locales permitidos por el usuario:

- `http://localhost:3001`
- `http://localhost:3002`
- `http://localhost:3003`
- `http://localhost:3004`

Preferir 3003 para manager y 3004 para vendedor cuando estén libres; no son asignaciones
exclusivas. Comprobar que el puerto no pertenece a otro proceso antes de iniciar la app. Cada
workspace cloud tiene su VM; dentro de ella, Generador y QA comparten los mismos servicios.

Arrancar realmente Expo/web con el puerto elegido (`expo start --web --port <puerto>`),
manteniendo el entorno y la conexión al backend del runtime. Registrar el proceso y su puerto
real con Local Runtime y pasar la URL exacta al reviewer. No basta cambiar la URL de Playwright
si el servidor sigue escuchando en otro puerto. No usar `127.0.0.1`, un puerto 21xxx o el preview
público como origen del navegador. Los puertos internos de APIs, DB y Redis se conservan.

En TurboShop, inspeccionar `scripts/sales-force-proposals-runtime.js`: hoy calcula el puerto
web como `manifest.portRange.start + 82/83`. No invocarlo sin adaptar ese arranque al rango
anterior. Para QA, se puede preparar un launcher bajo el run ignorado que reutilice la carga de
entorno, `EXPO_PUBLIC_BACKEND_SALES_FORCE_URL`, el flag E2E del vendedor y el registro mediante
`registerOwnedProcessForCurrentRuntime`, pasando el puerto web elegido a Expo y al registro.
Resolver imports desde el checkout al mover un launcher. No editar producto ni el manifiesto
del runtime a mano para simular otra URL. Reutilizar el servidor durante todos los casos.

Comprobar `page.url()` y la consola tras cargar el mapa. Si Google aún rechaza el origen exacto
con `RefererNotAllowedMapError`, informar el origen y mantener ese caso bloqueado; no cambiar
la API key, sus restricciones o Google Maps por un mock para presentar un mapa real como PASS.

## Casos nativos con Jest o scripts

Usar primero tests existentes que cubran el contrato. Cuando falten, ejecutar una prueba
focalizada contra el código real de la app, sustituyendo sólo la frontera del SO/proveedor:

- GPS: permisos concedidos/denegados, registro y liberación de suscripciones, callbacks de
  ubicación, parada al cierre de las 19:00 y ausencia de tareas o envíos posteriores al cierre.
- Avisos nativos (`Alert.alert`): disparar el camino real desde la pantalla/hook y verificar
  mensaje, botones y efectos de sus callbacks. La presentación visual del aviso nativo no se
  exige en React Native Web; verificar la interacción web circundante con capturas reales.
- Ciclo de vida, permisos y teclado: simular eventos del SO, avanzar el reloj cuando aplique
  y comprobar los efectos observables; completar layout, teclado simulado y gestos en web.

No reemplazar por mocks la lógica que se intenta validar ni aprobar porque un mock devuelve
éxito. Los scripts deben cargar código real o probar la API del runtime y comprobar el estado
resultante, incluida la DB cuando haya persistencia. Conservar la regla de un dueño por
invariante y [retained-tests.md](../../test/references/retained-tests.md): no crear matrices
duplicadas ni un test permanente por cada caso. Los scripts exploratorios quedan en el run.

Para cada caso, guardar comando, SHA, salida/código de salida y assertions observadas. Adjuntar
esa salida al caso del guion y publicarla con Resultado QA; las capturas sólo acreditan lo que
se observó en web. Identificar el método como Jest/script con adaptador nativo simulado.

El caso puede ser PASS cuando sus reglas y callbacks se comprueban de esta manera y las
interacciones web aplicables pasan. No requiere WAIVED por ausencia de dispositivo. Un test
fallido es FAIL; no poder ejecutar o cubrir la regla es BLOCKED, con causa concreta. Registrar
`nativeValidation: not-executed` para el SO real como nota informativa, sin certificar GPS de
fondo, permisos, avisos o rendimiento en un teléfono físico.
