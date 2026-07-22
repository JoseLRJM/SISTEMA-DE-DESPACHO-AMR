# Guía rápida de configuración QR, PDA y FIFO Chain

## 1. Objetivo de la guía

Esta guía explica cómo configurar correctamente:

- Códigos QR.
- Prioridades entre QR y Scanner/Perfil.
- Cadenas FIFO de 2, 3 y 4 tramos.
- Materiales y origen de cada tramo.
- Transiciones de material o estado.
- Terminales PDA para pruebas y operación.

Está dirigida a administradores, integradores y soporte técnico.

## 2. Qué pestaña se usa para cada cosa

| Pestaña | Para qué sirve |
|---|---|
| **Códigos QR configurados** | Es la configuración principal. Define qué hace cada QR. Sus valores tienen prioridad. |
| **Estaciones / Escáneres** | Define el Scanner/Perfil base: valores por defecto, permisos y asociación con PDA. No sobrescribe valores explícitos del QR. |
| **Terminales PDA** | Define qué dispositivo puede escanear, qué perfil usa y si puede ejecutar. |
| **Transiciones** | Cambia material, estado u otros datos del rack cuando la orden queda completada. |
| **Historial de escaneos** | Permite revisar qué QR se leyó, desde qué terminal, el resultado y los errores. |

En modo operador, la PDA siempre sigue este flujo:

**Escaneo → Preview interno → Confirmación → Ejecución**

El modal presenta datos operativos: QR leído, tramo, material, origen, destino y una recomendación comprensible. No muestra datos técnicos como Acción, Terminal, Scanner, Modo o JSON.

Las transiciones no se aplican durante el preview ni al crear la orden. Se aplican al completar el movimiento.

## 3. Prioridad de configuración

| Campo | Prioridad | Nota |
|---|---|---|
| Acción | QR > Scanner/Perfil | Revisar primero la regla QR exacta. |
| Origen y destino | QR > Scanner/Perfil | El QR manda cuando tiene un valor explícito. |
| AGV y Task type | QR > Scanner/Perfil | El perfil funciona como fallback. |
| Total de tramos | QR > Scanner/Perfil > 2 | Solo admite 2, 3 o 4. |
| Material tramo 1 | QR > Scanner/Perfil > error | Siempre es obligatorio. |
| Material tramo 2/3/4 | QR > Scanner/Perfil | Es obligatorio con “Cualquier área por material”. |
| Material asociado general | No aplica en `fifo_chain` | No usarlo para definir materiales de la cadena. |
| Rack asociado general | No aplica en `fifo_chain` | La selección se realiza por tramo. |

> **Importante:** en `fifo_chain` no use **Material asociado** ni **Rack asociado** general. Use **Material requerido tramo 1, 2, 3 y 4**.

## 4. Cómo configurar un QR fifo_chain

1. Abra **Códigos QR configurados**.
2. Cree un QR o edite uno existente.
3. Capture el valor exacto que lee el dispositivo.
4. Seleccione `route_mode = fifo_chain`.
5. Seleccione la cantidad de tramos: 2, 3 o 4.
6. Configure el tramo 1:
   - Material requerido tramo 1.
   - Origen.
   - Destino.
7. Configure cada tramo adicional:
   - Tipo de origen: configurado o cualquier área por material.
   - Origen, cuando corresponda.
   - Material requerido o filtro de material.
   - Destino.
8. Revise AGV y Task type si la operación los necesita.
9. Guarde el QR.
10. Ejecute un preview.
11. Pruebe desde una PDA autorizada.

La ejecución inicial crea solamente el tramo 1. El tramo 2 se crea cuando el primero queda completado; el 3 y el 4 siguen la misma regla. Nunca se crea un tramo 5.

### Ejemplo de 2 tramos

| Tramo | Origen | Destino |
|---:|---|---|
| 1 | A-2-AMR1 | ALMACEN |
| 2 | ALMACEN | A-2-AMR1 |

### Ejemplo de 4 tramos

| Tramo | Origen | Destino |
|---:|---|---|
| 1 | A-2-AMR1 | ALMACEN |
| 2 | ALMACEN | A-2-AMR1 |
| 3 | A-1-AMR1 | ALMACEN |
| 4 | Cualquier área con AMR1 | A-1-AMR1 |

## 5. Materiales por tramo

En `fifo_chain`:

- **Material requerido tramo 1** siempre es obligatorio.
- **Material asociado general** no se usa.
- **Rack asociado general** no se usa.
- En tramos 2, 3 y 4 con origen configurado, el material puede utilizarse como filtro.
- En tramos 2, 3 y 4 con cualquier área por material, el material es obligatorio.

### Configuración correcta

- Tramo 1: material `AMR1`.
- Tramo 2: material `VACÍO`.
- Tramo 3: material `CAJAS`.

### Configuración incorrecta

Capturar `AMR1` en **Material asociado general** y dejar vacío **Material requerido tramo 1**. El valor general no reemplaza al material del tramo.

## 6. Origen configurado vs cualquier área por material

| Modo | Qué hace | Cuándo usarlo |
|---|---|---|
| `configured_area` | Busca el rack en el origen configurado. | Cuando se conoce el área exacta de salida. |
| `any_area_by_material` | Ignora el origen configurado y busca un rack con el material requerido en cualquier área operativa elegible. | Cuando importa el material, no su ubicación actual. |

Con `configured_area` se deben definir origen y destino. El material opcional puede restringir la selección.

Con `any_area_by_material` se deben definir material y destino. El origen real será la ubicación del rack encontrado.

### Ejemplo

Para que el tramo 4 utilice cualquier rack `AMR1` disponible:

- Origen tramo 4: **Cualquier área por material**.
- Material tramo 4: `AMR1`.
- Destino tramo 4: `A-1-AMR1`.

## 7. Preview proyectado

El preview de `fifo_chain` simula los tramos en orden. No modifica la base de datos, no crea órdenes, no reserva racks y no despacha tareas.

Ejemplo:

- Tramo 1: `A-2-AMR1 → ALMACEN`.
- Tramo 2: `ALMACEN → A-2-AMR1`.

Aunque `A-2-AMR1` esté ocupado al inicio, el preview puede aceptar la secuencia porque el tramo 1 libera ese destino antes del tramo 2.

Cada tramo real se revalida cuando se crea. Si posteriormente cambia el rack, el material o la capacidad del destino, el siguiente tramo puede no crearse aunque el preview inicial haya sido correcto.

Si el destino está ocupado y ningún tramo anterior lo libera, el preview debe marcar error. Si el destino será liberado por un tramo anterior, el preview puede permitirlo y mostrar una nota informativa.

## 8. Transiciones

Las transiciones cambian el material o estado del rack cuando una orden queda completada.

El orden es:

1. RCS completa la tarea.
2. La aplicación mueve localmente el rack.
3. La aplicación aplica la transición.
4. Si es `fifo_chain`, crea el siguiente tramo.

La transición ocurre antes del siguiente tramo. Por eso el tramo siguiente puede buscar el material nuevo.

Si una transición no aplica, el siguiente tramo puede buscar un material que todavía no existe. Por eso conviene revisar que origen, destino, material actual, estado actual y prioridad coincidan con la regla.

### Ejemplo

- Tramo 1: `A-2-AMR1 → ALMACEN`.
- Transición: al llegar a `ALMACEN`, cambiar material a `VACÍO`.
- Tramo 2: `ALMACEN → A-2-AMR1`, filtrando material `VACÍO`.

### Modos de transición

- **Simple por origen/destino:** recomendado para ciclos sencillos. Al completar un movimiento de X a Y, aplica el material o estado configurado.
- **Avanzado:** úselo cuando necesite filtrar además por QR, Scanner/Perfil, material actual o estado actual.

Las reglas deben estar activas. Si varias podrían coincidir, revise su especificidad y prioridad.

## 9. Errores comunes y cómo corregirlos

| Error o confusión | Causa probable | Cómo corregirlo |
|---|---|---|
| No permite guardar porque falta material. | Falta Material requerido tramo 1. | Configure el material del tramo 1, no Material asociado general. |
| No encuentra material en origen. | El material está en otra área o el origen está invertido. | Corrija el origen o use `any_area_by_material`. |
| El destino aparece ocupado. | Está ocupado y ningún tramo anterior lo libera. | Libere el destino o corrija la secuencia. |
| El preview permite un destino ocupado. | Un tramo anterior lo libera en la proyección. | No es necesariamente un error; revise el orden proyectado. |
| No se crea el siguiente tramo. | El anterior no está completado, falta rack/material o cambió el destino. | Revise Historial, estado de la tarea y diagnóstico PDA. |
| La transición no cambia el material. | No coincide origen, destino, material o estado; o la regla está inactiva. | Revise filtros, estado activo y prioridad de la transición. |
| La PDA solo muestra error. | Falló el preview interno. | Revise material, origen, destino y ubicaciones indicadas en el diagnóstico. |
| El QR hace algo diferente a lo esperado. | El QR tiene prioridad sobre el perfil. | Revise primero la regla del valor QR exacto. |
| La terminal PDA no ejecuta. | La terminal o su perfil deniegan ejecución. | Verifique terminal activa, `allow_execute` y perfil asociado. |
| Se confundió `double_area` con `fifo_chain`. | `double_area` es una orden multipunto; `fifo_chain` crea varias órdenes. | Use `fifo_chain` cuando cada paso deba esperar que el anterior se complete. |
| El siguiente tramo busca material incorrecto. | La transición no cambió el material esperado o el material del tramo está mal configurado. | Revise la transición y el Material requerido del tramo siguiente. |
| El sentido del movimiento está invertido. | El rack con el material está realmente en el destino configurado, no en el origen. | Invierta origen/destino o use `any_area_by_material` si el origen debe resolverse automáticamente. |

## 10. Checklist antes de probar con PDA/RCS

- [ ] El valor exacto del QR está configurado.
- [ ] El `route_mode` es correcto.
- [ ] La cantidad de tramos es correcta.
- [ ] Material requerido tramo 1 está configurado.
- [ ] Los tramos con `any_area_by_material` tienen material configurado.
- [ ] Origen y destino de cada tramo están revisados.
- [ ] El sentido de cada tramo está revisado: origen → destino.
- [ ] Las áreas tienen ubicaciones visibles y habilitadas.
- [ ] Existe un rack disponible con el material requerido.
- [ ] Existe espacio en cada destino.
- [ ] Las transiciones necesarias están activas y sus filtros coinciden.
- [ ] Las transiciones coinciden con origen, destino, material actual y estado actual.
- [ ] La terminal PDA está activa y permite ejecutar.
- [ ] El Scanner/Perfil asociado permite ejecutar.
- [ ] El preview termina correctamente.
- [ ] El modo operador muestra la confirmación antes de ejecutar.

> Un preview correcto no reserva recursos. Confirme nuevamente el estado físico y operativo antes de una prueba real.
