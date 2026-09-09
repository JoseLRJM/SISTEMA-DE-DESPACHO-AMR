# Sistema de Despacho AMR — v2026.09.08

## Rendimiento y coherencia

- Caché IndexedDB versionado para 10.000 ubicaciones, con invalidación por revisión y fallback seguro.
- Actualizaciones incrementales de ubicaciones, racks, catálogo y órdenes mediante WebSocket.
- Resync controlado cuando WebSocket está degradado o desconectado.
- Envío FIFO, Direct Move y Operator Window sin recargas globales posteriores al éxito.
- Upsert idempotente para respuestas HTTP y eventos WebSocket, evitando órdenes duplicadas.
- Historial paginado y runtime snapshot limitado a órdenes activas y terminales recientes.
- Consultas de lectura de MovementOrder y serialización de respuestas optimizadas.

## Observabilidad y diagnóstico

- Endpoint administrativo read-only `GET /api/admin/performance-health`.
- Métricas agregadas y acotadas en memoria para HTTP, SQL, snapshots y WebSocket.
- Warnings informativos configurables para operaciones lentas y mensajes grandes.
- Utilidad `db_diagnostics.py` para integridad, comparación multi-base, crecimiento e índices.
- Detección read-only de índices esperados y drift entre modelos y SQLite.

## SQLite

- Índices de reservas e historial evaluados y aplicados mediante procedimiento manual controlado:
  - `idx_movement_orders_status_destination_cell`
  - `ix_movement_orders_created_at_id`
- No se incorporaron migraciones automáticas ni cambios de esquema.

## Verificación

- 63 pruebas automatizadas para despacho, rollback, cancelación, cleanup, WebSocket, IndexedDB, paginación, monitor, diagnóstico y observabilidad.
- Payload RCS, `reqCode`, retries, timeouts y límites transaccionales conservados.
- La base SQLite y los datos operativos no forman parte del repositorio.

## Observaciones operativas

- `status_query_log_json` concentra la mayor parte del tamaño de la instalación evaluada; no se aplicó retención automática.
- Validar el script de arranque apropiado para cada instalación antes del despliegue.
