# Fase 2C: auditoría RCS sin credenciales

## Correlación

Cada operación recibe trace_id. Los registros RCS_AUDIT contienen timestamp UTC,
operación (create/query/cancel; debug cuando no hay MovementOrder), referencias
movement_order_id/Q1/T1, request_id propio de esa petición y resultado.
Cada intento HTTP agrega número de intento, endpoint saneado, método, payload
preparado sin secretos, estado HTTP y tipo de error. El resultado de create agrega
T1 cuando está disponible. Una consulta de lote identifica todas sus órdenes.
Las consultas generales de robots y operaciones libres no inventan una orden local.

El adaptador observa el PreparedRequest sin modificarlo. Retry.increment observa
los fallos y el hook de respuesta observa el último intento. Se evita contabilizar
dos veces una misma respuesta final. No se cambian retries, redirects, payloads,
criterios de aceptación ni clasificación de creación incierta. Cancel sigue sin retry.

## Persistencia y secretos

Las columnas de auditoría de órdenes, eventos y cambios de configuración se sanean
antes de persistir. También se sanean las vistas de datos históricos y la descarga
administrativa de logs. Se filtran estructuras anidadas, headers/cookies, JSON en
texto, errores/tracebacks, credenciales en URLs y ecos de credenciales conocidas.
Las credenciales operativas configuradas siguen siendo necesarias para el transporte;
no se usa una copia saneada para enviar el request real.

Los overrides operativos que contienen secretos se guardan cifrados mediante DPAPI
en la columna existente, ligados a la cuenta Windows. Solo se descifran para uso
operativo; las vistas muestran valores ocultos. Reguardar esa vista conserva el valor
oculto original. En plataformas sin DPAPI se rechaza guardar un override sensible
en lugar de persistirlo en texto plano. No se añadió esquema ni migración.
Cambiar de cuenta Windows requiere volver a configurar esos overrides.

## Retención acotada

- Consola: hasta 1.000 eventos globales.
- Por orden: hasta 20 entradas de creación/cancelación y 180 entradas recientes;
  estas incluyen trazas de transporte recientes de la operación.
- Logs rotativos existentes: 5 MiB por archivo, cinco respaldos. RCS_AUDIT se
  enruta a rcs.log y conserva trace_id para enlazar intentos y resultado.

La retención depende del volumen: no se promete un número fijo de días. La consola
no es la única fuente para reconstruir un incidente. Los JSON saneados son copias
de auditoría, no credenciales reutilizables ni instrucciones para reenviar una tarea.

## Límites

No se abrieron ni reescribieron datos/logs productivos antiguos. El filtrado de lectura
y descarga no elimina físicamente secretos que pudieran existir previamente en disco
o respaldos; su revisión, rotación de credenciales y purga necesitan un procedimiento
operativo autorizado. Los archivos originales deben conservar permisos restringidos.
La sanitización no puede identificar por semántica cualquier secreto desconocido que
un tercero devuelva sin etiqueta; se filtran etiquetas y valores conocidos, y no se
guardan headers/cookies completos en las trazas de transporte.
