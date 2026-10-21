# Revisión local de regresión del editor contextual

Fecha: 21 de octubre de 2026. Esta revisión es local y aislada; no valida operación con robots.

## Referencias y límites de la comparación

Se revisaron `git status`, `git log`, `git reflog`, `git diff` y los archivos nuevos no registrados. No se encontró `AGENTS.md` en el proyecto. Se conservaron los cambios locales y el ZIP ya existente.

La base comparada es **`c023b5ec4ae7fb2bb36b6d4dd267dc5332cb7101`**, último commit de `main`/`origin/main`, del 11 de septiembre de 2026. Se exportó con `git archive` a una carpeta temporal y se ejecutó allí su suite completa. Se verificó que ese árbol no contiene el editor contextual. La conversación anterior también registra que los archivos versionados estaban limpios en esa revisión antes de implementar el editor; no se eligió HEAD solo por su nombre.

El editor inicial y las dos vistas siguen como cambios locales sin commits intermedios. Por tanto, **no hay una referencia Git completa inmediatamente anterior a la ampliación de vistas**. Las huellas SHA-256 registradas antes y después de esa ampliación sí coinciden para `main.py` (`B70CAEB2376D2C9F801E136395CE4D51620D39657B3EAC4A7E92D4C7CBBA667B`) y `static/app.js` (`45C9C604F718A4C3ECA6A46B40CA76DC69280CD7C4C44CDEF1659E4C946275AD`). Son evidencia de sesión para esos archivos, no un sustituto de una revisión Git del HTML, CSS y JS iniciales. No se puede demostrar equivalencia completa entre ambas etapas a partir de Git.

| Archivo / zona | Implementación inicial | Ampliación Usuario/Admin | Esta revisión |
|---|---|---|---|
| `main.py` | Endpoint GET/PUT, huellas, transacción y validador compartido; cambió también `update_rack()` legado y el helper SIN_MATERIAL | Huella sin cambios | No se modificó |
| `static/app.js` | Hooks del modal en permisos/selección y retirada del resync parcial; este último afectaba a toda la aplicación | Huella sin cambios | Se restauraron las tres líneas originales del resync |
| `static/cell-editor.js` | Modal, borrador, selección de rack, guardado y conflictos | Vistas, listas permitidas y resolución de borradores | No se modificó |
| HTML/CSS | Diálogo único y botón de acceso desde panel/canvas | Botones de vista, referencias y campos administrativos ocultables | No se modificaron |
| Pruebas/documentación | Casos nuevos del editor | Cobertura de vistas | Pruebas de regresión, fixture integrado y este informe |

La separación de funcionalidades en los archivos compartidos se apoya en el código y el registro de la tarea; no existe un diff Git independiente de la segunda etapa. El diff final contra la base ya no contiene el cambio de WebSocket que se corrigió en esta revisión. Los demás archivos versionados —modelos, PDA, QR, FIFO, RCS, cancelación, canvas y handlers originales— no presentan cambios, salvo las zonas indicadas de `main.py` y `app.js`.

## Hallazgos

### R1 — Regresión confirmada y corregida: eliminación del resync de ubicación

**Archivo y función:** `static/app.js`, `applyRuntimeIncrementalMessage()`; efecto visible en `applyRuntimeSnapshotData()` y `fillCellForm()`.

La implementación inicial eliminó la solicitud de snapshot completo al dejar `locationsRevision === null` tras un evento `location_updated` o `location_deleted`. La secuencia global de eventos no reemplaza el contrato original de checkpoint completo ni la actualización de las vistas que hacía el snapshot.

Evidencia automatizada antes de corregir:

- Base Git: **274 pruebas, todas aprobadas**.
- Versión recibida: **296 pruebas, dos fallos** originales de `test_phase2b_runtime.CacheCheckpointTests`: `test_partial_event_never_confirms_global_revision` y `test_multiple_deletions_need_complete_snapshot`.
- Se mantuvieron intactas esas pruebas originales.

Evidencia en navegador integrado: se seleccionó `(0, 0)` en el panel. Otra sesión del fixture cambió su código a `REMOTE-BEFORE-FIX`; llegó el evento sin error de consola, pero el panel siguió mostrando `TEST-0-0`. Tras restaurar la política y recargar el JS, el mismo cambio remoto a `REMOTE-AFTER-FIX` actualizó el panel sin reseleccionar la casilla.

**Corrección mínima:** restaurar exactamente el `if` original que llama a `requestRuntimeResync('Locations parcial: se requiere snapshot completo')`. No se añadió otro algoritmo, polling o política de caché. Se ajustó la prueba nueva del editor que asumía equivocadamente cero resync; las pruebas originales no se relajaron.

Consecuencia explícita: el guardado del modal sigue sin llamar directamente a `loadLocations()`, `loadCatalog()` ni al endpoint HTTP de snapshot. Cambiar de vista no hace peticiones. Sin embargo, un evento de ubicación vuelve a solicitar el snapshot completo preexistente, con la deduplicación original. **No se certifica cero snapshots después de guardar**. Suprimirlos preservando toda la semántica original requeriría otro cambio de diseño y queda fuera de esta revisión.

### R2 — Diferencia de validación introducida inicialmente; no equivale a comportamiento idéntico

**Archivo y funciones:** `main.py`, `_validated_rack_values()` / `update_rack()`.

Para un código formado solo por espacios, el endpoint legado de la base hace commit del código vacío y después falla al construir `RackOut` con `ValidationError`. La versión actual devuelve 400 antes de escribir. Una prueba diferencial ejecuta ambos cuerpos de función y comprueba también la base temporal. Es un endurecimiento observable sobre una entrada inválida; no se restauró el defecto de datos parciales para declarar equivalencia. El frontend legado ya envía el código recortado y su cadena vacía es rechazada por `RackIn`.

Los casos diferenciales de guardado normal, reserva, SIN_MATERIAL, material inexistente/inactivo, código duplicado y protección por orden activa coinciden con la base. La comparación del cuerpo legado usa los helpers actuales; por eso esta evidencia es específica a esos escenarios, no equivalencia formal de todo el backend.

### R3 — Alcance y límites que permanecen

Las dos vistas usan el mismo rol y endpoint administrativos; sus controles no son una barrera de seguridad. El servidor mantiene `require_admin()`, `BEGIN IMMEDIATE`, huellas y validaciones. El guardado conjunto atómico corresponde al endpoint nuevo del modal; los guardados laterales originales siguen sus peticiones independientes y no se afirma que sean atómicos entre sí.

No se encontró una nueva interferencia en las vistas o selección cubierta. Eso no demuestra ausencia de todo defecto. No hay una versión Git intermedia del modal inicial, y la comprobación nativa no cubrió un dispositivo PDA, gestos táctiles, Meta en macOS, arrastre nativo con botón derecho ni todas las pantallas configurables de captura de puntos. Las rutas y handlers correspondientes sí tienen revisión estática y/o pruebas automatizadas indicadas abajo.

## Ejecución segura

Se crearon dos copias temporales de código: una exportada de Git y otra con el árbol de trabajo completo relevante, incluidos los archivos nuevos. No se copiaron ni abrieron bases de producción ni entornos virtuales. Se utilizó el intérprete existente del proyecto.

`tests/run_phase1a_suite.py` instala un audit hook antes de importar la aplicación: prohíbe cualquier conexión SQLite cuyo nombre contenga `agv.db` y conexiones externas de socket. Se ejecutó desde las copias temporales. Las pruebas usan SQLite en memoria/archivos temporales y mocks; no envían tareas al RCS real.

Para navegador se añadió `tests/preview_integrated_editor.py`, distinto del fixture de modal aislado. Sirve el **HTML, canvas, paneles, JS, endpoints de inventario, autenticación y WebSocket reales** mediante otra instancia FastAPI. No instala el startup de producción ni su middleware de lease: no prueba el arranque de producción. Inyecta SQLite en memoria en los módulos cargados, instala los hooks reales de eventos de sesión y solo inicia el broadcaster WebSocket local. Los arranques de monitores/scheduler y el cliente RCS están sustituidos por funciones que fallan si se invocan. Otro audit hook bloquea `agv.db` y redes externas. El servidor escucha únicamente en `127.0.0.1:8767`.

El acceso del fixture usó una contraseña exclusiva de prueba. Los controles `/__review__/...` solo existen en ese archivo de prueba; no se añadieron al producto. El servidor y la pestaña de prueba se cerraron al terminar.

## Pruebas automatizadas y comandos

Se usó `C:\Users\DEV2_\AppData\Local\Temp\amr-regression-0a1e1e03dfb241a18f61c22998788980` como raíz aislada. Los comandos siguientes se ejecutaron con el directorio de trabajo indicado:

```powershell
# En baseline/, exportado de c023b5ec4ae7fb2bb36b6d4dd267dc5332cb7101:
& $python tests/run_phase1a_suite.py

# En current/, copia aislada con los cambios locales:
& $python tests/run_phase1a_suite.py
node tests/test_context_cell_frontend.js

# Bloques añadidos/afectados, también en current/:
$env:AMR_REGRESSION_BASELINE_MAIN = "$auditRoot\baseline\main.py"
$env:PYTHONPATH = "$auditRoot\current\tests;$auditRoot\current"
& $python -m unittest test_editor_regression test_phase2b_runtime -v
& $python -m unittest test_editor_regression -v

# Reejecución completa tras la corrección y las pruebas adicionales:
$env:AMR_REGRESSION_BASELINE_MAIN = "$auditRoot\baseline\main.py"
& $python tests/run_phase1a_suite.py
node --check static/app.js
node --check static/cell-editor.js
```

`$python` fue `C:\Users\Yael AGVAPP\SISTEMA-DE-DESPACHO-AMR-main\.venv\Scripts\python.exe`. `$auditRoot` representa la raíz temporal anterior. La variable de baseline es necesaria para las pruebas diferenciales nuevas; sin ella, esos casos se marcan omitidos, no aprobados.

Resultados: base **274/274**; recibida **294/296**, con los dos fallos R1; bloque afectado tras corregir **28/28**; bloque ampliado **12/12**. La suite completa después de la primera ampliación pasó **307/307** y la reejecución final pasó **308/308 en 132,004 segundos**, incluyendo pan/zoom. La suite JS también pasó; ejecuta el editor real con DOM/red controlados: vistas, filtros del payload, borradores, sesión, doble envío y resultados ambiguos. Se añadió un ciclo de **40 aperturas/cierres** que conserva el número de listeners y solo realiza un GET por apertura. Las comprobaciones `node --check`, compilación Python y `git diff --check` no encontraron errores.

Las suites completas incluyen catálogo/ubicaciones, identidad y recursos, QR/PDA y transiciones, reconciliación, FIFO Chain, cancelación, administración segura, fallos antes del envío, errores/ambigüedad del despacho, observabilidad, caché, handshake, reconexión y checkpoints. No se sustituyeron reglas de negocio por mocks permisivos para conseguir que pasaran; se conservaron las expectativas originales.

## Comprobación separada en navegador

Pruebas realizadas en la aplicación integrada, no solo en el modal aislado:

1. Inicio de sesión, restauración de sesión tras recargar y conexión WebSocket real.
2. Reproducción del panel desactualizado antes de R1 y refresco correcto tras corregirlo.
3. Selección normal en canvas: selecciona sin abrir el modal y ofrece la acción. Ctrl sobre dos casillas: selección múltiple, formulario masivo y oferta oculta.
4. Modal abierto desde el canvas sobre una casilla vacía: reserva/lote/material deshabilitados y explicación; seleccionar `TEST-R2` carga su lote `LOTE-R2`, no los datos de otro rack. Guardar asignación deja rack `2`, estado ocupado `1` y código original en el panel.
5. Borrador `BORRADOR-EN-VIVO` permanece al recibir un cambio remoto de nota y su snapshot. Guardar con huella anterior devuelve 409 y conserva el borrador. El panel exterior sí recibe la nota remota.
6. Guardado lateral legado: nota `PANEL-LATERAL-OK` y mensaje de éxito, sin utilizar el nuevo endpoint del modal.
7. Captura original de origen `(1, 0)` y destino `(2, 0)`: etiquetas correctas, modal cerrado y oferta de edición oculta. No se pulsó crear/despachar orden.
8. Layout libre: arrastre de `(2, 0)`, mensaje de posición guardada y PATCH real sobre la base ficticia (`free_enabled=1`, `free_x≈52.0685`, `free_y≈6.1028`). Finalizar el arrastre no abre el modal ni ofrece editar. Zoom real por rueda: `2.46x → 2.70x`.
9. Bloquear Admin desde el control original; abrir desde el canvas en consulta: Admin oculto, Guardar deshabilitado y cero controles de datos habilitados.
10. Alternancia Usuario → Admin → Usuario: el contador real del servidor no registra nuevas peticiones (se excluye únicamente el GET de evidencia). PUT del editor sin token: **401**, nota previa conservada.

La pérdida de sesión con borradores administrativos, la confirmación de descarte/cancelación, Meta, el doble envío y la respuesta de red ambigua se comprobaron con la suite JS; no se presenta esa simulación como una prueba de expiración real en navegador.

## Matriz de evidencia y pendientes

| Flujo | Evidencia | Resultado local | Pendiente / límite |
|---|---|---|---|
| Selección normal/programática | Diff del handler, suite JS, canvas real y selección inicial tras reload | Conservada en los escenarios revisados | No todas las rutas programáticas configurables se recorrieron en navegador |
| Múltiple, Ctrl/Meta | Ctrl nativo; suite JS con Ctrl/Meta y no apertura implícita | Conforme | Meta nativo en macOS fuera del entorno Windows |
| Zoom y desplazamiento | Rueda real; callbacks originales de pan medio/derecho y zoom ejecutados en Node | Conforme | Pan nativo prolongado y táctil no recorridos |
| Arrastre libre | Navegador, PATCH temporal y oferta oculta | Conforme | Interacción táctil no probada |
| Captura origen/destino/puntos | Captura real de origen/destino; flags de puntos en JS; handlers sin cambios | Sin interferencia observada | Pantallas configurables de puntos no recorridas de extremo a extremo |
| Paneles y guardado lateral | Navegador + comparación diferencial de `update_rack()` | Regresión de refresco corregida; guardado conservado | Los guardados laterales siguen siendo independientes |
| Exclusividad de rack | SQLite temporal y conflicto de asignación; selector integrado | Conforme | No se afirma equivalencia formal con toda secuencia posible |
| Material, SIN_MATERIAL e inactivos | Suites de catálogo/editor y pruebas diferenciales | Conforme en casos válidos/rechazos cubiertos | Caso inválido de espacios difiere deliberadamente (R2) |
| Reserva y órdenes activas | Editor/seguridad/identidad; guardas existentes | Conforme con mocks y SQLite temporal | Sin recuperación operativa de robots |
| Creación/despacho/errores | Suites originales de backend, preenvío, idempotencia, timeout y cancelación | Aprobadas con RCS simulado | RCS y robots reales excluidos |
| PDA, QR, FIFO, cancelación | Suites originales completas y diff de archivos | Aprobadas localmente | Dispositivo PDA y flujos físicos excluidos |
| Login/cierre/permisos | Navegador real + PUT sin token 401 + suite de pérdida de sesión | Conforme en escenarios cubiertos | Expiración por reloj con modal abierto no inducida en navegador |
| Usuario/Admin y payload | Suite JS, contador HTTP real, referencia/controles integrados | Conforme; cero peticiones al alternar | Sin revisión Git intermedia completa |
| Cambio de rack/borrador/cancelación | Suite JS + rack vacío/selección real + 409 integrado | Conforme en casos cubiertos | Confirmaciones de todas las combinaciones no recorridas nativamente |
| Transacción/rollback/huellas | Suite del editor; dos escritores SQLite y fallo tras flush | Conforme | No se ensayó corte físico del proceso durante commit |
| WebSocket orden/duplicados/antiguos/saltos | Pruebas existentes y nuevas con handlers reales | Conforme tras restaurar R1 | Se conserva el resync original; cero snapshots no certificado |
| Desconexión/reconexión/caché parcial | `CacheCheckpointTests`, handshake y navegador tras reload | Conforme | Pérdida física de red/energía no ensayada |
| HTTP antes/después de WebSocket | Pruebas con caches/índices reales y timestamps | Última versión/ubicación/racks coherentes | No se simuló toda latencia posible |
| WebSocket durante edición | Borrador y conflicto 409 en navegador integrado + JS | Borrador conservado | — |
| Listeners/reenvío/recargas | 40 ciclos JS; guardas busy y resultado ambiguo; contadores HTTP | Sin listeners adicionales ni reintento automático | Resync de ubicación original activo por R1 |

## Entrega y evidencia

La única corrección del producto en esta revisión es el bloque de resync en `static/app.js`. Se añadieron `tests/test_editor_regression.py` y el fixture `tests/preview_integrated_editor.py`, se amplió la prueba JS existente y se corrigió la documentación de la política WebSocket. No se hicieron refactors ni cambios de esquema, reglas de despacho, RCS o PDA.

Se entregan logs de baseline/versión recibida/corrección, contadores de red y el diff final en `evidencia_regresion_editor/`. La captura del conflicto integrado muestra el canvas y el panel detrás del modal, el borrador y el 409; es evidencia visual complementaria, no prueba de operación con robots.

- [Suite base: 274 aprobadas](evidencia_regresion_editor/01-base-274.txt).
- [Versión recibida: dos regresiones](evidencia_regresion_editor/02-recibida-296-dos-fallos.txt).
- [Suite final: 308 aprobadas](evidencia_regresion_editor/04-final-308.txt).
- [Suite dinámica del editor](evidencia_regresion_editor/05-frontend.txt).
- [Contadores HTTP al alternar vistas](evidencia_regresion_editor/contador_vistas.json).
- [Diff de archivos versionados contra c023b5e](evidencia_regresion_editor/diff-versionados-contra-c023b5e.patch); los archivos nuevos se entregan por separado y figuran en [huellas de la entrega](evidencia_regresion_editor/huellas-entrega.json).
- [Comparación de módulos originales sin cambios](evidencia_regresion_editor/modulos-sin-cambios.json), normalizando CRLF/LF: el checkout Windows y `git archive` usan finales de línea distintos; se conservan ambas huellas de bytes para explicarlo.
- [Captura del 409 con borrador conservado](C:/Users/DEV2_/.codex/visualizations/2026/10/21/01a15f30-6409-7dd0-8a67-600a463e6bcf/regresion-editor-conflicto.png).
