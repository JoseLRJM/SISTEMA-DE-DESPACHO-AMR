"""RCS Web Charge: one disposable session, no transport retries."""
from urllib.parse import urlsplit

import requests

LOGIN_ENDPOINT = "/rcms/web/login/login.action"
CHARGE_ENDPOINT = "/rcms/web/agvControl/agvChargeTask.action"


def connected(robot):
    text = str(getattr(robot, "statusText", "") or "").strip().lower()
    if text in {"offline", "disconnected", "sin conexión", "desconectado"}:
        return False
    return str(getattr(robot, "agvStatus", "") or getattr(robot, "status", "")).strip() in {str(n) for n in range(1, 10)}


def web_url(base, endpoint):
    parsed = urlsplit(base)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ValueError("URL base RCS Web inválida")
    if not endpoint.startswith("/") or endpoint.startswith("//") or any(c in endpoint for c in "?#\\\r\n"):
        raise ValueError("Endpoint RCS Web inválido")
    return base.rstrip("/") + endpoint


def send_charge(*, base_url, login_endpoint, charge_endpoint, username, password, agv_code, verify_tls=True):
    login_url = web_url(base_url, login_endpoint)
    charge_url = web_url(base_url, charge_endpoint)
    if not username or not password:
        return {"ok": False, "result": "configuration_missing", "message": "Faltan credenciales RCS Web."}
    # Never expose arbitrary response bodies, exception strings or cookies.
    stage = "login"
    try:
        with requests.Session() as session:
            response = session.post(login_url,
                data={"ecsUserName": username, "ecsPassword": password, "pwdSafeLevelLogin": "3"},
                headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8", "X-Requested-With": "XMLHttpRequest"},
                timeout=(5, 15), verify=verify_tls, allow_redirects=False)
            if response.status_code != 200 or response.json().get("success") is not True:
                return {"ok": False, "result": "login_rejected", "message": "Login RCS Web no confirmado; no se envió Charge."}
            stage = "charge"
            response = session.post(charge_url,
                json={"clientCode": "", "agvCode": agv_code, "chargeCode": "1"},
                headers={"Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest"},
                timeout=(5, 15), verify=verify_tls, allow_redirects=False)
            body = response.json()
            ok = response.status_code == 200 and str(body.get("code")) == "0" and body.get("message") == "successful"
            return {"ok": ok, "result": "accepted" if ok else "not_confirmed",
                    "message": f"Orden de carga enviada al AMR {agv_code}." if ok else "Charge no confirmado; consultar estado RCS antes de repetir."}
    except (requests.RequestException, ValueError, AttributeError):
        return {"ok": False, "result": stage + "_unknown", "message": "Resultado Web no confirmado; no se reintentó automáticamente."}
