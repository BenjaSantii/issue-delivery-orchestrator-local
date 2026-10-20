# Evidencia nativa mediante ADB

No aplica a Conductor Cloud: ese modo siempre usa simulación web y no exige dispositivos.

Usar esta excepción sólo cuando el usuario solicite o autorice probar una app Android nativa mediante ADB. No sustituye la revisión de interfaces web ni cambia el modo, worktree o reviewer persistido del run. ADB puede conectarse por USB o depuración inalámbrica según lo acordado con el usuario.

Registrar la instrucción real del usuario, dispositivo y build probado. No interpretar capturas antiguas, una conexión ADB exitosa o un test con mocks como una prueba funcional nativa. Ejecutar los escenarios afectados y guardar PNG originales bajo el run. Conservar los límites de la prueba y atribuir la evidencia al commit exacto, reutilizando sólo comportamientos no invalidados.

El manifiesto v2 mantiene `verifiedCommit`, `runtimeId`, `verifiedAt`, `scenarioIds`, screenshots y anotaciones. Para evidencia nativa declarar:

```json
{
  "verification": {
    "status": "PASS",
    "provider": "adb",
    "verifiedCommit": "<HEAD completo>",
    "runtimeId": "<runtime registrado>",
    "verifiedAt": "<ISO-8601>",
    "scenarioIds": ["US-001"],
    "nativeReview": {
      "userInstruction": "<instrucción explícita del usuario>",
      "device": "<modelo del dispositivo o emulador>",
      "build": "<applicationId y versión, build o bundle probado>",
      "transport": "wireless"
    }
  }
}
```

`transport` admite `wireless` o `usb`. La metadata documenta la autorización de la conversación; no es una autorización que el agente pueda inventar. El motor rechaza recibos sin estos campos y aplica los mismos gates de PASS, HEAD, runtime, fecha y escenarios. ADB no admite asistencia Playwright ni se etiqueta como CUA o Browser.

Preparar, inspeccionar, registrar y publicar mediante los mismos comandos `prepare-evidence`, `checkpoint --phase manual-revision` y `publish-evidence`. El recibo conserva el método y metadata nativos. El comentario de GitHub identifica ADB; el reviewer web configurado permanece intacto. Si también hay historias web, verificarlas con su reviewer configurado y conservar recibos separados, sin atribuir pruebas web a ADB.
