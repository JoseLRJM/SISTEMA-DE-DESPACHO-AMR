# Diagnóstico de rendimiento del envío de tareas

Fecha: 2026-09-08

## Alcance y seguridad

El diagnóstico se ejecutó sobre copias temporales de `agv.db`. El cliente RCS fue reemplazado por un stub que conservó el flujo local y simuló esperas de 100 ms, 1 s, 5 s y error. No se llamó a un RCS real. La sonda temporal fue retirada al terminar. No se cambió payload, `reqCode`, FIFO, reservas, rollback, estados, reintentos, timeouts, SQLite ni workers.

## Resumen ejecutivo

La causa dominante es que los tres endpoints esperan sincrónicamente a `createTask`. Por ello, una espera RCS de 1 s agrega aproximadamente 1 s al POST y una espera de 5 s agrega aproximadamente 5 s. La aplicación aporta normalmente 140–200 ms adicionales.

Hay además un problema de percepción independiente: antes del `fetch` no se deshabilita el botón ni se muestra `Enviando...`. La primera confirmación aparece sólo al terminar el POST. Después del éxito, los tres flujos todavía ejecutan recargas globales y secuenciales de locations, catálogo y órdenes, a pesar de existir WebSocket incremental.

## Trazado actual

### FIFO

`click` → construir body → `POST /api/fifo/execute` → resolver y revalidar FIFO → crear `MovementOrder(pending_dispatch)` y reservar rack → commit local → auditoría de reserva y commit → construir payload → guardar `dispatch_request_json` y commit → evento debug enviado (uno o dos commits) → `createTask` → evento debug recibido (uno o dos commits) → guardar respuesta/estados y commit → refresh → recargar orden → construir respuesta → FastAPI serializa → navegador muestra confirmación → `loadLocations()` → `loadCatalog()` → `loadMovementOrders()` → seleccionar celda/dibujar.

### Direct Move

Igual que FIFO, sustituyendo la selección por validación de las dos celdas, rack, áreas, material y reservas. Tras el POST también recarga locations, catálogo y órdenes.

### Operator Window

Consulta ventana → valida contraseña → consulta botón → ejecuta internamente FIFO o Direct Move → mismo despacho RCS → respuesta → cierra modal y muestra confirmación → recarga locations, catálogo y órdenes → dibujo completo.

El WebSocket se alimenta después de cada commit. El `after_commit` sólo agenda cambios; otro hilo vuelve a consultar la entidad, construye el evento y lo difunde. El frontend aplica `order_created/order_updated`, rack y location incrementalmente, pero esas actualizaciones compiten con las recargas globales explícitas del flujo POST.

## Resultados medidos

Los tiempos son de una máquina local y una copia de la base representativa con 10,000 locations. Son muestras diagnósticas, no un SLA. SQL, commits y funciones son mediciones incluidas dentro del tiempo de aplicación, por lo que no deben sumarse entre sí.

| Flujo | Stub RCS | Aplicación | Total endpoint |
|---|---:|---:|---:|
| FIFO | 100 ms | 148.2 ms | 248.2 ms |
| FIFO | 1,000 ms | 152.1 ms | 1,152.1 ms |
| FIFO | 5,000 ms | 159.4 ms | 5,159.4 ms |
| Direct Move | 100 ms | 145.3 ms | 245.3 ms |
| Direct Move | 1,000 ms | 146.9 ms | 1,146.9 ms |
| Direct Move | 5,000 ms | 136.1 ms | 5,136.1 ms |
| Operator Window (Direct Move configurable) | 100 ms | 140.6 ms | 240.6 ms |
| Operator Window (Direct Move configurable) | 1,000 ms | 199.9 ms | 1,199.9 ms |
| Operator Window (Direct Move configurable) | 5,000 ms | 163.0 ms | 5,163.0 ms |

Promedio de aplicación de las nueve muestras: aproximadamente 155 ms. Participación del RCS: 40–42% con 100 ms, 83–87% con 1 s y 96–97% con 5 s.

### Componentes observados

| Componente | FIFO | Direct Move | Operator Window |
|---|---:|---:|---:|
| Queries SQL | 46 | 44 | 48 |
| SQL acumulado | 38.8–42.1 ms | 36.2–39.7 ms | 39.9–45.0 ms |
| Selección FIFO | 46.4–49.0 ms | N/A | N/A en botón Direct Move medido |
| Commits | 8 | 8 | 8 |
| Flushes observados | 11 | 11 | 11 |
| `refresh()` explícitos | 2 | 2 | 2 |
| Tiempo acumulado de commits | 20.8–36.6 ms | 24.3–35.5 ms | 25.4–43.5 ms |
| Construcción payload | 1.3–1.4 ms | 1.6–1.8 ms | 1.4–1.5 ms |
| Construcción objeto respuesta | 4.6–6.6 ms | 5.5–5.9 ms | 6.4–9.6 ms |
| Debug enviado/recibido y retención | 25.8–36.1 ms | 30.6–37.9 ms | 24.6–42.8 ms |

El modo Operator Window medido fue Direct Move configurable. Un botón Operator FIFO agrega las dos consultas de ventana/botón al recorrido FIFO y comparte el resto del perfil FIFO.

### Timeout simulado

El stub de error se activó tras 100 ms. El rollback completo produjo:

| Flujo | Queries | Commits | Flushes | Refresh | Aplicación sin espera | Total |
|---|---:|---:|---:|---:|---:|---:|
| FIFO | 61 | 9 | 16 | 3 | 224.0 ms | 324.0 ms |
| Direct Move | 59 | 9 | 16 | 3 | 193.6 ms | 293.6 ms |
| Operator Window | 63 | 9 | 16 | 3 | 198.8 ms | 298.8 ms |

Esto mide el manejo local del error. Un timeout real puede durar mucho más por los timeouts y reintentos HTTP descritos abajo.

## Queries y scans

Hallazgos principales:

- FIFO carga con `.all()` todos los racks/celdas candidatos del material para después escoger el primero en Python. Es un scan proporcional al número de candidatos.
- La búsqueda de destino carga con `.all()` todas las locations libres del área y luego descarta reservas en Python.
- La lista de destinos reservados consulta todas las órdenes activas en cada ejecución; también se repite en Direct Move.
- FIFO vuelve a consultar rack, origen y destino inmediatamente después de haberlos obtenido en la selección. Son tres lecturas deliberadas de revalidación, pero duplicadas desde el punto de vista de latencia.
- El endpoint vuelve a consultar el rack para auditoría y vuelve a consultar la orden después del despacho.
- `_movement_order_out` puede ejecutar hasta siete consultas puntuales: rack, origen, destino, área origen, área destino, material y ubicación actual del rack.
- La auditoría debug cuenta toda la tabla en cada evento y, al superar 20 filas, consulta y elimina las más antiguas.

No se encontraron scans completos de las 10,000 locations en los endpoints de ejecución, pero sí colecciones completas dentro del área/material correspondiente y scans lógicos de reservas activas.

## Flush, commit y refresh

En la base representativa, cada éxito produjo ocho commits:

1. Crear orden y reservar rack — **A: necesario actualmente para durabilidad antes del RCS**.
2. Auditoría de reserva — **B: probablemente consolidable**, actualmente separado.
3. Guardar `dispatch_request_json` — **A/B:** asegura evidencia antes del RCS, aunque podría evaluarse junto con el primer commit.
4. Insertar debug `sent` — **C: logging/auditoría**.
5. Purgar debug antiguo — **C: logging/auditoría**.
6. Insertar debug `received` — **C: logging/auditoría**.
7. Purgar debug antiguo — **C: logging/auditoría**.
8. Guardar respuesta RCS y estado final — **A: necesario**.

Si la tabla debug aún no supera el límite de 20 eventos, los commits 5 y 7 no ocurren y el total normal es seis. Se observaron dos `refresh()` explícitos: después de la creación local y después de guardar la respuesta. Los 11 flushes incluyen flushes automáticos provocados por commits/queries, además del `flush()` explícito que obtiene el id de la orden.

`dispatch_response_json` y `rcs_response_json` se guardan en el mismo commit final. `dispatch_request_json` usa un commit previo independiente. Cada evento debug puede generar dos commits separados cuando hay purga.

## Frontend y latencia percibida

| Momento | Comportamiento actual |
|---|---|
| Click → inicio fetch | Sólo validaciones/construcción del body; no hay feedback visible nuevo |
| Mientras espera el POST | Botón sigue habilitado; sin spinner ni `Enviando...`; no aparece una orden local pendiente |
| “Enviada” | Sólo después de recibir confirmación RCS dentro de la respuesta POST |
| Post-respuesta | Se lanzan tres recargas globales secuenciales |
| WebSocket | Actualiza elementos afectados incrementalmente, pero no elimina las recargas explícitas |

Recargas encontradas:

- FIFO: `loadLocations()`, `loadCatalog()`, `loadMovementOrders(order_id)` y dibujo.
- Direct Move: las mismas tres y dibujo.
- Operator Window: `loadLocations()`, `loadCatalog()`, `loadMovementOrders()` y dibujo.
- Error de despacho: `refreshAfterDispatchError()` recarga las tres fuentes en paralelo.

No se solicita explícitamente un runtime snapshot completo tras el éxito. Sin embargo, `loadLocations()` fuerza el endpoint pesado y no utiliza el arranque cacheado de IndexedDB. Mediciones de la etapa anterior situaron esa descarga en aproximadamente 0.9–1.0 s para 10,000 locations, después del POST.

El procesamiento incremental del WebSocket afecta sólo la orden/rack/location, pero el tiempo commit → navegador depende del scheduler, consultas de materialización del evento y red. No se asignó una cifra falsa: la sonda backend aislada no tenía un navegador WebSocket conectado. Debe medirse en una siguiente instrumentación E2E con un identificador de correlación si se requiere percentil real de producción.

## Reintentos de `createTask`

- Máximo configurado: dos reintentos además del intento original, es decir, hasta tres POST.
- Reintenta errores de conexión, lectura y estados HTTP 429, 500, 502, 503 y 504.
- Métodos permitidos incluyen explícitamente POST.
- Timeout por intento: conexión 5 s y lectura 20 s.
- `backoff_factor`: 0.4; urllib3 controla la secuencia y `Retry-After` cuando aplica.
- El payload se construye una vez y mantiene el mismo `reqCode` durante los reintentos internos.
- El header `X-Request-Id` también se crea antes del `session.post`, por lo que el adaptador reutiliza la misma petición lógica.

Existe riesgo real de duplicado si RCS acepta el primer POST y su respuesta se pierde. El cliente vuelve a enviar el POST. Mantener el mismo `reqCode` sólo protege si el RCS garantiza y conserva idempotencia por ese campo. El código local no verifica esa garantía, no consulta el estado por `reqCode` antes de reintentar y no puede demostrar que el servidor RCS deduplique. Por tanto, la protección actual es **no confirmada**.

En el peor caso teórico de timeouts de lectura, tres intentos pueden mantener bloqueado el POST cerca de 60 s, más backoff. Los fallos de conexión pueden aproximarse a 15 s, más backoff.

## Top 5 mejoras recomendadas

1. **Alto impacto / bajo riesgo:** feedback inmediato (`Enviando...`, botón deshabilitado y tiempo visible) manteniendo el POST síncrono. No cambia semántica.
2. **Alto impacto / bajo riesgo:** eliminar las recargas globales de éxito y aplicar la respuesta POST/eventos incrementales sólo a orden, rack y locations afectadas. No cambia confirmación RCS.
3. **Medio impacto / bajo-medio riesgo:** hacer que `_movement_order_out` reutilice entidades ya cargadas o use una consulta agrupada, evitando hasta siete consultas puntuales.
4. **Medio impacto / riesgo medio:** reducir commits y purgas de debug; conservar evidencia de solicitud/respuesta, pero agrupar retención o sacarla del camino crítico. Requiere revisar requisitos de auditoría.
5. **Medio impacto / riesgo medio:** limitar selección FIFO y destino en SQL y revisar índices compuestos para disponibilidad/reservas. Debe conservar estrictamente orden FIFO y exclusión de reservas.

También conviene eliminar lecturas duplicadas únicamente cuando se mantenga una revalidación transaccional equivalente.

## Alternativa 202/pending_dispatch

Es técnicamente posible responder `202` después de crear y reservar localmente, enviar a RCS en background y publicar `dispatched/error` por WebSocket. Reduciría drásticamente la espera HTTP percibida, pero cambia la semántica: “orden creada” dejaría de significar “aceptada por RCS”. Introduce recuperación de jobs tras reinicio, entrega al menos una vez, idempotencia, reintentos persistentes, conciliación, UX de estados pendientes y manejo de reservas prolongadas. No se recomienda hasta resolver formalmente idempotencia y durabilidad. No fue implementado.

## Conclusión

Sin cambiar semántica pueden implementarse con seguridad relativa las recomendaciones 1, 2 y 3. Las recomendaciones 4 y 5 requieren pruebas específicas de auditoría, concurrencia y FIFO. La estrategia 202 es de alto riesgo y queda fuera de esta etapa.
