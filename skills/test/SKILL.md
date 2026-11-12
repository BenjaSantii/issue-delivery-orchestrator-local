---
name: test
description: >
  Seleccionar, ejecutar y atribuir la validación automatizada mínima suficiente
  para un cambio de código. Usar cuando el usuario pida correr tests, verificar
  una implementación o diagnosticar una falla de CI, y cuando otra skill delegue
  unit tests, integración o typecheck. No usar para revisión visual manual.
---

# Focused Tests

Validar el riesgo real del cambio sin convertir cada handoff en una suite completa.
Leer todos los `AGENTS.md` aplicables y respetar el package manager y los comandos
existentes del repositorio.

## Entrada

Aceptar el alcance indicado por el usuario o la skill llamadora. Cuando exista,
recibir el SHA inicial, los criterios de aceptación, los comandos declarados y la
branch base. Si falta alguno, inferir sólo lo necesario desde el diff y el grafo
del proyecto.

## Reutilizar evidencia

Antes de ejecutar, revisar los resultados ya obtenidos en la conversación o en el
recibo del run. No repetir un comando que ya pasó si los archivos que cubre y su
configuración relevante no cambiaron después. Un rebase u otra operación que sólo
reescriba historia no invalida por sí sola el resultado cuando el patch efectivo
permanece igual.

## Elegir el alcance

Para distinguir las pruebas permanentes de la validación temporal de QA, aplicar
[retained-tests.md](references/retained-tests.md). Revisar duplicación entre capas en el delta
de tests; informar el recorte al implementador sin modificar archivos desde esta skill.

1. Calcular el delta desde el SHA inicial o base indicada, incluyendo cambios sin
   commit.
2. Identificar los proyectos afectados y sus targets existentes. En Nx, usar los
   paths explícitos con `pnpm exec nx show projects --affected --files=...` y
   consultar cada proyecto con `pnpm exec nx show project <proyecto> --json`.
3. Preferir, en este orden:
   - tests existentes directamente relacionados con el comportamiento cambiado;
   - el archivo o conjunto focal más pequeño que pueda falsar el riesgo;
   - typecheck de los proyectos afectados cuando cambió código tipado.
   Si no hay un spec obvio, usar los tests relacionados con los archivos cambiados (en Jest,
   `--findRelatedTests <archivos>`), no la suite del proyecto.
4. No ejecutar la suite completa de un proyecto, E2E ni validación de todo el repo fuera de la
   paridad completa previa a la PR, salvo que el usuario lo pida explícitamente.
5. Ejecutar una sola tarea pesada a la vez: nunca Jest en paralelo con typecheck, lint u otro
   Jest, y Jest con `--runInBand`. En paralelo el sistema mata los procesos por memoria y cada
   reintento cuesta otra corrida.
6. Si el host tiene el comando `heavy` (`command -v heavy`), anteponerlo a todo Jest, typecheck,
   lint y `nx run-many`: `heavy pnpm nx run <proyecto>:test -- --runInBand ...`. `heavy` limita
   cuántas tareas pesadas corren a la vez en toda la máquina, también entre agentes distintos, y
   espera solo si no hay turno. No lanzarlo en segundo plano para saltarse la espera.

En TurboShop, ejecutar `pnpm` desde la raíz. No asumir que `nx test <app>` es el
default: primero buscar la forma focal ya usada por el proyecto.

## Paridad con CI

Cuando la skill llamadora pida paridad con CI, leer [ci-parity.md](references/ci-parity.md). Tiene
dos modos:

- `completa`: una sola vez por PR, justo antes del push que la abre. Reproduce los jobs de
  validación de la branch target sobre el delta completo, con las suites de tests de los
  proyectos afectados.
- `focal`: cualquier push posterior, como las rondas de convergencia. Lint y typecheck de los
  proyectos afectados, y sólo los tests relacionados con lo que cambió desde el último push
  validado. Las suites completas quedan para la CI de la PR.

Si la skill llamadora no dice el modo, usar `focal`.

## Fallas

No repetir una suite completa para investigar una sola falla. Aislar el test que
falló y comprobar primero configuración, variables de entorno y servicios locales.
Si la atribución sigue dudosa, reproducir el mismo comando sobre el SHA exacto de
la base en un checkout desechable y clasificarlo como:

- `branch-caused`: sólo falla con el cambio o empeora con él;
- `base-failure`: falla igual en la base;
- `environment`: depende del entorno y pasa al aislarlo;
- `unclear`: no hay evidencia suficiente.

Esta skill diagnostica y reporta. No modifica código para reparar una falla salvo
que el usuario o la skill llamadora también haya autorizado la reparación.

## Resultado

Entregar un recibo breve con:

- delta y proyectos afectados;
- evidencia reutilizada y por qué sigue vigente;
- comandos exactos, código de salida y conteos disponibles;
- validaciones no ejecutadas y razón;
- estado `PASS`, `FAIL` o `BLOCKED`, con atribución de cada falla.

Para métricas de un run nuevo, devolver conteos observados y findings con IDs estables cuando
estén disponibles; señalar duplicados y reparaciones. No inventar conteos o tokens y no publicar
desde esta skill: el controlador registra el recibo según
[run-metrics.md](../issue-delivery-orchestrator/references/run-metrics.md). Esto no amplía la suite
que se ejecuta ni los tests que se conservan en la PR.
