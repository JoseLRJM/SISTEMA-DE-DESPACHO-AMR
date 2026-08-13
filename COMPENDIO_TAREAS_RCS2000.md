# Compendio de tareas RCS-2000

Este compendio define el formato para enviar tareas al RCS-2000 usando plantillas ya configuradas en el RCS.

En esta etapa no se envia AGV, rack ni ubicaciones, porque las rutas, estaciones y posiciones ya estan fijas dentro del RCS-2000.

## Configuracion general

Base URL:

```text
http://IP_DEL_RCS:8182
```

Endpoint:

```text
/rcms/services/rest/hikRpcService/genAgvSchedulingTask
```

Metodo:

```text
POST
```

Content-Type:

```text
application/json
```

## Regla de nombres

Los nombres de plantilla deben ser cortos e intuitivos:

```text
MAXIMO 8 LETRAS + 2 NUMEROS
```

El numero final de 2 digitos depende de la estacion de entrega.

Ejemplos:

```text
01 = estacion de entrega 1
02 = estacion de entrega 2
03 = estacion de entrega 3
```

Por ejemplo:

```text
CAMPANA01
CAMPANA02
ENSAMBLE01
CUERCAMP01
```

## Campos que van vacios

Como las tareas ya estan configuradas dentro del RCS, estos campos deben ir vacios:

```json
{
  "agvCode": "",
  "podCode": "",
  "positionCodePath": []
}
```

No se debe forzar AGV, rack ni ubicaciones desde el sistema de pruebas.

## 1. Mapa Campanas

Nombre corto:

```text
CAMPANA01
```

Formato:

```text
CAMPANAXX
```

Donde `XX` es la estacion de entrega en 2 digitos.

Ejemplos:

```text
CAMPANA01
CAMPANA02
CAMPANA03
```

JSON ejemplo:

```json
{
  "reqCode": "REQ_CAMPANA01_001",
  "reqTime": "2026-08-10 12:00:00",
  "clientCode": "",
  "tokenCode": "",
  "agvCode": "",
  "taskCode": "",
  "taskTyp": "CAMPANA01",
  "priority": "",
  "wbCode": "",
  "ctnrCode": "",
  "ctnrTyp": "",
  "podCode": "",
  "podTyp": "",
  "podDir": "",
  "materialLot": "",
  "positionCodePath": [],
  "data": ""
}
```

## 2. Mapa Campanas Ensamble

Nombre corto:

```text
ENSAMBLE01
```

Formato:

```text
ENSAMBLEXX
```

Donde `XX` es la estacion de entrega en 2 digitos.

Ejemplos:

```text
ENSAMBLE01
ENSAMBLE02
ENSAMBLE03
```

JSON ejemplo:

```json
{
  "reqCode": "REQ_ENSAMBLE01_001",
  "reqTime": "2026-08-10 12:01:00",
  "clientCode": "",
  "tokenCode": "",
  "agvCode": "",
  "taskCode": "",
  "taskTyp": "ENSAMBLE01",
  "priority": "",
  "wbCode": "",
  "ctnrCode": "",
  "ctnrTyp": "",
  "podCode": "",
  "podTyp": "",
  "podDir": "",
  "materialLot": "",
  "positionCodePath": [],
  "data": ""
}
```

## 3. Mapa Cuerpo Campanas

Nombre corto:

```text
CUERCAMP01
```

Formato:

```text
CUERCAMPXX
```

Donde `XX` es la estacion de entrega en 2 digitos.

Ejemplos:

```text
CUERCAMP01
CUERCAMP02
CUERCAMP03
```

JSON ejemplo:

```json
{
  "reqCode": "REQ_CUERCAMP01_001",
  "reqTime": "2026-08-10 12:02:00",
  "clientCode": "",
  "tokenCode": "",
  "agvCode": "",
  "taskCode": "",
  "taskTyp": "CUERCAMP01",
  "priority": "",
  "wbCode": "",
  "ctnrCode": "",
  "ctnrTyp": "",
  "podCode": "",
  "podTyp": "",
  "podDir": "",
  "materialLot": "",
  "positionCodePath": [],
  "data": ""
}
```

## Notas de envio

Se envia una tarea por cada POST.

Cada envio debe tener un `reqCode` unico.

Ejemplo de secuencia:

```text
POST CAMPANA01
POST ENSAMBLE01
POST CUERCAMP01
```

Si se cambia la estacion de entrega, se cambia el numero final:

```text
CAMPANA02
ENSAMBLE02
CUERCAMP02
```
