# v2.1.0 - Control de AMR e integración de carga RCS

- Added Charge action per AMR from Estado AMR.
- Added separate RCS Web configuration for login/charge.
- Added automatic login/session handling before Charge.
- Charge uses the selected robot agvCode and fixed chargeCode=1.
- Charge is hidden and backend-blocked for disconnected/stale robots.
- Existing Pause/Resume behavior remains unchanged.
- Test result: 274 PASS / 0 FAIL.
