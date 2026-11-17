# Política de decisiones

Cada pregunta al usuario detiene el run hasta que responde. En LOG-486 las esperas sumaron unos 80
minutos de un total de 2h18m, y varias preguntas tenían una respuesta deducible del spec o del
código. Esta política dice cuándo deducir y cuándo preguntar. Es obligatoria en la vía express y
aplica también al flujo normal después del Grill: Implement, Refactor, revisión manual,
reparaciones y triage de blockers. El Grill normal sigue entrevistando según su skill.

## Antes de preguntar, deducir

Ante una duda, buscar la respuesta en este orden y decidir con la primera fuente que la determine:

1. El spec y los tickets aprobados, con las decisiones ya tomadas por el usuario.
2. Lo que la issue de Linear pide de forma literal, con el menor alcance posible (YAGNI). Lo que no
   sea imprescindible va a `Deferred`.
3. Las reglas del repositorio (`AGENTS.md`, `CODING_STANDARDS.md`, `docs/rules/`) y los patrones
   existentes de la app. Si una regla del repositorio contradice el spec, gana la regla y se corrige
   el spec antes de implementar.
4. La consistencia con flujos parecidos de la misma app: mismos pasos, textos, estados vacíos,
   mensajes de error, confirmaciones y ubicación de acciones. Si dos opciones cumplen la issue,
   elegir la que el usuario ya conoce de otra pantalla y nombrar esa pantalla en el fundamento.
5. El comportamiento actual, en todo lo que la issue no pide cambiar.

## Cuándo preguntar

Devolver `NEEDS_USER_DECISION` o preguntar en el chat sólo si se cumple al menos una condición:

- Mantener lo aprobado causaría un defecto real (regresión, riesgo de seguridad o de datos), o una
  regla del repositorio obliga a cambiar un acuerdo. Si alguien sólo prefiere algo distinto de lo
  aprobado, mantener lo aprobado sin preguntar.
- Cambia el alcance o un comportamiento visible para el usuario, ninguna fuente de la lista lo
  determina y las opciones llevan a resultados materialmente distintos.
- La acción es irreversible o de alto riesgo: pérdida o migración de datos en ambientes
  compartidos, dinero, seguridad o permisos, o compromisos con sistemas externos.

En cualquier otro caso, decidir y seguir. Al preguntar, explicar por qué no se pudo deducir,
recomendar una respuesta y juntar en una sola pausa todas las preguntas pendientes de la misma
etapa.

Un hallazgo de QA que es un defecto claro dentro del spec aprobado (el resultado esperado está
escrito y la app no lo cumple) no es una decisión: se repara sin preguntar y se informa.

## Registro

Registrar cada decisión tomada sin aprobación del usuario en
`.local-runtime/issue-delivery-orchestrator/<run-id>/decisions.md`, una entrada por decisión:

```md
## <etapa>: <pregunta en una línea>

- Decisión: <qué se hizo>
- Fuente: <criterio 1 a 5 y la evidencia concreta, por ejemplo la ruta del flujo parecido>
- Descartado: <alternativas y por qué>
```

No registrar lo que el usuario ya aprobó. Los workers devuelven sus decisiones en su respuesta
final y el chat principal es el único que escribe el archivo.

## Resumen final

El mensaje final del run, y el de cada handoff posterior, incluye la sección "Decisiones tomadas
sin tu aprobación": una línea por cada entrada de `decisions.md` que el usuario no vio en un
resumen anterior, con la decisión, la razón y la etapa, y al final la ruta del archivo. Si no hubo
decisiones, decirlo en una línea. La sección es informativa y no hace preguntas.
