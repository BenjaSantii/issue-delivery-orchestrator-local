# Evidencia de validación

## Carpeta del run

Guardar la evidencia con nombres convencionales y dejar que el motor arme manifiesto, subidas y
comentarios. No escribir JSON de evidencia a mano salvo los casos de [Manifiesto manual](#manifiesto-manual).

```
.local-runtime/issue-delivery-orchestrator/<run-id>/validation/
  ui/<ID>[-<NN>][-<slug>].png   US-1-01-lista-filtrada.png, QA-001.png
  qa/<QA-ID>.txt | .json         salida del comando, query o script
  notes.md                       una línea por historia, caso o captura
  callouts.json                  opcional
  qa-results.json                si el spec tiene QA Script
  pr-validation.md               bloque de validación de la PR
```

- `<ID>` es la historia o el caso QA. `<NN>` ordena las capturas de una historia; la primera es
  la que se ve en la PR y el resto queda colapsado.
- `<slug>` es el título de la captura. Sin nota propia, también es su caption.
- `notes.md` usa líneas `US-1: qué demuestra`. La nota de `US-1` es el caption de su primera
  captura; `US-1-02: …` le da caption a una captura puntual; `QA-003: …` es el summary del caso
  cuando `qa-results.json` no lo trae.
- No guardar en `ui/` capturas de fallos intermedios ni estados duplicados.

Publicar todo con un solo comando, después de crear la PR y en cada push posterior:

```bash
python3 <plugin-root>/scripts/issue-delivery <issue> publish-run [--runtime <runtime-id>]
```

El motor genera `validation/ui-manifest.json` con el HEAD y el runtime registrado, sube capturas y
salidas a Linear, publica en la PR una imagen por historia con link a Linear y aplica
`qa-results.json` y `pr-validation.md` si existen. `--runtime` sólo hace falta con más de un
runtime registrado. La respuesta es un resumen corto; no leer los archivos generados para
confirmar la publicación.

## Callouts opcionales

Por defecto la captura no lleva anotaciones: el caption explica qué mirar. Agregar callouts sólo
cuando el cambio sea una región chica dentro de una pantalla grande y el caption no baste. En ese
caso declararlos en `callouts.json` por nombre de archivo sin extensión:

```json
{
  "US-1-01-lista-filtrada": [
    {"kind": "highlight", "caption": "Nuevo selector de estado",
     "bounds": {"x": 0.62, "y": 0.18, "width": 0.25, "height": 0.12}}
  ]
}
```

`kind` admite `highlight`, `circle` y `arrow`. Todos usan `bounds` normalizados entre `0` y `1`;
`arrow` exige además `anchor: {"x": <0..1>, "y": <0..1>}`. Calcular bounds y anchor desde la misma
observación final que produjo el PNG, preferentemente mediante DOM o AX. Ningún callout puede
cubrir más del 60% de la imagen. Sólo cuando hay callouts, inspeccionar una vez la copia
`.annotated.png` que genera el motor y confirmar que cada indicador apunta al elemento descrito.

## Manifiesto manual

Escribir `validation/ui-manifest.json` a mano sólo para evidencia ADB o con asistencia headless,
que necesitan metadata que el motor no puede deducir. Un manifiesto escrito a mano tiene prioridad
sobre la carpeta; `publish-run` lo usa tal cual.

```json
{
  "evidenceVersion": 2,
  "verification": {
    "status": "PASS",
    "provider": "cua-driver",
    "verifiedCommit": "<sha>",
    "runtimeId": "<runtime-id>",
    "verifiedAt": "<ISO-8601>",
    "scenarioIds": ["US-1"]
  },
  "screenshots": [
    {
      "storyId": "US-1",
      "title": "Resultado observable",
      "caption": "Qué demuestra la captura",
      "path": ".local-runtime/issue-delivery-orchestrator/<run-id>/validation/ui/US-1.png"
    }
  ]
}
```

Cada captura necesita `caption`, `callouts` o `annotationReason`.

Con un manifiesto manual que lleve callouts, ejecutar
`python3 <plugin-root>/scripts/issue-delivery <issue> prepare-evidence --manifest <ruta>` e
inspeccionar la copia anotada antes del checkpoint. El checkpoint de Manual Revision vuelve a
preparar la evidencia y bloquea coordenadas inválidas.

El motor numera los callouts, dibuja una copia PNG determinista y preserva el original. En Linear y
GitHub publica la copia anotada con captions numerados y un link a la captura original. Todo cambio
que invalide el PASS también invalida ambas copias y sus coordenadas.

Algunas superficies entregan bytes JPEG aunque la ruta termine en `.png`. El motor detecta ese caso
y crea una copia PNG visualmente equivalente antes de publicar o anotar, sin modificar la captura
recibida. Usar `sips` en macOS y aceptar ImageMagick o ffmpeg como fallback en otros hosts.

## Selección por destino

Linear recibe siempre el set completo. En la PR se ve la primera captura de cada historia y las demás
quedan dentro de un bloque colapsado, con link a Linear. Cuando el usuario pida sacar capturas de
GitHub por completo, usar un manifiesto manual con `publishToGithub: false` en esas capturas.
