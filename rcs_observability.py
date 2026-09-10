"""Secret-free RCS audit copies; transport semantics remain owned by requests."""
import base64
import ctypes
import json
import logging
import os
import re
import threading
import traceback
import uuid
from collections import deque
from collections.abc import Mapping
from contextvars import ContextVar
from datetime import datetime, timezone

REDACTED = "[REDACTED]"
_known = deque(maxlen=512)
_known_lock = threading.Lock()
_scope = ContextVar("rcs_audit_scope", default=None)
_transport = ContextVar("rcs_audit_transport", default=None)


def sensitive_key(key):
    key = re.sub(r"[^a-z0-9]", "", str(key).lower())
    return key in {"key", "sid", "auth", "pwd"} or any(part in key for part in
        ("password", "passwd", "passphrase", "token", "authorization", "authheader", "cookie", "secret", "apikey", "accesskey", "privatekey", "credential", "sessionid"))


def remember_secret(value):
    if isinstance(value, (dict, list, tuple)):
        for item in (value.values() if isinstance(value, dict) else value): remember_secret(item)
    elif value and value != REDACTED and len(str(value)) >= 3:
        with _known_lock:
            text = str(value)
            if text not in _known: _known.append(text)


def sanitize_text(value):
    text = str(value)
    if text.lstrip().startswith(("{", "[")):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, (dict, list)):
                return json.dumps(sanitize(parsed), ensure_ascii=False)
        except (ValueError, TypeError):
            pass
    # Quoted JSON/Python values, header lines, URL credentials and query values.
    keys = r"[\w-]*(?:password|passwd|passphrase|pwd|token|authorization|cookie|secret|api[ _-]?key|credential|session[_-]?id)[\w-]*"
    text = re.sub(r"(?i)([\"']?(?:" + keys + r"|key)[\"']?\s*[:=]\s*)([\"'])(.*?)\2", lambda m: m[1]+m[2]+REDACTED+m[2], text)
    text = re.sub(r"(?im)((?:authorization|proxy-authorization|cookie|set-cookie)\s*:\s*)[^\r\n]+", lambda m: m[1]+REDACTED, text)
    text = re.sub(r"(?i)([?&\s](?:" + keys + r"|key)=)[^&\s,;\"'}]+", lambda m: m[1]+REDACTED, text)
    text = re.sub(r"(?im)(\b(?:" + keys + r"|key)\s*[:=]\s*)(?![\"'])([^\r\n,;&]+)", lambda m: m[1]+REDACTED, text)
    text = re.sub(r"(?im)([\"']?(?:" + keys + r"|key)[\"']?\s*[:=]\s*[\"'])[^\"'\r\n]*$", lambda m: m[1]+REDACTED, text)
    text = re.sub(r"(?i)(https?://)[^/@\s]+@", r"\1[REDACTED]@", text)
    text = re.sub(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+", r"\1[REDACTED]", text)
    with _known_lock:
        known = sorted(_known, key=len, reverse=True)
    for secret in known:
        if len(secret) < 8:
            text = re.sub(r"(?<!\w)"+re.escape(secret)+r"(?!\w)", REDACTED, text)
        else:
            text = text.replace(secret, REDACTED)
    return text


def sanitize(value):
    if isinstance(value, dict):
        # Discover credentials first, including echoes in sibling message fields.
        _collect_secrets(value)
        return {key: (REDACTED if item else item) if sensitive_key(key) else sanitize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        if len(value) == 2 and isinstance(value[0], str) and sensitive_key(value[0]):
            remember_secret(value[1])
            return [value[0], REDACTED if value[1] else value[1]]
        return [sanitize(item) for item in value]
    if isinstance(value, str): return sanitize_text(value)
    return value


def _collect_secrets(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if sensitive_key(key):
                remember_secret(item)
                if isinstance(item, str) and str(key).lower() in {"authorization", "proxy-authorization"}:
                    remember_secret(item.split(" ", 1)[-1])
                if isinstance(item, str) and "cookie" in str(key).lower():
                    for part in item.split(";"):
                        if "=" in part: remember_secret(part.split("=", 1)[1].strip())
            else: _collect_secrets(item)
    elif isinstance(value, (list, tuple)):
        if len(value) == 2 and isinstance(value[0], str) and sensitive_key(value[0]): remember_secret(value[1])
        for item in value: _collect_secrets(item)
    elif isinstance(value, str) and value.lstrip().startswith(("{", "[")):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, (dict, list)): _collect_secrets(parsed)
        except ValueError: pass


class SecretFilter(logging.Filter):
    def filter(self, record):
        record.args = sanitize(record.args)
        if isinstance(record.args, list): record.args = tuple(record.args)
        record.msg = sanitize_text(record.getMessage())
        record.args = ()
        if record.exc_info:
            record.exc_text = sanitize_text("".join(traceback.format_exception(*record.exc_info)))
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = sanitize_text(record.exc_text)
        if record.stack_info: record.stack_info = sanitize_text(record.stack_info)
        return True


def audit_record(**fields):
    record = sanitize({"timestamp": datetime.now(timezone.utc).isoformat(), **fields})
    logging.getLogger("app.rcs.audit").info("RCS_AUDIT %s", json.dumps(record, ensure_ascii=False, default=str))
    scope = _scope.get()
    if scope is not None: scope["events"].append(record)
    return record


def trace_call(operation, rows, callback, *args, **kwargs):
    context = {"trace_id": uuid.uuid4().hex, "operation": operation,
        "orders": [{"movement_order_id": row.id, "q1": row.req_code, "t1": row.remote_task_code} for row in rows], "events": []}
    token = _scope.set(context)
    try:
        result = callback(*args, **kwargs)
        audit_record(**{k:v for k,v in context.items() if k != "events"}, event="operation_result",
                     result="response", rcs_code=getattr(result, "code", None),
                     t1=getattr(result, "data", None) if operation == "create" else None)
        return result
    except Exception as exc:
        audit_record(**{k:v for k,v in context.items() if k != "events"}, event="operation_result",
                     result="exception", error_type=type(exc).__name__, error=sanitize_text(exc))
        raise
    finally:
        for row in rows:
            row._rcs_audit_events = context["events"][-20:]
        _scope.reset(token)


def record_attempt(response=None, error=None):
    state = _transport.get()
    if state is None: return
    if response is not None:
        if any(previous is response for previous in state["responses"]): return
        state["responses"].append(response)
    state["attempt"] += 1
    status = getattr(response, "status", getattr(response, "status_code", None))
    http_error = isinstance(status, int) and status >= 400
    audit_record(**state["context"], event="transport_attempt", attempt=state["attempt"],
        endpoint=state["endpoint"], request_id=state["request_id"], payload=state["payload"],
        method=state.get("method", "POST"),
        result="transport_error" if error is not None else "http_error" if http_error else "http_response",
        error_type=type(error).__name__ if error is not None else "HTTPError" if http_error else None,
        error=sanitize_text(error) if error is not None else None,
        http_status=status)


def observe_prepared_request(request):
    state = _transport.get()
    if state is None: return
    sanitize(dict(request.headers))
    body = request.body
    if isinstance(body, bytes): body = body.decode("utf-8", errors="replace")
    try: body = json.loads(body)
    except (ValueError, TypeError): pass
    state.update(payload=sanitize(body), endpoint=sanitize_text(request.url), method=request.method)
    if isinstance(body, dict): state["request_id"] = body.get("reqCode")


def audited_post(session, url, *, operation, **kwargs):
    payload = kwargs.get("data", "")
    try: payload = json.loads(payload)
    except (TypeError, ValueError): pass
    sanitize(session.headers)
    sanitize(kwargs.get("headers", {}))
    from urllib.parse import urlsplit, parse_qsl, unquote
    target = urlsplit(url)
    if target.password: remember_secret(unquote(target.password))
    sanitize(dict(parse_qsl(target.query)))
    if session.auth and isinstance(session.auth, tuple): remember_secret(session.auth[1])
    for cookie in session.cookies: remember_secret(cookie.value)
    safe_payload = sanitize(payload)
    scope = _scope.get()
    context = {k:v for k,v in scope.items() if k != "events"} if scope else {"trace_id": uuid.uuid4().hex, "operation": operation, "orders": []}
    context["operation"] = operation if not scope else scope["operation"]
    state = {"context": context, "endpoint": sanitize_text(url), "payload": safe_payload,
             "request_id": payload.get("reqCode") if isinstance(payload, dict) else None, "attempt": 0, "responses": []}
    token = _transport.set(state)
    hooks = dict(kwargs.pop("hooks", {}) or {})
    previous = hooks.get("response", [])
    if callable(previous): previous = [previous]
    def observe(response, *args, **options):
        for hook in previous:
            changed = hook(response, *args, **options)
            if changed is not None: response = changed
        response_headers = getattr(response, "headers", {})
        if isinstance(response_headers, Mapping): sanitize(dict(response_headers))
        record_attempt(response=getattr(response, "raw", None) or response)
        return response
    hooks["response"] = observe
    try:
        response = session.post(url, hooks=hooks, **kwargs)
        # Also accommodates transport stubs which do not execute requests hooks.
        if state["attempt"] == 0: record_attempt(response=getattr(response, "raw", None) or response)
        return response
    except Exception as exc:
        # Retry.increment already records exhausted attempts, including the last.
        if state["attempt"] == 0: record_attempt(error=exc)
        raise
    finally:
        _transport.reset(token)


def protect_payload(payload):
    """Windows DPAPI protects operational override credentials, not audit copies."""
    if sanitize(payload) == payload: return json.dumps(payload, ensure_ascii=False)
    if os.name != "nt":
        raise ValueError("Payload con credenciales requiere almacenamiento protegido de Windows")
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return json.dumps({"_protected_payload": base64.b64encode(_dpapi(data, False)).decode("ascii")})


def restore_redactions(value, original):
    if value == REDACTED and original is not None: return original
    if isinstance(value, dict) and isinstance(original, dict):
        return {key: restore_redactions(item, original.get(key)) for key, item in value.items()}
    if isinstance(value, list) and isinstance(original, list):
        return [restore_redactions(item, original[i] if i < len(original) else None) for i, item in enumerate(value)]
    if isinstance(value, str) and isinstance(original, str) and value == sanitize_text(original):
        return original
    return value


def unprotect_payload(value):
    payload = json.loads(value) if isinstance(value, str) else value
    if isinstance(payload, dict) and "_protected_payload" in payload:
        return json.loads(_dpapi(base64.b64decode(payload["_protected_payload"]), True))
    return payload


def _dpapi(value, decrypt):
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_byte))]
    buffer = ctypes.create_string_buffer(value)
    source = Blob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    target = Blob()
    operation = ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise ValueError("No fue posible acceder al payload protegido para esta cuenta Windows")
    try: return ctypes.string_at(target.data, target.size)
    finally: ctypes.windll.kernel32.LocalFree(target.data)


def install_persistence_sanitizer():
    from sqlalchemy import event
    from sqlalchemy.orm import Session
    from models import MovementOrder, DebugConsoleEvent, RackSyncEvent, ConfigAuditLog
    @event.listens_for(Session, "before_flush")
    def sanitize_audit_columns(session, *_):
        for row in session.new | session.dirty:
            if isinstance(row, MovementOrder):
                fields = ("dispatch_request_json", "dispatch_response_json", "rcs_response_json", "status_query_request_json", "status_query_response_json", "status_query_log_json", "rcs_message")
            elif isinstance(row, (DebugConsoleEvent, RackSyncEvent)):
                fields = ("payload_json", "message", "base_url", "endpoint")
            elif isinstance(row, ConfigAuditLog):
                if sensitive_key(row.record_key) or sensitive_key(row.field_name):
                    row.old_value = REDACTED if row.old_value else row.old_value
                    row.new_value = REDACTED if row.new_value else row.new_value
                fields = ("old_value", "new_value", "note", "endpoint")
            else: continue
            for field in fields:
                value = getattr(row, field, None)
                if value is not None: setattr(row, field, sanitize_text(value))
