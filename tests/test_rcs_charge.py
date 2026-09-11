import json
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main
import rcs_charge_service as charge
from models import Base


class ChargeTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine)
        self.args = dict(base_url="http://rcs.test:8181", login_endpoint=charge.LOGIN_ENDPOINT,
                         charge_endpoint=charge.CHARGE_ENDPOINT, username="admin", password="PRIVATE_PASSWORD", agv_code="30634")

    def tearDown(self):
        self.engine.dispose()

    def test_card_visibility_and_selected_identity(self):
        source = (Path(__file__).resolve().parents[1] / "static/app.js").read_text(encoding="utf-8")
        block = source[source.index("function robotCanCharge"):source.index("async function sendRobotControl")]
        script = """
const assert=require('assert');
const robotMonitorBody={innerHTML:'', querySelectorAll:()=>[]};
function escapeHtml(s){return String(s)}
function formatRobotStatusTone(){return ''} function getRobotMonitorStateLabel(){return ''}
function getRobotTaskStateValue(){return ''} function getRobotLocationValue(){return ''}
function getRobotMapShortNameValue(){return ''} function getRobotBatteryValue(){return ''}
function normalizeRobotBooleanFlag(){return false} function getRobotVelocityValue(){return ''}
function getRobotErrorText(){return ''} function translateRobotStatusToSpanish(){return ''}
""" + block + """
for(const code of ['30634','30636']) {
 renderRobotMonitorItems([{agvCode:code,status:'1'}], '', true);
 assert(robotMonitorBody.innerHTML.includes(`data-robot-charge="${code}"`));
 assert(robotMonitorBody.innerHTML.includes('data-robot-action="stop"'));
 assert(robotMonitorBody.innerHTML.includes('data-robot-action="resume"'));
}
for(const [robot,fresh] of [[{agvCode:'30634',status:'1',statusText:'Offline'},true],[{agvCode:'30634',status:'1'},false]]) {
 renderRobotMonitorItems([robot], '', fresh);
 assert(!robotMonitorBody.innerHTML.includes('data-robot-charge='));
}
assert(robotCanCharge({agvCode:'30634',status:'5'},true));
clearTimeout(chargeVisibilityTimer);
"""
        run = subprocess.run(["node", "-"], input=script, text=True, capture_output=True, timeout=15)
        self.assertEqual(run.returncode, 0, run.stderr)

    def test_fresh_backend_check_blocks_offline_stale_and_missing(self):
        with patch.object(main, "SessionLocal", self.factory), patch.object(main, "_get_robot_status_monitor_response") as state, patch.object(charge, "send_charge") as send:
            for observation in [main.RobotMonitorResponse(ok=False), main.RobotMonitorResponse(ok=True, stale=True), main.RobotMonitorResponse(ok=True, robots=[main.RobotMonitorItem(agvCode="30634",status="0")])]:
                state.return_value = observation
                with self.assertRaises(HTTPException):
                    main.charge_robot_control(main.RobotControlIn(robot_code="30634"))
            send.assert_not_called()

    def test_new_session_login_cookies_charge_and_close_per_operation(self):
        sessions = []
        real_session = requests.Session
        def session_factory():
            session = real_session()
            sessions.append(session)
            session.close = Mock(wraps=session.close)
            def post(url, **kw):
                if url.endswith("login.action"):
                    self.assertEqual(kw["data"], {"ecsUserName":"admin","ecsPassword":"PRIVATE_PASSWORD","pwdSafeLevelLogin":"3"})
                    session.cookies.set("JSESSIONID", "PRIVATE_COOKIE")
                    return SimpleNamespace(status_code=200, json=lambda: {"success":True})
                self.assertEqual(session.cookies.get("JSESSIONID"), "PRIVATE_COOKIE")
                self.assertEqual(kw["json"], {"clientCode":"","agvCode":"30634","chargeCode":"1"})
                self.assertFalse(kw["allow_redirects"])
                return SimpleNamespace(status_code=200, json=lambda: {"code":"0","message":"successful"})
            session.post = Mock(side_effect=post)
            return session
        with patch.object(charge.requests, "Session", side_effect=session_factory):
            for _ in range(2):
                self.assertTrue(charge.send_charge(**self.args)["ok"])
        self.assertIsNot(sessions[0], sessions[1])
        for session in sessions:
            self.assertEqual(session.post.call_count, 2)
            session.close.assert_called_once()

    def test_failed_login_no_charge_no_retry_or_secret_response(self):
        for failure in [SimpleNamespace(status_code=200,json=lambda:{"success":False,"message":"PRIVATE_PASSWORD PRIVATE_COOKIE"}), requests.ReadTimeout("PRIVATE_PASSWORD PRIVATE_COOKIE")]:
            with patch.object(charge.requests, "Session") as session:
                post = session.return_value.__enter__.return_value.post
                if isinstance(failure, Exception): post.side_effect=failure
                else: post.return_value=failure
                result = charge.send_charge(**self.args)
                self.assertFalse(result["ok"])
                self.assertEqual(post.call_count, 1)
                self.assertNotIn("PRIVATE_", json.dumps(result))
                session.return_value.__exit__.assert_called_once()

    def test_settings_mask_and_charge_audit_excludes_secrets(self):
        with self.factory() as db:
            main._save_rcs_config(db, main.RcsConfigIn(web_base_url=self.args["base_url"], web_username="admin", web_ecs_password="PRIVATE_PASSWORD"))
            db.commit()
            self.assertEqual(main._get_rcs_config(db).web_ecs_password, "")
            main._save_rcs_config(db, main.RcsConfigIn(web_ecs_password=""))
            db.commit()
            self.assertEqual(main.get_setting(db,"rcs_web_ecs_password",""), "PRIVATE_PASSWORD")
        with patch.object(main,"SessionLocal",self.factory), patch.object(main,"_get_robot_status_monitor_response",return_value=main.RobotMonitorResponse(ok=True,robots=[main.RobotMonitorItem(agvCode="30634",status="1")])), patch.object(charge.requests,"Session") as session, patch.object(main,"_append_debug_console_event") as log:
            session.return_value.__enter__.return_value.post.side_effect=[SimpleNamespace(status_code=200,json=lambda:{"success":True}),SimpleNamespace(status_code=200,json=lambda:{"code":"0","message":"successful","Cookie":"PRIVATE_COOKIE","ecsPassword":"PRIVATE_PASSWORD"})]
            result=main.charge_robot_control(main.RobotControlIn(robot_code="30634"))
            self.assertTrue(result["ok"])
            self.assertNotIn("PRIVATE_", str(log.call_args) + json.dumps(result))
