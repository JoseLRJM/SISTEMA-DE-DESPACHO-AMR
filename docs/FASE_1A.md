# Fase 1A: identidad, observación y reserva

## Trazado anterior a la edición funcional

| Operación | Función/camino | Generación | Persistencia anterior | Sobrescritura |
| --- | --- | --- | --- | --- |
| Crear | main._movement_order_json_payload / _dispatch_movement_order | generate_req_code_ms, timestamp UTC de 17 dígitos | dispatch_request_json antes de POST; req_code después de respuesta | response.reqCode reemplaza req_code; response.data reemplaza remote_task_code |
| Editar JSON | _save_movement_order_payload_override | suministrado por usuario | override_payload_json y req_code | Sí, incluso tras envío |
| Reenviar | dispatch_movement_order | req_code actual o uno nuevo | reemplaza request/response e identidad remota | Sí; no comprueba intento anterior |
| Consulta automática | build_auto_status_query_payload / _record_status_query_for_order | timestamp nuevo por consulta/lote/fallback | status_query_request_json, status_query_response_json, status_query_log_json, req_code | Sí; también hay asignaciones en bloque antiguo inalcanzable |
| Consulta manual | run_manual_status_query | payload del usuario | mismos JSON de consulta y req_code, incluso excepción | Sí |
| Cancelar | _execute_cancel_for_order | timestamp nuevo | status_query_log_json y req_code | Sí; taskCode sale de remote_task_code |
| Cliente directo | RcsClient.query_task_status / cancel_task | UUID truncado / timestamp | respuesta devuelta al llamador | No escribe DB directamente |
| Simulador | SimulatedRcsClient | delega generador; taskCode UUID | memoria y luego despacho compartido | despacho compartido |
| Preview de ventana | _build_payload_from_cells / _preview_window_button | timestamp | no persiste identidad de creación | no |
| Otros mensajes RCS | rack sync (Q/B con sufijo), posición pod, robot status/control | generador compartido | eventos/JSON del módulo | no escribe identidad MovementOrder |
| Timeout/reinicio | _dispatch_movement_order / check_active_tasks | reenvío puede generar otro | timeout se compensa; monitor exige success y taskCode | request anterior no se usa para recuperar identidad |

Lectores: serializers de orden/respuesta, generador de payload, historial, frontend
y PDA. Ninguno necesita que req_code signifique la última consulta. El monitor y
cancelación usan remote_task_code, no req_code, para localizar la tarea física.
FIFO/Direct/QR/cadenas llegan al despachador compartido. No hay endpoint configurado
para localizar una creación por reqCode. Los únicos writes productivos de
remote_task_code están en el despachador; los otros son fixtures de orden local.

## Contrato y alcance

La configuración contiene URLs/endpoints y simulación, no un selector contractual
Lite/2000. La guía oficial Lite describe reqCode como String de máximo 32 caracteres,
identificador único, separado de reqTime. El compendio local usa valores alfanuméricos
y exige unicidad. No existe dependencia de timestamp en los lectores del proyecto.
Referencia: https://wiki-control.rms.hikrobotics.com/zh/AMRController/scheduling/RCS-Lite第三方接口配置操作指南

Se conservará un identificador de texto corto, sin migración. No se cambiará la
regla global de aceptación basada en mensaje/código ni el contrato de cancelación.
Las pruebas usarán un runner que bloquea agv.db y conexiones externas.

# FASE 1A — RESULTADO

## 1. Diagnóstico previo

La creación persistía su JSON antes del POST, pero no su req_code. Tras respuesta,
copiaba el reqCode devuelto. Consultas y cancelaciones sustituían ese campo. Un
reenvío podía crear otra tarea y reemplazar remote_task_code. Un error de transporte
se trataba como rechazo y la compensación liberaba el rack. El monitor podía
combinar el código solicitado con un estado ajeno y convertir errores de consulta
en failed. FIFO/Direct comprobaban disponibilidad sin adquisición atómica.

Reproducción del generador anterior: 1,000 llamadas rápidas a la expresión exacta
basada en milisegundos produjeron solamente 12 valores distintos en esta máquina.

## 2. Bugs confirmados

| ID | Archivo / función | Causa e impacto |
| --- | --- | --- |
| A1 | monitor_service.generate_req_code_ms | Resolución de milisegundos: colisiones entre solicitudes |
| A2 | main.run_manual_status_query, _execute_cancel_for_order; monitor_service._record_status_query_for_order | Q2/Q3 sobrescribían Q1; pérdida de identidad de creación |
| A3 | main._dispatch_movement_order, _save_movement_order_payload_override | Reenvío/edición sin protección: podían sustituir identidad o crear otra tarea |
| A4 | main._dispatch_movement_order, _rollback_unsuccessful_order_dispatch | Timeout convertido en rechazo: recurso liberado con resultado remoto desconocido |
| A5 | monitor_service._single_task_response_item; rcs_client._extract_task_status_items | Se fabricaba correlación solicitada + estado recibido, incluso de otra tarea |
| A6 | monitor_service._run_single_status_query, _run_status_query_batch_safely; main.run_manual_status_query | Error de observación interpretado como fallo físico |
| A7 | fifo_service.execute_direct_move_request, execute_fifo_request, execute_fifo_request_any_available | Lectura y reserva separadas; dos sesiones podían adquirir rack/destino |
| A8 | fifo_service._reserved_destination_cell_ids | Cancelaciones pendientes no protegían el destino en esta selección |

## 3. Cambios realizados

- **rcs_client.py:** generate_request_code usa 20 caracteres hexadecimales (80 bits
  aleatorios del sistema operativo). query_task_status/cancel_task usan el mismo
  generador. _extract_task_status_items conserva la identidad recibida, sin
  inventarla. create_task extrae códigos explícitos de texto/objeto y descarta
  identidades contradictorias; no convierte diccionarios arbitrarios en taskCode.
- **monitor_service.py:** se conserva el nombre público generate_req_code_ms.
  Las funciones de consulta ya no escriben req_code ni convierten errores de
  observación en failed. Se exige coincidencia explícita y respuesta válida.
  check_active_tasks incluye incertidumbres con taskCode conocido; una consulta
  válida permite reconciliarlas.
- **main.py:** _dispatch_movement_order adquiere el intento mediante UPDATE
  condicional y persiste Q1/JSON/uncertainty antes de enviar. No vuelve a enviar
  órdenes ya intentadas ni reemplaza T1. _save_movement_order_payload_override
  protege la identidad mediante actualización condicional. Se bloquea la
  reutilización de un Q1 de creación ya persistido por otra orden. Consultar y
  cancelar conservan Q1. La compensación compartida no libera incertidumbres.
  Los tres endpoints FIFO/Direct/ventana responden 409 con revisión requerida.
  La cancelación sin T1 del nuevo estado incierto no permite un deshacer local:
  es una salvaguarda del resultado desconocido, no un rediseño de cancel/return.
- **fifo_service.py:** _claim_movement_resources hace la adquisición atómica y
  revalida ubicaciones desde DB bajo la transacción de escritura. Las tres
  funciones de creación lo usan. Se incluyen cancelaciones pendientes/cancel_error.
  Los códigos locales DM/MO incorporan microsegundos para permitir repetir el
  mismo flujo dentro de un segundo sin chocar con el historial.
- **tests/test_phase1a_identity_resources.py:** regresiones A–J y casos adicionales.
- **tests/test_dispatch_backend_baseline.py:** expectativas actualizadas para la
  política de incertidumbre y para los contadores de persistencia: seis commits
  se conservan; seis flushes y siete refreshes por creación normal.
- **tests/run_phase1a_suite.py:** bloquea acceso a agv.db y conexiones externas;
  desactiva escritura de logs operativos. HTTP real se limita a loopback.
- **docs/FASE_1A.md:** trazado previo, evidencia y resultado. Sin migraciones,
  cambios de frontend, scanner_service o fifo_chain_service.

## 4. Modelo final de identidad

```text
MovementOrder
  ├─ creación Q1 → req_code + dispatch_request_json.reqCode (antes del POST)
  │    └─ retries HTTP del mismo intento → exactamente Q1 y mismo body
  ├─ respuesta inequívoca T1 → remote_task_code (no se reemplaza por reenvío)
  ├─ consulta Q2 + T1 → JSON de consulta + historial
  └─ cancelación Q3 + T1 → historial de cancelación
```

La unicidad aleatoria es probabilística, no una garantía matemática absoluta.
No depende de timestamp ni de contadores en memoria compartidos entre workers.
El bloqueo adicional de Q1 repetido cubre creaciones persistidas por esta versión.
No se migra ni reescribe retrospectivamente el historial existente.

## 5. Nuevo flujo de timeout

```text
Reservar rack/destino atómicamente
  ↓
Adquirir intento y guardar Q1 + body + uncertain (commit)
  ↓
POST / retries de transporte con idéntico body
  ├─ aceptación + identidad coherente + T1 → dispatched/success
  ├─ rechazo conforme a regla existente, sin T1 → compensación existente
  └─ timeout / transporte / identidad ambigua / respuesta sin T1
       ↓
       pending_dispatch + dispatch_status=uncertain + rcs_status=dispatch_unknown
       ↓
       rack y destino retenidos; no reenvío automático/manual de creación
       ├─ T1 conocido → queryTaskStatus → coincidencia válida → reconciliar
       └─ T1 desconocido → revisión manual en RCS con Q1 y JSON almacenado
```

Un reinicio no elimina el marcador. Un intento histórico con request almacenado
pero not_sent se trata conservadoramente como incierto al intentar reenviarlo.
No existe endpoint de búsqueda fiable por reqCode configurado en este cliente;
no se ha inventado uno ni usado un POST de creación como consulta.

## 6. Nuevo flujo del monitor

```text
Consultar T1 → conservar respuesta original
  ├─ error HTTP/transporte/JSON o respuesta no válida → registrar observación
  │                                                  sin estado terminal
  ├─ falta taskCode / taskCode distinto de T1 → warning, no aplicar
  └─ taskCode=T1 + respuesta válida + estado → aplicar a esa orden
```

La misma protección se aplica a consulta manual, lote y fallback individual.
Se verifica también la ruta positiva: completed de T1 sí finaliza su movimiento.
No se cambió la regla global de éxito Lite/RCS-2000 ni la de cancelación.

## 7. Protección de recursos

UPDATE condicional de Rack exige estado disponible y ausencia de órdenes activas
que lo referencien como rack principal, pickup o dropoff. La escritura serializa
competidores SQLite, incluidos otros procesos que usen estas funciones. Con la
transacción aún abierta se refrescan rack/origen/destino y se vuelve a consultar
la reserva de destino. El perdedor recibe conflicto y revierte su adquisición.
El commit se realiza junto con la nueva orden, no entre reserva y creación.

Se protegen pending_dispatch (incluye incertidumbre), dispatched, in_progress,
cancel_requested_total, cancel_requested_undo y cancel_error. Una orden terminal
no bloquea por su mera existencia histórica: la reutilización secuencial pasa.
No hay exclusividad histórica del rack ni migración de restricciones.

## 8. Pruebas agregadas

| Test | Resultado |
| --- | --- |
| A: creación → consultas manuales/monitor → cancelación → reenvío | PASS |
| B: 1,000 identificadores en 16 hilos | PASS |
| C: tres POST reales a servidor local; bytes/body/Q1 idénticos | PASS |
| D: timeout sin creación remota | PASS |
| E: creación en ledger remoto, timeout, nueva sesión sin duplicación | PASS |
| F: T200/completed y ausencia de taskCode, consultando otra tarea | PASS |
| G: query timeout y error con estado failed; recurso sigue activo | PASS |
| H: dos sesiones Direct mismo rack, Direct mismo destino y FIFO concurrente | PASS |
| I: terminar movimiento y reutilizar rack en el siguiente tramo del flujo | PASS |
| J: cancelaciones pendientes y cancel_error, incluso rack local stale available | PASS |

Adicionales: dos despachadores simultáneos producen un solo POST; aceptación con
reqCode ajeno o sin taskCode retiene recursos; JSON intentado no editable;
reconciliación con T1; intento histórico interrumpido no reenviado; estado terminal
con identidad exacta sí aplicado; parser no inventa taskCode desde objetos;
reutilización manual de Q1 bloqueada.

## 9. Suite existente

Comando seguro: `py -3.11 -B tests/run_phase1a_suite.py`.

- Antes: 63 pruebas, todas correctas.
- Después: 79 pruebas, todas correctas (63 existentes + 16 nuevas).
- Regresiones observadas: ninguna en la suite final. Las expectativas antiguas
  que exigían liberar recursos por timeout se actualizaron deliberadamente;
  los payloads y persistencia de éxito FIFO, Direct y ventana siguen comprobados.
- Las pruebas no arrancan el servicio, no envían tareas al RCS real ni abren
  agv.db. La concurrencia usa SQLite temporal y conexiones independientes.

## 10. Riesgos pendientes de esta fase

1. Sin T1, la reconciliación requiere inspección manual del RCS; no se puede
   prometer cierre automático ni tiempo máximo de retención.
2. No se valida el firmware instalado contra robots reales. Configuraciones que
   devuelvan estados sin identidad quedan retenidas conservadoramente; necesitan
   acreditar un contrato inequívoco antes de permitir correlación implícita.
3. La garantía de adquisición aplica a FIFO/Direct y sus llamadores compartidos
   sobre SQLite. Escrituras externas que omitan esas funciones o cambien estados
   a mano no quedan impedidas por una restricción global de DB.
4. No hay backfill de identidades históricamente sobrescritas. Cuando existe,
   dispatch_request_json conserva el Q1 histórico; no se reenvía por recuperarlo.
5. La idempotencia remota de Q1 depende del RCS: se demostró estabilidad del body,
   no el comportamiento del servidor productivo. La aplicación no inicia una
   segunda creación lógica para resolver incertidumbre.

Resultado: criterios A–J demostrados en entorno aislado. Aprobación técnica local
de Fase 1A; no equivale a certificación de todos los firmwares ni a despliegue.
