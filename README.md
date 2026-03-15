# GivEnergy Inverter Local API

A simple, self-hosted Docker container that talks directly to your GivEnergy inverter over your local network. No cloud. No GivEnergy API keys. No reliance on third-party servers.

It gives you a clean REST API and a lightweight web UI for monitoring and controlling your inverter — perfect for home automation, scripting, or just keeping an eye on things from your phone.

## Why?

GivEnergy's cloud API is fine, but it's slow, rate-limited, and goes down. If you just want to check your solar generation or flip your battery to charge mode, you shouldn't need to round-trip through the internet to do it.

This talks directly to your inverter on port 8899 using the Modbus protocol. Reads are instant. Writes are confirmed. Everything stays on your LAN.

## What it does

- Polls your inverter every ~10 seconds for live power data (solar, house, battery, grid)
- Exposes a REST API for reading status and sending commands
- Quick controls: Normal, Pause, Charge, Discharge — with proper save/restore of all settings
- Correctly handles eco mode (register 27), schedule times, and power limits
- Single TCP connection for batch writes with per-register confirmation
- Web UI with live-updating power readings, energy stats, and controls
- All config persisted across container restarts via Docker volume

## What it doesn't do

- Multi-inverter support (this is for a single inverter setup)
- Authentication (keep it on your LAN, don't expose to the internet)
- Historical data or graphs (use Grafana/InfluxDB for that)

This is intentionally simple. Fork it, extend it, build on it.

## Quick start

```bash
git clone https://github.com/cdevereu/givenergy-inverter-api.git
cd givenergy-inverter-api
docker compose up -d
```

Then open `http://YOUR_DOCKER_HOST:8080` in your browser and enter your inverter's IP address.

Your inverter must have a static IP on your local network (set in the inverter or your router's DHCP reservations).

## API

All endpoints return JSON.

### GET /api/status

Returns cached inverter readings. Never hits the inverter directly.

```bash
curl http://YOUR_HOST:8080/api/status
```

Response includes `solar_w`, `house_w`, `battery_w`, `grid_w`, `battery_percent`, `max_charge_power_w`, `max_discharge_power_w`, `energy_today_kwh`, and more.

### POST /api/control

Send commands to the inverter.

```bash
# Normal mode — restore all saved settings (eco mode, schedules, power limits)
curl -X POST http://YOUR_HOST:8080/api/control \
  -H 'Content-Type: application/json' \
  -d '{"action": "normal"}'

# Pause — zero charge and discharge, eco mode off
curl -X POST http://YOUR_HOST:8080/api/control \
  -H 'Content-Type: application/json' \
  -d '{"action": "pause"}'

# Force charge — schedule 00:00-23:59, unlimited rate, eco off
curl -X POST http://YOUR_HOST:8080/api/control \
  -H 'Content-Type: application/json' \
  -d '{"action": "charge"}'

# Force discharge — schedule 00:00-23:59, unlimited rate, eco off
curl -X POST http://YOUR_HOST:8080/api/control \
  -H 'Content-Type: application/json' \
  -d '{"action": "discharge"}'

# Set max charge rate in watts
curl -X POST http://YOUR_HOST:8080/api/control \
  -H 'Content-Type: application/json' \
  -d '{"action": "set_charge_power", "watts": 1500}'

# Set max discharge rate in watts
curl -X POST http://YOUR_HOST:8080/api/control \
  -H 'Content-Type: application/json' \
  -d '{"action": "set_discharge_power", "watts": 1500}'
```

The response includes a `writes` array showing each register write and whether the inverter confirmed it.

### POST /api/config

Set the inverter IP address. Persisted across restarts.

```bash
curl -X POST http://YOUR_HOST:8080/api/config \
  -H 'Content-Type: application/json' \
  -d '{"ip": "192.168.1.100"}'
```

### GET /api/polling

Check whether background polling is enabled.

### POST /api/polling

Start or stop background polling.

```bash
curl -X POST http://YOUR_HOST:8080/api/polling \
  -H 'Content-Type: application/json' \
  -d '{"enabled": false}'
```

### GET /api/log

Returns the last 200 API/poll log entries.

## How the quick controls work

When you switch to Charge, Discharge, or Pause mode, the app:

1. Saves your current inverter settings (eco mode, schedule times, schedule enables, power limits) to a JSON file
2. Writes the new mode registers to the inverter, including setting eco mode off (register 27)
3. All writes go over a single TCP connection with per-register confirmation

When you switch back to Normal, it restores all 12 saved registers — schedule times, enables, eco mode, and power limits.

This matches the behaviour of the original BBC BASIC application by Richard T. Russell.

**Important:** If you set a mode with this app, restore it to Normal with this app. Don't mix with the GivEnergy app or portal for mode changes.

## Architecture

- **app.py** — FastAPI web server with polling loop and control endpoints
- **modbus.py** — GivEnergy Modbus protocol layer (frame building, CRC, register read/write)
- **templates/index.html** — Single-page web UI

The poller and control writes share an asyncio lock so they never hit the inverter simultaneously. Only SAFEID (0x31) is used for polling — this avoids pushing data to the GivEnergy portal.

## Configuration

| Environment | Default | Description |
|---|---|---|
| Port mapping | 8080:8080 | Change the left side to use a different host port |

The inverter IP is configured via the web UI or `POST /api/config` and persisted in a Docker volume.

## Requirements

- Docker and Docker Compose
- A GivEnergy inverter on your local network with a static IP
- Port 8899 accessible on the inverter (this is the default Modbus port)

## Contributing

This is a simple project and contributions are welcome. Fork it, improve it, send a PR.

Some ideas:
- Multi-inverter support
- MQTT publishing for Home Assistant
- Historical data logging
- Battery SoC-based automation
- Authentication middleware

## Credits

The Modbus protocol implementation is derived from the [BBC BASIC Inverter Utility](https://www.bbcbasic.co.uk/inverter_source.html) by **Richard T. Russell**. His original application supports up to three inverters with a full GUI, calibration modes, and a clever register-packing scheme for saving settings. This project is a simplified Docker/API adaptation of that work.

## License

MIT — do what you want with it.

## Disclaimer

This software communicates directly with your inverter's Modbus registers. While it only uses safe register ranges and has been tested extensively, use it at your own risk. The authors accept no responsibility for any damage to your inverter or electrical system.
