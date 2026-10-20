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
   - typecheck de los proyectos afectados cuando cambió código tipado;
   - la suite de un proyecto sólo si no existe una invocación focal fiable.
4. Ejecutar suites completas de una app, E2E, lint o validación de todo el repo
   únicamente cuando el usuario lo pida explícitamente, el ticket lo declare o el
   repositorio no ofrezca un seam menor suficiente.

En TurboShop, ejecutar `pnpm` desde la raíz. No asumir que `nx test <app>` es el
default: primero buscar la forma focal ya usada por el proyecto.

## Paridad con CI

Cuando la skill llamadora pida paridad con CI (por ejemplo, el orquestador antes de pushear),
leer [ci-parity.md](references/ci-parity.md) y reproducir los jobs de validación de la branch
target sobre el delta completo, incluido lint. Este modo no reemplaza la selección focal de
arriba durante la implementación; se ejecuta una vez por push.

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
