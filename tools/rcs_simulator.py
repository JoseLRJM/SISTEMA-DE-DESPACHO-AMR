from __future__ import annotations

import time
import uuid
from datetime import datetime
from typing import Any

from fastapi import FastAPI, Request


app = FastAPI(title="RCS Simulator")

TASKS: dict[str, dict[str, Any]] = {}
RUNNING_SECONDS = 10.0


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except Exception:
        data = {}
    return data if isinstance(data, dict) else {}


def _new_task_code(payload: dict[str, Any]) -> str:
    task_typ = str(payload.get("taskTyp") or "TASK").strip() or "TASK"
    return f"{task_typ}_{uuid.uuid4()}"


@app.get("/health")
def health():
    return {"ok": True, "tasks": len(TASKS), "time": _now_text()}


@app.post("/rcms/services/rest/hikRpcService/genAgvSchedulingTask")
@app.post("/rcs/task/create")
async def create_task(request: Request):
    payload = await _json_body(request)
    req_code = str(payload.get("reqCode") or uuid.uuid4().hex[:16])
    task_code = _new_task_code(payload)
    TASKS[task_code] = {
        "taskCode": task_code,
        "taskTyp": str(payload.get("taskTyp") or ""),
        "agvCode": str(payload.get("agvCode") or "SIM-AGV"),
        "createdAt": time.monotonic(),
        "payload": payload,
    }
    print(f"[RCS SIM] createTask reqCode={req_code} taskCode={task_code}")
    return {
        "code": 0,
        "data": task_code,
        "message": "Success",
        "reqCode": req_code,
    }


@app.post("/rcms/services/rest/hikRpcService/queryTaskStatus")
async def query_task_status(request: Request):
    payload = await _json_body(request)
    req_code = str(payload.get("reqCode") or uuid.uuid4().hex[:16])
    requested = payload.get("taskCodes") or []
    if isinstance(requested, str):
        requested = [requested]
    if not requested and payload.get("taskCode"):
        requested = [payload.get("taskCode")]

    items = []
    for raw_code in requested:
        task_code = str(raw_code or "").strip()
        task = TASKS.get(task_code) or {
            "taskCode": task_code,
            "taskTyp": "",
            "agvCode": "SIM-AGV",
            "createdAt": time.monotonic() - RUNNING_SECONDS,
        }
        elapsed = time.monotonic() - float(task.get("createdAt") or 0.0)
        items.append({
            "taskCode": task_code,
            "taskStatus": "2" if elapsed < RUNNING_SECONDS else "9",
            "taskTyp": str(task.get("taskTyp") or ""),
            "agvCode": str(task.get("agvCode") or "SIM-AGV"),
            "elapsedSeconds": round(elapsed, 2),
        })

    if not items:
        items.append({
            "taskCode": "",
            "taskStatus": "9",
            "taskTyp": "",
            "agvCode": "SIM-AGV",
        })

    print(f"[RCS SIM] queryTaskStatus reqCode={req_code} tasks={[i['taskCode'] for i in items]}")
    return {
        "code": 0,
        "message": "Success",
        "reqCode": req_code,
        "data": items,
    }


@app.post("/rcms-dps/rest/queryAgvStatus")
async def query_agv_status(request: Request):
    payload = await _json_body(request)
    req_code = str(payload.get("reqCode") or uuid.uuid4().hex[:16])
    return {
        "code": 0,
        "message": "Success",
        "reqCode": req_code,
        "data": [
            {
                "agvCode": "SIM-AGV",
                "robotCode": "SIM-AGV",
                "status": "idle",
                "battery": 100,
                "mapShortName": str(payload.get("mapShortName") or "SIM"),
            }
        ],
    }


@app.post("/rcms/services/rest/hikRpcService/cancelTask")
async def cancel_task(request: Request):
    payload = await _json_body(request)
    task_code = str(payload.get("taskCode") or "").strip()
    if task_code in TASKS:
        TASKS[task_code]["status"] = "6"
    return {
        "code": 0,
        "message": "Success",
        "reqCode": str(payload.get("reqCode") or uuid.uuid4().hex[:16]),
    }
