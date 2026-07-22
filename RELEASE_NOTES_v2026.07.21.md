# v2026.07.21 - Mejoras UI, sesión admin y FIFO Chain

Esta versión consolida las mejoras funcionales y correcciones realizadas durante julio de 2026.

## Cambios principales

- Soporte completo de `fifo_chain` de 2, 3 y 4 tramos.
- Selección dinámica de celdas para pasos posteriores cuando solo se configura el área.
- Revalidación de racks, materiales y destinos al crear cada tramo real.
- Preview proyectado secuencial para destinos liberados por pasos anteriores.
- Contexto QR/Scanner heredado en transiciones de pasos encadenados.
- Transiciones aplicadas antes de crear el siguiente tramo y nuevos logs de diagnóstico.
- Mensajes operativos más claros para PDA y previews.
- Flujo PDA operador con preview, confirmación y ejecución.
- Corrección de sesión administrativa y restauración completa de datos por pestaña.
- Mejoras en configuración de QR, scanners, terminales y transiciones.
- Guía rápida de configuración incluida en `docs/`.

## Alcance de seguridad

La publicación excluye bases de datos, logs, entornos virtuales, respaldos y archivos ZIP locales.
