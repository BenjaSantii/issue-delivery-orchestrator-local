# Calidad antes y dentro de la PR

Aplicar en la fase PR y en cada push de convergencia. En el historial analizado, el reviewer
automatizado encontró la mayoría de los defectos recién después de abrir la PR, y 11 de 30 PRs se
bloquearon sólo por "validación no acreditada". Esta referencia adelanta ambos.

## 1. Paridad con CI

Invocar `test` pidiendo paridad con CI contra `origin/<prTarget>` del estado (la rama del padre en
un stack) sobre el delta completo.
Consumir su recibo; reparar cada `branch-caused` con `issue-delivery-implement` y repetir. Un
`base-failure` o `environment` se declara en el bloque de validación, no se oculta.

## 2. Review previa a la PR

En modo `conductor`, esta etapa no es requerida: no ejecutar ni esperar LocalReviewer, no pedir
credenciales para CLIs anidados y no fabricar un recibo PASS. Continuar con la PR después de QA
y paridad CI, declarando `LocalReviewer: NOT_RUN — no requerido en Conductor` en la validación.
Se mantienen las revisiones y la convergencia de GitHub. El resto de esta sección aplica sólo
fuera de Conductor.

Antes de invocar LocalReviewer, pedir permiso explícito para esa ejecución, indicando SHA,
alcance, provider/modo y número máximo de ejecuciones. El permiso cubre una ejecución salvo que
el usuario autorice un ciclo con un límite explícito. La aprobación del spec, la entrega, las
reparaciones, los tests o la PR no concede ese permiso. Respetar las cancelaciones previas y
las reglas de `pr-blocker-convergence`.

Si no hay autorización, conservar el gate pendiente y declarar la review NOT_RUN o CANCELLED.
Completar primero el trabajo independiente autorizado. No ejecutar la review para desbloquear
el gate por cuenta propia ni inventar un PASS.

Invocar la skill `local-reviewer` (`$local-reviewer` en Codex o `/local-reviewer:local-reviewer` en
Claude Code) sólo con permiso vigente, con alcance de branch delta y sin publicar:

```bash
python3 <local-reviewer-root>/scripts/local-reviewer --provider auto \
  --base origin/<prTarget> --no-auto-pr \
  [--mode ultra] --save <run-root>/pre-pr-review/run-<NN>.md
```

- Usar `--mode ultra` cuando el delta supere ~1500 líneas de producto o toque tres o más apps, o
  cuando el spec declare riesgos de concurrencia, escrituras con resultado incierto o ciclo de vida
  asíncrono.
- Nunca pasar `--publish`.
- Si `local-reviewer` no está instalado o ningún provider está autenticado, bloquear con el error
  exacto. No abrir la PR sin esta review.

Procesar los findings con `pr-blocker-convergence` como ronda `00` en la misma memoria del run
(`history/round-00.json`), con las mismas disposiciones, reparaciones por causa raíz y detección de
ciclos. Después de reparar, pedir permiso antes de volver a ejecutar la review sobre el nuevo
HEAD, salvo que esa ejecución ya esté cubierta por un ciclo explícitamente autorizado. El recibo
final debe corresponder a una ejecución sobre el HEAD que se va a pushear. El máximo de tres
ejecuciones es un límite, no una autorización para realizarlas. Si la
tercera todavía trae `FIX`, aplicar `STOP` y consultar al usuario.

Guardar el recibo en `<run-root>/pre-pr-review/receipt.md`:

```markdown
# Pre-PR review
SHA: <sha completo del HEAD revisado>
Provider / modo: <codex|claude> / <standard|ultra>
Ejecuciones: <n>
Findings: <total> (FIX <n>, SKIP <n>)

| ID | Finding | Disposición | Commit o motivo |
| --- | --- | --- | --- |

Resultado: PASS | SKIP_ONLY
```

`PASS` significa que la última ejecución no trajo findings bloqueantes; `SKIP_ONLY`, que todo lo
restante tiene `SKIP` con motivo. Registrar con `record-pre-review --receipt <ruta>`.

## 3. Bloque de validación en la PR

Escribir `<run-root>/validation/pr-validation.md` sólo con resultados reales del HEAD pusheado y
publicarlo con `publish-validation --receipt <ruta>`. El motor lo inserta entre marcadores dentro
del body; ejecutar nuevamente después de cada push.

```markdown
## Validación
SHA verificado: `<sha completo>`

### Automatizada (paridad con CI contra `<target>`)
| Check | Comando | Resultado |
| --- | --- | --- |
| Lint (`--max-warnings=0`) | `…` | PASS · 0 warnings · proyectos: … |
| Typecheck | `…` | PASS |
| Unit tests | `…` | PASS · <n> tests |
| DB tests | `…` | PASS / No aplica |
| Migraciones / contratos | `…` | PASS / No aplica |

### Manual
| Historia | Superficie | Método | Resultado |
| --- | --- | --- | --- |
| US-001 | shipment-manager 1440 px y 390 px | cua-driver | PASS · ver comentario de evidencia |

### Review previa
<provider/modo> sobre `<sha>`: <resultado>, <n> FIX aplicados, <n> SKIP.

### No ejecutado
- <check>: <motivo concreto>
```

Reglas:

- Copiar comandos, códigos de salida y conteos desde los recibos de `test`; no resumir de memoria.
- En Conductor, declarar simulación web móvil y sus límites; no exigir teléfono ni emulador nativo.
- Fuera de Conductor, para apps móviles nativas, declarar dispositivo o emulador y build probado. Si no hubo prueba en
  dispositivo, decirlo en `No ejecutado`; no dejar que el reviewer lo infiera.
- No incluir secretos, URLs privadas ni datos del ledger.
