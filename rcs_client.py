from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib.parse import urlsplit, urlunsplit

from logging_config import get_logger

logger = get_logger("app.rcs_client")


def _safe_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    safe = dict(payload or {})
    for key in ("tokenCode", "password", "secret"):
        if key in safe and safe[key]:
            safe[key] = "[REDACTED]"
    return safe


def _payload_context(payload: Dict[str, Any]) -> Dict[str, str]:
    return {
        "robot": str(payload.get("agvCode") or payload.get("robotCode") or ""),
        "task": str(payload.get("taskCode") or ""),
        "mapCode": str(payload.get("mapCode") or ""),
        "mapShortName": str(payload.get("mapShortName") or ""),
    }


def _response_code(data: Dict[str, Any]) -> str:
    return str(data.get("code") or data.get("status") or "")


def _log_empty_data(action: str, endpoint: str, payload: Dict[str, Any], data: Dict[str, Any]) -> None:
    if data.get("data") == []:
        context = _payload_context(payload)
        logger.info(
            "RCS empty data | action=%s | endpoint=%s | robot=%s | task=%s | mapCode=%s | mapShortName=%s | response_code=%s",
            action,
            endpoint,
            context["robot"] or "-",
            context["task"] or "-",
            context["mapCode"] or "-",
            context["mapShortName"] or "-",
            _response_code(data) or "-",
        )


class RcsError(Exception):
    pass


class RcsHttpError(RcsError):
    def __init__(self, status_code: int, body: str):
        super().__init__(f"HTTP {status_code}: {body[:500]}")
        self.status_code = status_code
        self.body = body


class RcsParseError(RcsError):
    pass


class RcsTimeoutError(RcsError):
    pass


@dataclass
class PositionStep:
    positionCode: str
    type: str = "00"


@dataclass
class RcsTaskRequest:
    agvCode: str = ""
    clientCode: str = ""
    ctnrCode: str = ""
    ctnrTyp: str = ""
    data: str = ""
    materialLot: str = ""
    podCode: str = ""
    podDir: str = ""
    podTyp: str = ""
    positionCodePath: List[PositionStep] = field(default_factory=list)
    priority: str = ""
    reqCode: str = ""
    reqTime: str = ""
    taskCode: str = ""
    taskTyp: str = "A01"
    tokenCode: str = ""
    wbCode: str = ""

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "RcsTaskRequest":
        return cls(
            agvCode=str(payload.get("agvCode", "") or ""),
            clientCode=str(payload.get("clientCode", "") or ""),
            ctnrCode=str(payload.get("ctnrCode", "") or ""),
            ctnrTyp=str(payload.get("ctnrTyp", "") or ""),
            data=str(payload.get("data", "") or ""),
            materialLot=str(payload.get("materialLot", "") or ""),
            podCode=str(payload.get("podCode", "") or ""),
            podDir=str(payload.get("podDir", "") or ""),
            podTyp=str(payload.get("podTyp", "") or ""),
            positionCodePath=[
                PositionStep(
                    positionCode=str(step.get("positionCode", "") or ""),
                    type=str(step.get("type", "00") or "00"),
                )
                for step in (payload.get("positionCodePath") or [])
            ],
            priority=str(payload.get("priority", "") or ""),
            reqCode=str(payload.get("reqCode", "") or ""),
            reqTime=str(payload.get("reqTime", "") or ""),
            taskCode=str(payload.get("taskCode", "") or ""),
            taskTyp=str(payload.get("taskTyp", "A01") or "A01"),
            tokenCode=str(payload.get("tokenCode", "") or ""),
            wbCode=str(payload.get("wbCode", "") or ""),
        )

    def to_payload(self) -> Dict[str, Any]:
        return {
            "agvCode": self.agvCode,
            "clientCode": self.clientCode,
            "ctnrCode": self.ctnrCode,
            "ctnrTyp": self.ctnrTyp,
            "data": self.data,
            "materialLot": self.materialLot,
            "podCode": self.podCode,
            "podDir": self.podDir,
            "podTyp": self.podTyp,
            "positionCodePath": [
                {"positionCode": s.positionCode, "type": s.type}
                for s in (self.positionCodePath or [])
            ],
            "priority": self.priority,
            "reqCode": self.reqCode,
            "reqTime": self.reqTime,
            "taskCode": self.taskCode,
            "taskTyp": self.taskTyp,
            "tokenCode": self.tokenCode,
            "wbCode": self.wbCode,
        }


@dataclass
class RcsTaskResponse:
    code: int
    data: str
    message: str
    reqCode: str
    raw: Dict[str, Any]

    @property
    def ok(self) -> bool:
        return self.code == 0


@dataclass
class RcsTaskStatusResponse:
    code: int
    task_code: str
    task_status: str
    message: str
    reqCode: str
    raw: Dict[str, Any]
    task_statuses: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.code == 0


@dataclass
class RcsSimpleResponse:
    code: int
    message: str
    reqCode: str
    raw: Dict[str, Any]

    @property
    def ok(self) -> bool:
        return self.code == 0


def _extract_task_status_items(data: Dict[str, Any], payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    requested_codes: List[str] = []
    for code in (payload.get("taskCodes") or []):
        value = str(code or "").strip()
        if value and value not in requested_codes:
            requested_codes.append(value)
    single_requested_code = str(payload.get("taskCode") or "").strip()
    if single_requested_code and single_requested_code not in requested_codes:
        requested_codes.append(single_requested_code)

    data_section = data.get("data")
    candidates: List[Any] = []
    if isinstance(data_section, list):
        candidates.extend(data_section)
    elif isinstance(data_section, dict):
        candidates.append(data_section)
        for key in ("list", "records", "items", "taskList", "taskStatusList", "tasks"):
            value = data_section.get(key)
            if isinstance(value, list):
                candidates.extend(value)
    if not candidates:
        candidates.append(data)

    items: List[Dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        task_code = str(
            candidate.get("taskCode")
            or candidate.get("taskcode")
            or candidate.get("task_code")
            or candidate.get("taskNo")
            or ""
        ).strip()
        task_status = str(
            candidate.get("taskStatus")
            or candidate.get("status")
            or candidate.get("task_state")
            or candidate.get("taskStatusStr")
            or ""
        ).strip()
        agv_code = str(candidate.get("agvCode") or candidate.get("robotCode") or "").strip()
        if not task_code and len(requested_codes) == 1:
            task_code = requested_codes[0]
        if task_code or task_status:
            items.append({
                "taskCode": task_code,
                "taskStatus": task_status,
                "agvCode": agv_code,
                "message": str(candidate.get("message") or data.get("message") or ""),
                "raw": candidate,
            })

    if not items and requested_codes:
        for code in requested_codes:
            items.append({
                "taskCode": code,
                "taskStatus": str(data.get("taskStatus") or data.get("status") or "").strip(),
                "agvCode": str(data.get("agvCode") or "").strip(),
                "message": str(data.get("message") or ""),
                "raw": data,
            })
    return items


class RcsClient:
    def __init__(
        self,
        base_url: str,
        endpoint_create_task: str,
        endpoint_query_task_status: str = "/rcms/services/rest/hikRpcService/queryTaskStatus",
        endpoint_cancel_task: str = "/rcms/services/rest/hikRpcService/cancelTask",
        timeout: Tuple[float, float] = (5.0, 20.0),
        verify_tls: bool = True,
        max_retries: int = 2,
        backoff_factor: float = 0.4,
        default_headers: Optional[Dict[str, str]] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.endpoint_create_task = endpoint_create_task
        self.endpoint_query_task_status = endpoint_query_task_status
        self.endpoint_cancel_task = endpoint_cancel_task
        self.timeout = timeout
        self.verify_tls = verify_tls
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        if default_headers:
            self.session.headers.update(default_headers)
        retry = Retry(
            total=max_retries,
            connect=max_retries,
            read=max_retries,
            status=max_retries,
            backoff_factor=backoff_factor,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET", "POST", "PUT", "DELETE", "PATCH"]),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def _build_url(self, endpoint: Optional[str]) -> str:
        endpoint = str(endpoint or "").strip()
        if endpoint.startswith("http://") or endpoint.startswith("https://"):
            return endpoint

        base = (self.base_url or "").strip()
        if not base:
            return endpoint

        base_parts = urlsplit(base)
        base_path = (base_parts.path or "").rstrip("/")
        endpoint_path = endpoint or ""
        if endpoint_path and not endpoint_path.startswith("/"):
            endpoint_path = "/" + endpoint_path

        if base_path and endpoint_path:
            if endpoint_path == base_path or endpoint_path.startswith(base_path + "/"):
                final_path = endpoint_path
            else:
                final_path = base_path + endpoint_path
        else:
            final_path = endpoint_path or base_path or "/"

        return urlunsplit((base_parts.scheme, base_parts.netloc, final_path, "", ""))

    def post_json_payload(self, payload: Dict[str, Any], endpoint_override: Optional[str] = None) -> Dict[str, Any]:
        endpoint = endpoint_override or self.endpoint_query_task_status
        url = self._build_url(endpoint)
        headers = {"X-Request-Id": str(uuid.uuid4())}
        context = _payload_context(payload)
        logger.info(
            "RCS request | endpoint=%s | robot=%s | task=%s | mapCode=%s | mapShortName=%s",
            endpoint,
            context["robot"] or "-",
            context["task"] or "-",
            context["mapCode"] or "-",
            context["mapShortName"] or "-",
        )
        try:
            resp = self.session.post(
                url,
                data=json.dumps(payload),
                headers=headers,
                timeout=self.timeout,
                verify=self.verify_tls,
            )
        except requests.Timeout as e:
            logger.warning("RCS request timeout | endpoint=%s | robot=%s | task=%s", endpoint, context["robot"] or "-", context["task"] or "-")
            raise RcsTimeoutError("Timeout enviando JSON al RCS") from e
        except requests.RequestException as e:
            logger.warning("RCS request network error | endpoint=%s | robot=%s | task=%s | error=%s", endpoint, context["robot"] or "-", context["task"] or "-", e)
            raise RcsError(f"Error de red enviando JSON al RCS: {e}") from e
        if not (200 <= resp.status_code < 300):
            logger.warning("RCS request http error | endpoint=%s | status=%s | body=%s", endpoint, resp.status_code, resp.text[:500])
            raise RcsHttpError(resp.status_code, resp.text)
        data = self._safe_json(resp)
        logger.info("RCS request success | endpoint=%s | robot=%s | task=%s | status=%s | response_code=%s", endpoint, context["robot"] or "-", context["task"] or "-", resp.status_code, _response_code(data) or "-")
        _log_empty_data("post_json_payload", endpoint, payload, data)
        return data

    def query_agv_status_with_payload(self, payload: Dict[str, Any], endpoint_override: Optional[str] = None) -> Dict[str, Any]:
        endpoint = endpoint_override or "/rcms-dps/rest/queryAgvStatus"
        url = self._build_url(endpoint)
        headers = {"X-Request-Id": str(uuid.uuid4())}
        context = _payload_context(payload)
        logger.info("RCS queryAgvStatus request endpoint=%s robot=%s mapCode=%s mapShortName=%s payload=%s", endpoint, context["robot"] or "-", context["mapCode"] or "-", context["mapShortName"] or "-", _safe_payload(payload))
        try:
            resp = self.session.post(
                url,
                data=json.dumps(payload),
                headers=headers,
                timeout=self.timeout,
                verify=self.verify_tls,
            )
        except requests.Timeout as e:
            logger.warning("RCS queryAgvStatus timeout endpoint=%s robot=%s mapCode=%s mapShortName=%s", endpoint, context["robot"] or "-", context["mapCode"] or "-", context["mapShortName"] or "-")
            raise RcsTimeoutError("Timeout consultando estado de AGV en el RCS") from e
        except requests.RequestException as e:
            logger.warning("RCS queryAgvStatus network error endpoint=%s robot=%s error=%s", endpoint, context["robot"] or "-", e)
            raise RcsError(f"Error de red consultando estado de AGV en el RCS: {e}") from e
        logger.info("RCS queryAgvStatus response endpoint=%s status=%s", endpoint, resp.status_code)
        if not (200 <= resp.status_code < 300):
            logger.warning("RCS queryAgvStatus http error endpoint=%s status=%s body=%s", endpoint, resp.status_code, resp.text[:500])
            raise RcsHttpError(resp.status_code, resp.text)
        data = self._safe_json(resp)
        _log_empty_data("queryAgvStatus", endpoint, payload, data)
        logger.info("RCS request success | endpoint=%s | robot=%s | status=%s | response_code=%s", endpoint, context["robot"] or "-", resp.status_code, _response_code(data) or "-")
        logger.info("RCS queryAgvStatus response body=%s", data)
        return data


    def cancel_task_with_payload(self, payload: Dict[str, Any], endpoint_override: Optional[str] = None) -> RcsSimpleResponse:
        endpoint = endpoint_override or self.endpoint_cancel_task
        url = self._build_url(endpoint)
        headers = {"X-Request-Id": str(uuid.uuid4())}
        context = _payload_context(payload)
        logger.info("RCS cancelTask request | endpoint=%s | robot=%s | task=%s | forceCancel=%s", endpoint, context["robot"] or "-", context["task"] or "-", payload.get("forceCancel", "-"))
        try:
            resp = self.session.post(
                url,
                data=json.dumps(payload),
                headers=headers,
                timeout=self.timeout,
                verify=self.verify_tls,
            )
        except requests.Timeout as e:
            logger.warning("RCS cancelTask timeout | endpoint=%s | robot=%s | task=%s", endpoint, context["robot"] or "-", context["task"] or "-")
            raise RcsTimeoutError("Timeout cancelando tarea en el RCS") from e
        except requests.RequestException as e:
            logger.warning("RCS cancelTask network error | endpoint=%s | robot=%s | task=%s | error=%s", endpoint, context["robot"] or "-", context["task"] or "-", e)
            raise RcsError(f"Error de red cancelando tarea en el RCS: {e}") from e
        if not (200 <= resp.status_code < 300):
            logger.warning("RCS cancelTask http error | endpoint=%s | status=%s | body=%s", endpoint, resp.status_code, resp.text[:500])
            raise RcsHttpError(resp.status_code, resp.text)
        data = self._safe_json(resp)
        _log_empty_data("cancelTask", endpoint, payload, data)
        logger.info("RCS request success | endpoint=%s | robot=%s | task=%s | status=%s | response_code=%s", endpoint, context["robot"] or "-", context["task"] or "-", resp.status_code, _response_code(data) or "-")
        try:
            return RcsSimpleResponse(
                code=int(data.get("code", 0)),
                message=str(data.get("message", "")),
                reqCode=str(data.get("reqCode", payload.get("reqCode", ""))),
                raw=data,
            )
        except Exception as e:
            logger.exception("RCS cancelTask parse error | endpoint=%s | data=%s", endpoint, data)
            raise RcsParseError(f"Respuesta JSON no coincide con el formato esperado: {data}") from e

    def cancel_task(self, task_code: str = "", agv_code: str = "", req_code: str = "", token_code: str = "", client_code: str = "", force_cancel: str = "0", matter_area: str = "") -> RcsSimpleResponse:
        payload = {
            "reqCode": req_code or datetime.now().strftime("%Y%m%d%H%M%S%f")[:-3],
            "reqTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "clientCode": client_code or "",
            "tokenCode": token_code or "",
            "forceCancel": force_cancel or "0",
            "matterArea": matter_area or "",
            "agvCode": agv_code or "",
            "taskCode": task_code or "",
        }
        return self.cancel_task_with_payload(payload)

    def query_task_status_with_payload(self, payload: Dict[str, Any]) -> RcsTaskStatusResponse:
        url = self._build_url(self.endpoint_query_task_status)
        headers = {"X-Request-Id": str(uuid.uuid4())}
        endpoint = self.endpoint_query_task_status
        context = _payload_context(payload)
        logger.info("RCS queryTaskStatus request | endpoint=%s | robot=%s | task=%s", endpoint, context["robot"] or "-", context["task"] or "-")
        try:
            resp = self.session.post(
                url,
                data=json.dumps(payload),
                headers=headers,
                timeout=self.timeout,
                verify=self.verify_tls,
            )
        except requests.Timeout as e:
            logger.warning("RCS queryTaskStatus timeout | endpoint=%s | task=%s", endpoint, context["task"] or "-")
            raise RcsTimeoutError("Timeout consultando estado de tarea en el RCS") from e
        except requests.RequestException as e:
            logger.warning("RCS queryTaskStatus network error | endpoint=%s | task=%s | error=%s", endpoint, context["task"] or "-", e)
            raise RcsError(f"Error de red consultando estado de tarea en el RCS: {e}") from e
        if not (200 <= resp.status_code < 300):
            logger.warning("RCS queryTaskStatus http error | endpoint=%s | status=%s | body=%s", endpoint, resp.status_code, resp.text[:500])
            raise RcsHttpError(resp.status_code, resp.text)
        data = self._safe_json(resp)
        _log_empty_data("queryTaskStatus", endpoint, payload, data)
        try:
            items = _extract_task_status_items(data, payload)
            preferred_code = str(payload.get("taskCode") or "").strip()
            selected = None
            if preferred_code:
                selected = next((item for item in items if str(item.get("taskCode") or "").strip() == preferred_code), None)
            if selected is None and items:
                selected = items[0]
            selected = selected or {}
            logger.info("RCS request success | endpoint=%s | task=%s | status=%s | response_code=%s | items=%s", endpoint, context["task"] or "-", resp.status_code, _response_code(data) or "-", len(items))
            return RcsTaskStatusResponse(
                code=int(data.get("code", 0)),
                task_code=str(selected.get("taskCode") or preferred_code or ""),
                task_status=str(selected.get("taskStatus") or data.get("taskStatus") or data.get("status") or ""),
                message=str(selected.get("message") or data.get("message", "")),
                reqCode=str(data.get("reqCode", payload.get("reqCode", ""))),
                raw=data,
                task_statuses=items,
            )
        except Exception as e:
            logger.exception("RCS queryTaskStatus parse error | endpoint=%s | data=%s", endpoint, data)
            raise RcsParseError(f"Respuesta JSON no coincide con el formato esperado: {data}") from e

    def query_task_status(self, task_code: str, req_code: str = "", token_code: str = "", client_code: str = "WCS") -> RcsTaskStatusResponse:
        payload = {
            "reqCode": req_code or uuid.uuid4().hex[:16],
            "reqTime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "clientCode": client_code or "WCS",
            "tokenCode": token_code or "",
            "taskCode": task_code,
        }
        return self.query_task_status_with_payload(payload)

    def create_task(self, task: RcsTaskRequest) -> RcsTaskResponse:
        url = self._build_url(self.endpoint_create_task)
        headers = {"X-Request-Id": str(uuid.uuid4())}
        payload = task.to_payload()
        endpoint = self.endpoint_create_task
        context = _payload_context(payload)
        logger.info("RCS createTask request | endpoint=%s | robot=%s | task=%s | mapCode=%s | mapShortName=%s", endpoint, context["robot"] or "-", context["task"] or "-", context["mapCode"] or "-", context["mapShortName"] or "-")
        try:
            resp = self.session.post(
                url,
                data=json.dumps(payload),
                headers=headers,
                timeout=self.timeout,
                verify=self.verify_tls,
            )
        except requests.Timeout as e:
            logger.warning("RCS createTask timeout | endpoint=%s | robot=%s | task=%s", endpoint, context["robot"] or "-", context["task"] or "-")
            raise RcsTimeoutError("Timeout enviando tarea al RCS") from e
        except requests.RequestException as e:
            logger.warning("RCS createTask network error | endpoint=%s | robot=%s | task=%s | error=%s", endpoint, context["robot"] or "-", context["task"] or "-", e)
            raise RcsError(f"Error de red enviando tarea al RCS: {e}") from e
        if not (200 <= resp.status_code < 300):
            logger.warning("RCS createTask http error | endpoint=%s | status=%s | body=%s", endpoint, resp.status_code, resp.text[:500])
            raise RcsHttpError(resp.status_code, resp.text)
        data = self._safe_json(resp)
        _log_empty_data("createTask", endpoint, payload, data)
        try:
            task_code = str(data.get("taskCode") or data.get("taskcode") or data.get("task_code") or "").strip()
            data_value = str(data.get("data", "") or "").strip()
            if not data_value and task_code:
                data_value = task_code
            logger.info("RCS request success | endpoint=%s | robot=%s | task=%s | status=%s | response_code=%s | remote_task_code=%s", endpoint, context["robot"] or "-", context["task"] or "-", resp.status_code, _response_code(data) or "-", data_value or "-")
            return RcsTaskResponse(
                code=int(data.get("code", 0)),
                data=data_value,
                message=str(data.get("message", "")),
                reqCode=str(data.get("reqCode", "")),
                raw=data,
            )
        except Exception as e:
            logger.exception("RCS createTask parse error | endpoint=%s | data=%s", endpoint, data)
            raise RcsParseError(f"Respuesta JSON no coincide con el formato esperado: {data}") from e

    @staticmethod
    def _safe_json(resp: requests.Response) -> Dict[str, Any]:
        try:
            j = resp.json()
        except ValueError as e:
            raise RcsParseError(f"Respuesta no es JSON. body={resp.text[:500]}") from e
        if not isinstance(j, dict):
            return {"data": j}
        return j
