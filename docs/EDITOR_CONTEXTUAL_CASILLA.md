# Editor contextual de casilla

## Dos vistas dentro del mismo pop-up

Cada apertura empieza en **Usuario**, incluso si la anterior terminó en Admin. Código de casilla, coordenadas, ID y área se muestran como referencia no editable. Solo se habilitan asignación de rack existente, reserva, lote y material del rack; sin rack, estos tres últimos quedan deshabilitados con una explicación. No se crean racks.

**Implementación / Admin** aparece únicamente con la sesión administrativa existente. Muestra los controles de configuración que ya tenía el editor: código, área, habilitada, visible, nota, estado permitido y todos los campos y características de rack. El estado de una casilla con rack queda deshabilitado y se omite del payload para conservar la ocupación derivada. Los otros campos aparecen exclusivamente en Admin.

Ambas vistas requieren la misma sesión para guardar; Usuario es una presentación simplificada, sin roles, tokens ni autorización nuevos. Sin sesión, todo el pop-up queda en consulta y no se permite acceder a Admin. La pérdida de sesión vuelve a Usuario y deshabilita la edición sin descartar el borrador. Si quedan cambios administrativos suspendidos, al recuperar sesión se pide volver a Admin para revisarlos; guardar desde Usuario no los elimina silenciosamente.

Alternar vistas no consulta ni escribe. Los cambios de asignación, reserva, lote y material se conservan. Al intentar salir de Admin con cambios exclusivos pendientes, Cancelar permanece en Admin; Aceptar descarta solo los campos administrativos y pasa a Usuario. Se conserva la advertencia independiente al cambiar de rack, cancelar, cerrar o recargar.

El guardado Usuario construye diferencias con listas explícitas: `location.rack_id`, `rack.lot`, `rack.material_group_id` y `reserved`. Mantiene `rack_id` estable y las huellas originales. Nunca envía campos administrativos, aunque sus controles contengan valores diferentes. Cada rack mantiene su propio estado inicial; no se copian valores del anterior.

Esta ampliación modifica únicamente `static/cell-editor.js`, el HTML/CSS del diálogo, las pruebas del editor y esta documentación. **No cambia `main.py`, `static/app.js`, el modelo, otros endpoints, paneles, canvas, despacho, WebSocket ni políticas de caché** respecto al editor ya implementado. Reutiliza los hooks de sesión actuales y el mismo GET/PUT, transacción, validaciones, cachés y tratamiento de 409/red ambigua.

Las pruebas dinámicas comprueban apertura siempre en Usuario, controles visibles/habilitados, listas permitidas del payload, conservación de configuración, acceso y capacidades de Admin, descarte selectivo, pérdida y recuperación de sesión, ausencia de peticiones al alternar y selección de un rack con sus propios valores iniciales. El navegador confirmó los cuatro campos de Usuario, la configuración completa de Admin con estado ocupado deshabilitado y el guardado de lote contra SQLite temporal conservando código, área y nota. El fixture no se modificó.

## Diagnóstico y decisiones

`selectCell()` y `setPrimarySelection()` sirven también para selecciones programáticas, despacho y captura de puntos. No abren el modal. El clic normal ofrece **Editar casilla**; también hay un botón en el panel de celda. Se mantiene el canvas y sus controladores de selección, desplazamiento y arrastre.

El guardado lateral existente de celda/reserva hace peticiones independientes; el modal usa un endpoint específico. Se mantienen esos endpoints y los paneles para compatibilidad. No hay migración del modelo: `Location.rack_id` sigue siendo la asignación exclusiva y `Rack.material_group_id` sigue siendo el material.

Se reutilizan:

- `_guard_admin_inventory_edit()` para impedir editar racks, orígenes y destinos con órdenes activas.
- `_validate_foreign_keys()` para referencias, área activa y asignación exclusiva de rack.
- `RackIn`, `LocationPatchAdmin`, `_normalize_rack_custom_fields()`, los serializadores existentes y las funciones de reserva/auditoría.
- `_validated_rack_values()`, extraída y compartida con `update_rack()`, para código único, material activo y normalización.
- `applyLocationUpdated()`, `applyRackUpdated()`, sus índices y protección contra actualizaciones antiguas, y la caché IndexedDB existente.
- Los hooks de sesión `after_flush`, `after_commit` y `after_rollback`, sin publicar eventos manuales duplicados.

El helper de `SIN_MATERIAL` admite ahora `commit=False`; su valor predeterminado mantiene el comportamiento de los demás consumidores. El editor inicializa el material especial dentro de su propia transacción cuando hace falta; rechaza asignar uno inactivo en lugar de restaurarlo desde el modal.

## Transacción y concurrencia

`PUT /api/admin/cell-editor/{location_id}` requiere sesión administrativa. Ejecuta `BEGIN IMMEDIATE` **antes de leer**, compara las huellas de todas las columnas de ubicación y racks afectados, y comprueba además la ubicación a la que está asignado cada rack. Esto detecta incluso escritores que cambian una asignación o un campo sin actualizar `updated_at`.

Valida referencias, campos permitidos, material, exclusividad, reservas y órdenes activas; aplica solo los campos enviados que realmente cambian. La ubicación ocupada se deriva al asignar un rack. Un rack reservado no puede reasignarse y ninguna orden activa puede liberarse ni recuperarse desde este endpoint. El cambio de reserva sin orden activa usa la función y auditoría existentes, sin llamar al helper de limpieza que puede confirmar transacciones por separado.

Hay un único commit. El resultado definitivo se construye después del flush y antes del commit; un fallo de validación, flush o construcción de respuesta revierte todos los cambios. Una solicitud sin cambios no ejecuta INSERT/UPDATE/DELETE ni commit. Los eventos de entidades se publican después del commit y se descartan en rollback.

Un estado diferente produce HTTP 409. El modal conserva el borrador y permite recargar tras confirmar su descarte. Un fallo de red o respuesta 5xx bloquea otro envío hasta revisar los datos; no hay reintento automático de escrituras con resultado ambiguo.

## Interfaz y peticiones

- Se crea un solo `<dialog>` en el HTML, con foco contenido, Escape, retorno de foco y advertencia de cambios pendientes.
- Un GET `/api/cell-editor/{id}` al abrir obtiene un estado coherente y sus huellas. Se usan los catálogos e índices ya cargados para opciones; no se vuelve a consultar todo el catálogo.
- Al seleccionar otro rack, se confirma el descarte de cambios del rack anterior. Si faltan sus detalles y huella en el borrador, se hace un GET del mismo endpoint con `?rack_id=...`. Se conserva la comparación de la ubicación original.
- Un único PUT al guardar, con el ID capturado al abrir y los campos modificados. No se guarda por tecla ni al cambiar un campo.
- La respuesta actualiza únicamente las entidades afectadas en las cachés, invalida las opciones mediante los helpers existentes y agrupa el redibujado del canvas. El guardado HTTP no llama a `loadLocations()` ni `loadCatalog()`. Los eventos WebSocket de ubicación conservan el resync original de la aplicación, como se explica en la revisión de regresión.
- Los eventos WebSocket actualizan las cachés, sin tener referencias a los controles del modal. Los campos laterales tampoco se usan como borrador.
- La implementación inicial retiró el resync después de los eventos de ubicación. La revisión de regresión detectó dos pruebas originales fallidas y un panel lateral desactualizado, por lo que se restauró la política original: un evento parcial invalida el checkpoint y solicita el snapshot completo, con la deduplicación de resync existente. No queda validada la afirmación anterior de cero snapshots después de guardar. Al reasignar ubicación/área se actualiza la versión de ambos racks afectados para emitir sus eventos y proteger sus datos derivados frente a respuestas antiguas.
- Sin sesión administrativa, la misma información permanece disponible en modo consulta; un cambio de sesión actualiza los permisos del modal. El servidor verifica la autorización de cada escritura.
- Las características personalizadas usan filas de nombre visible y valor, con botones para añadir y quitar; no requieren editar JSON.

## Verificación reproducible

Desde PowerShell, en la raíz del repositorio:

```powershell
$env:PYTHONPATH = "$PWD\tests;$PWD"
.\.venv\Scripts\python.exe -m unittest test_context_cell_editor test_catalog_optimization test_locations_optimization test_locations_client_cache test_phase1f_admin_safety test_runtime_incremental_websocket test_runtime_snapshot_handshake test_runtime_snapshot_orders test_dispatch_backend_baseline test_dispatch_frontend_optimization -v
node tests/test_context_cell_frontend.js
node --check static/app.js
node --check static/cell-editor.js
```

Las pruebas del endpoint usan SQLite temporal y mocks, incluidas dos conexiones concurrentes a un archivo temporal. Cubren autorización, guardado conjunto, rollback tras flush, creación transaccional de SIN_MATERIAL, omisión de campos, no-op sin escritura, exclusividad, reservas, órdenes activas, conflictos y eventos incrementales deduplicados.

La prueba JavaScript ejecuta el editor real con DOM y red controlados: cancelación sin escritura, ID estable, doble envío, borrador ante WebSocket/409, resultado ambiguo, descarte al cambiar rack, consulta sin permisos, características personalizadas, selección múltiple, Ctrl/Meta, modos de captura y arrastre. Las recargas globales están prohibidas por los mocks.

`python tests/preview_context_cell.py` ofrece además una revisión manual en `http://127.0.0.1:8766`, con los endpoints reales del editor y SQLite en memoria. No inicia la aplicación de producción ni sus workers. Solo admite un token ficticio y prohíbe obtener el cliente RCS. Se verificaron en navegador guardado conjunto, persistencia al reabrir, características personalizadas, navegación por teclado y cierre con Escape con retorno del foco.

## Alcance de la verificación

Resultado final: **87 pruebas Python aprobadas** (22 del endpoint del editor y 65 de regresión), prueba dinámica JavaScript ampliada para las dos vistas aprobada, comprobaciones de sintaxis Python/JavaScript y `git diff --check` sin errores. La revisión de navegador se realizó contra la base ficticia del fixture y el servidor de prueba se detuvo al finalizar.

Ese resultado corresponde a la verificación anterior, que no incluyó toda la suite original. La [revisión de regresión posterior](REVISION_REGRESION_EDITOR_CONTEXTUAL.md) compara la suite completa con Git, documenta la corrección del resync y añade la prueba del editor integrado con canvas y paneles reales sobre SQLite en memoria.

No se conectó al RCS real ni se abrió la aplicación contra `agv.db`. La prueba visual corresponde al modal aislado con los estilos reales; la integración con canvas, despacho y WebSocket se verifica mediante las pruebas automatizadas y las regresiones existentes. No sustituye una prueba operativa con robots en un entorno de ensayo autorizado.

## Archivos de la entrega

- `main.py`: endpoints, comparación transaccional y validación compartida de rack.
- `static/app.js`: permisos, ocultación de la oferta al seleccionar y actualización incremental sin snapshot por cada evento de ubicación.
- `static/cell-editor.js`: modal, borrador, características, guardado y manejo de conflictos.
- `static/index.html` y `static/styles.css`: controles, diálogo único y estilos adaptables.
- `tests/test_context_cell_editor.py`: 22 pruebas de endpoint y concurrencia, incluida la conservación de configuración con el payload simplificado.
- `tests/test_context_cell_frontend.js`: pruebas dinámicas de interacción y WebSocket.
- `tests/preview_context_cell.py`: fixture opcional para navegador con SQLite en memoria.
- `docs/EDITOR_CONTEXTUAL_CASILLA.md`: diagnóstico, arquitectura, peticiones y verificación.
