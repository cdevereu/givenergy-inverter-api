"""
GivEnergy Inverter Web API
- GET  /api/status   → cached power/status data
- POST /api/control  → set mode or power levels
- Web UI at /
"""

import asyncio
import json
import time
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from modbus import poll_inverter, write_register, write_registers, watts_to_register

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
CONFIG_FILE = Path("data/config.json")
SAVED_FILE = Path("data/saved_state.json")

inverter_ip: Optional[str] = None
cached_data: Optional[dict] = None
last_poll_time: float = 0.0
poll_interval: float = 10.0  # seconds — matches the original BBC BASIC app
poll_task: Optional[asyncio.Task] = None
polling_enabled: bool = True
current_mode: str = "unknown"  # normal, pause, charge, discharge

# Lock to prevent poll and write from hitting the inverter simultaneously.
# The BBC BASIC original is single-threaded so this never happens there.
# Initialised in lifespan() to ensure it's bound to the running event loop.
inverter_lock: Optional[asyncio.Lock] = None

# Rolling log of API requests (most recent first, max 200)
api_log: deque = deque(maxlen=200)


def _log(method: str, path: str, detail: str = ""):
    ts = datetime.now().strftime("%H:%M:%S")
    api_log.appendleft({"time": ts, "method": method, "path": path, "detail": detail})


def _load_config():
    global inverter_ip
    if CONFIG_FILE.exists():
        cfg = json.loads(CONFIG_FILE.read_text())
        inverter_ip = cfg.get("ip")


def _save_config():
    CONFIG_FILE.write_text(json.dumps({"ip": inverter_ip}, indent=2))


def _save_normal_state():
    """Save all schedule/mode registers so we can restore them on 'normal'.

    Mirrors the BBC BASIC PROCsavenormals which saves regs:
      94, 95  — charge start/finish times
      56, 57  — discharge (1) start/finish times
      44, 45  — discharge (2) start/finish times
      96, 59  — scheduled charge & discharge enables
      20, 27  — charge-limit enable & battery/eco mode
      111,112 — max charge & discharge power
    """
    if not cached_data:
        return
    state = {
        "reg_94": cached_data.get("reg_94", 0),
        "reg_95": cached_data.get("reg_95", 0),
        "reg_56": cached_data.get("reg_56", 0),
        "reg_57": cached_data.get("reg_57", 0),
        "reg_44": cached_data.get("reg_44", 0),
        "reg_45": cached_data.get("reg_45", 0),
        "reg_96": cached_data.get("reg_96", 0),
        "reg_59": cached_data.get("reg_59", 0),
        "reg_20": cached_data.get("reg_20", 0),
        "reg_27": cached_data.get("reg_27", 1),
        "charge_power_raw": cached_data.get("charge_power_raw", 50),
        "discharge_power_raw": cached_data.get("discharge_power_raw", 50),
    }
    SAVED_FILE.write_text(json.dumps(state, indent=2))


def _load_normal_state() -> dict:
    """Load saved normal state, or return sensible defaults."""
    if SAVED_FILE.exists():
        try:
            return json.loads(SAVED_FILE.read_text())
        except Exception:
            pass
    return {
        "reg_94": 0, "reg_95": 0,
        "reg_56": 0, "reg_57": 0,
        "reg_44": 0, "reg_45": 0,
        "reg_96": 0, "reg_59": 0,
        "reg_20": 0, "reg_27": 1,
        "charge_power_raw": 50, "discharge_power_raw": 50,
    }


# ---------------------------------------------------------------------------
# Background poller
# ---------------------------------------------------------------------------
async def _poll_loop():
    """Poll the inverter every ~10 seconds in the background."""
    global cached_data, last_poll_time
    while True:
        if inverter_ip and polling_enabled:
            async with inverter_lock:
                # Re-check after acquiring lock — polling may have been
                # disabled while we were waiting for a write to finish.
                if polling_enabled:
                    try:
                        data = await asyncio.get_running_loop().run_in_executor(
                            None, poll_inverter, inverter_ip
                        )
                        if data:
                            cached_data = data
                            last_poll_time = time.time()
                            _log("POLL", f"{inverter_ip}:8899", f"OK — {data['solar_w']}W solar")
                        else:
                            _log("POLL", f"{inverter_ip}:8899", "No response")
                    except Exception as e:
                        _log("POLL", f"{inverter_ip}:8899", f"Error: {e}")
        await asyncio.sleep(poll_interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global poll_task, inverter_lock
    inverter_lock = asyncio.Lock()
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    _load_config()
    poll_task = asyncio.create_task(_poll_loop())
    yield
    poll_task.cancel()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(title="GivEnergy Inverter API", lifespan=lifespan)
templates = Jinja2Templates(directory="templates")


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------
@app.get("/api/status")
async def api_status():
    """Return cached inverter data. Never hits the inverter directly."""
    _log("GET", "/api/status")
    if cached_data is None:
        return JSONResponse({"error": "No data yet — inverter may not be configured or reachable"}, status_code=503)
    age = round(time.time() - last_poll_time, 1)
    return {**cached_data, "cache_age_seconds": age}


@app.post("/api/control")
async def api_control(request: Request):
    """
    Control the inverter. JSON body with one of:
      {"action": "normal"}    — restore normal mode (all saved registers)
      {"action": "pause"}     — zero charge + discharge, eco mode off
      {"action": "charge"}    — force charge 00:00-23:59, unlimited, eco off
      {"action": "discharge"} — force discharge 00:00-23:59, unlimited, eco off
      {"action": "set_charge_power", "watts": 1500}
      {"action": "set_discharge_power", "watts": 1500}

    All multi-register writes use a single TCP connection to the inverter,
    with per-register confirmation. Matches the BBC BASIC original.
    """
    global current_mode
    if not inverter_ip:
        return JSONResponse({"error": "No inverter IP configured"}, status_code=400)

    body = await request.json()
    action = body.get("action", "")
    detail = json.dumps(body)
    _log("POST", "/api/control", detail)

    loop = asyncio.get_running_loop()
    scaling = cached_data.get("scaling_w", 0) if cached_data else 0

    async def _batch_write(pairs: list[tuple[int, int]]) -> list[dict]:
        async with inverter_lock:
            return await loop.run_in_executor(None, write_registers, inverter_ip, pairs)

    async def _single_write(reg, val) -> bool:
        async with inverter_lock:
            return await loop.run_in_executor(None, write_register, inverter_ip, reg, val)

    if action == "pause":
        if current_mode in ("normal", "unknown"):
            _save_normal_state()
        writes = [
            (27, 0),     # eco mode off
            (59, 0),     # disable scheduled discharge
            (111, 0),    # charge power = 0
            (112, 0),    # discharge power = 0
        ]
        results = await _batch_write(writes)
        all_ok = all(r["ok"] for r in results)
        if all_ok:
            current_mode = "pause"
        return {"ok": all_ok, "action": "pause", "writes": results,
                "detail": "Pause: eco off, sched discharge off, charge=0W, discharge=0W"}

    elif action == "normal":
        saved = _load_normal_state()
        writes = [
            (94, saved["reg_94"]),
            (95, saved["reg_95"]),
            (96, saved["reg_96"]),
            (56, saved["reg_56"]),
            (57, saved["reg_57"]),
            (59, saved["reg_59"]),
            (44, saved["reg_44"]),
            (45, saved["reg_45"]),
            (20, saved["reg_20"]),
            (27, saved["reg_27"]),
            (111, saved["charge_power_raw"]),
            (112, saved["discharge_power_raw"]),
        ]
        results = await _batch_write(writes)
        all_ok = all(r["ok"] for r in results)
        if all_ok:
            current_mode = "normal"
        return {"ok": all_ok, "action": "normal", "writes": results,
                "detail": f"Restored all registers incl. eco={saved['reg_27']}, "
                          f"charge_pwr={saved['charge_power_raw']}, "
                          f"discharge_pwr={saved['discharge_power_raw']}"}

    elif action == "charge":
        if current_mode in ("normal", "unknown"):
            _save_normal_state()
        writes = [
            (27, 0),       # eco mode off
            (20, 0),       # charge-limit enable off
            (94, 0),       # charge start = 00:00
            (95, 2359),    # charge end = 23:59
            (96, 1),       # enable scheduled charge
            (111, 51),     # charge power = unlimited
        ]
        results = await _batch_write(writes)
        all_ok = all(r["ok"] for r in results)
        if all_ok:
            current_mode = "charge"
        return {"ok": all_ok, "action": "charge", "writes": results,
                "detail": "Force charge: eco off, schedule 00:00-23:59, unlimited rate"}

    elif action == "discharge":
        if current_mode in ("normal", "unknown"):
            _save_normal_state()
        writes = [
            (27, 0),       # eco mode off
            (56, 0),       # discharge start = 00:00
            (57, 2359),    # discharge end = 23:59
            (59, 1),       # enable scheduled discharge
            (112, 51),     # discharge power = unlimited
        ]
        results = await _batch_write(writes)
        all_ok = all(r["ok"] for r in results)
        if all_ok:
            current_mode = "discharge"
        return {"ok": all_ok, "action": "discharge", "writes": results,
                "detail": "Force discharge: eco off, schedule 00:00-23:59, unlimited rate"}

    elif action == "set_charge_power":
        watts = int(body.get("watts", 0))
        reg_val = watts_to_register(watts, scaling)
        actual_w = "unlimited" if reg_val >= 50 else reg_val * scaling
        ok = await _single_write(111, reg_val)
        return {"ok": ok, "action": "set_charge_power", "watts_requested": watts,
                "register_value": reg_val, "actual_watts": actual_w}

    elif action == "set_discharge_power":
        watts = int(body.get("watts", 0))
        reg_val = watts_to_register(watts, scaling)
        actual_w = "unlimited" if reg_val >= 50 else reg_val * scaling
        ok = await _single_write(112, reg_val)
        return {"ok": ok, "action": "set_discharge_power", "watts_requested": watts,
                "register_value": reg_val, "actual_watts": actual_w}

    else:
        return JSONResponse({"error": f"Unknown action: {action}"}, status_code=400)


# ---------------------------------------------------------------------------
# Config endpoint
# ---------------------------------------------------------------------------
@app.post("/api/config")
async def api_config(request: Request):
    """Set inverter IP. JSON body: {"ip": "192.168.1.x"}"""
    global inverter_ip, cached_data
    body = await request.json()
    inverter_ip = body.get("ip", inverter_ip)
    cached_data = None  # clear stale data
    _save_config()
    _log("POST", "/api/config", f"Set IP to {inverter_ip}")
    return {"ok": True, "ip": inverter_ip}


# ---------------------------------------------------------------------------
# API log endpoint
# ---------------------------------------------------------------------------
@app.get("/api/log")
async def get_api_log():
    return list(api_log)


@app.post("/api/polling")
async def api_polling(request: Request):
    """Start or stop polling. JSON body: {"enabled": true/false}"""
    global polling_enabled
    body = await request.json()
    polling_enabled = bool(body.get("enabled", True))
    _log("POST", "/api/polling", "enabled" if polling_enabled else "disabled")
    return {"ok": True, "polling": polling_enabled}


@app.get("/api/polling")
async def get_polling():
    return {"polling": polling_enabled}


# ---------------------------------------------------------------------------
# Web UI
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    resp = templates.TemplateResponse("index.html", {
        "request": request,
        "ip": inverter_ip or "",
    })
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp
