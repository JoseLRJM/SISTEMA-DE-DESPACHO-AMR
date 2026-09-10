# Fase 2B: modo soportado y checkpoints de Locations

## Arquitectura soportada

Una instancia de Despacho AMR, un proceso/worker y una base SQLite local, en un solo host.
No se soportan varios workers, réplicas detrás de un balanceador ni bases compartidas
entre hosts. No se añadió infraestructura distribuida.

Los locks de monitor/single-flight, revisiones runtime, historial de eventos, colas,
WebSocket manager, caché de robots y schedulers son locales al proceso. Esto es
correcto únicamente mientras un proceso sea propietario del runtime.

Antes de inicializar la base o iniciar servicios, se valida WEB_CONCURRENCY/--workers
y se obtiene un bloqueo exclusivo del sistema operativo junto a la ruta canónica
de la base (.runtime.lock). Un segundo proceso falla antes de ejecutar recuperación,
monitor o scheduler. El bloqueo se conserva hasta terminar el proceso; una caída
libera el bloqueo automáticamente. No borrar ni reemplazar el archivo de bloqueo
mientras se ejecuta la aplicación. No acceder a la misma base por hardlinks/alias
ni mediante escritores externos a la aplicación.

HTTP y WebSocket fallan de forma segura sin propietario, incluso con lifespan
deshabilitado. Los lanzadores usan explícitamente 1 worker y lifespan habilitado.
Para otro despliegue usar: python -m uvicorn main:app --workers 1 --lifespan on
No hacer recarga/reemplazo con procesos de aplicación solapados; detener primero
el proceso anterior. La exclusión de procesos no sustituye una solución distribuida.

## Locations

La unidad de confirmación es un snapshot completo, no un evento individual.
Los eventos incrementales siguen actualizando la vista, pero dejan la revisión en
memoria sin confirmar y la caché con complete=false. No elevan la revisión global
persistida. Se solicita un resync completo; solicitudes repetidas se agrupan mientras
hay una pendiente. No se intenta deducir que un lote acabó contando eventos.

El snapshot completo escribe todas las filas y metadata en la misma transacción
IndexedDB. Solo entonces la caché queda completa. observedRevision impide que un
snapshot atrasado certifique una caché que ya recibió cambios más nuevos. La lectura
de filas y metadata comparte una transacción. Caches antiguas sin complete=true se
descartan, sin migración ni rediseño de IndexedDB.

El servidor reintenta snapshots si la revisión cambia durante la lectura. Si no puede
obtener una lectura estable, solicita resync en lugar de acreditar una revisión.
El cliente serializa el procesamiento de mensajes y escrituras de caché de cada
conexión WebSocket. Una reconexión parcial no anuncia Locations como vigentes.

## Coste y límites

Se prioriza coherencia sobre ancho de banda: cambios de Locations pueden requerir
un snapshot completo adicional. Validar latencia y volumen en instalación real.
Con cambios continuos puede aplazarse un checkpoint, nunca certificar uno incompleto.
La interfaz sigue recibiendo eventos. La suite usa SQLite temporal, stubs y pruebas
de procesos; no valida robots, RCS productivo ni comportamiento distribuido.
