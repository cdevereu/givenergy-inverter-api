# GivEnergy Inverter Local API

A simple, self-hosted Docker container that talks directly to your GivEnergy inverter over your local network. No cloud dependency — just a clean REST API and a lightweight web UI for monitoring and controlling your inverter.

## What it does

- Polls your inverter every ~10 seconds for live power data (solar, house, battery, grid)
- REST API for reading status and sending commands
- Quick controls: Normal, Pause, Charge, Discharge — with proper save/restore of settings
- Web UI with live-updating power readings, energy stats, and controls
- Config persisted across container restarts via Docker volume

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

Open `http://YOUR_DOCKER_HOST:8080` in your browser and enter your inverter's IP address.

Your inverter needs a static IP on your local network.

## API

### GET /api/status

Returns cached inverter readings.

```bash
curl http://YOUR_HOST:8080/api/status
```

### POST /api/control

Send commands to the inverter.

```bash
# Normal mode — restore saved settings
curl -X POST http://YOUR_HOST:8080/api/control \
  -H 'Content-Type: application/json' \
  -d '{"action": "normal"}'

# Pause — zero charge and discharge
curl -X POST http://YOUR_HOST:8080/api/control \
  -H 'Content-Type: application/json' \
  -d '{"action": "pause"}'

# Force charge
curl -X POST http://YOUR_HOST:8080/api/control \
  -H 'Content-Type: application/json' \
  -d '{"action": "charge"}'

# Force discharge
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

### POST /api/config

Set the inverter IP address. Persisted across restarts.

```bash
curl -X POST http://YOUR_HOST:8080/api/config \
  -H 'Content-Type: application/json' \
  -d '{"ip": "192.168.1.100"}'
```

### GET /api/polling / POST /api/polling

Check or toggle background polling.

```bash
curl http://YOUR_HOST:8080/api/polling
curl -X POST http://YOUR_HOST:8080/api/polling \
  -H 'Content-Type: application/json' \
  -d '{"enabled": false}'
```

### GET /api/log

Returns the last 200 API/poll log entries.

## How the quick controls work

When you switch to Charge, Discharge, or Pause, the app saves your current inverter settings to a JSON file, then writes the new mode. When you switch back to Normal, it restores everything.

**Important:** If you set a mode with this app, restore it to Normal with this app. Don't mix with the GivEnergy app for mode changes.

## Requirements

- Docker and Docker Compose
- A GivEnergy inverter on your local network with a static IP
- Port 8899 accessible on the inverter (default Modbus port)

## Contributing

Contributions welcome. Fork it, improve it, send a PR.

Some ideas:
- Multi-inverter support
- MQTT publishing for Home Assistant
- Historical data logging
- Battery SoC-based automation
- Authentication middleware

## Credits

The Modbus protocol implementation is derived from the [BBC BASIC Inverter Utility](https://www.bbcbasic.co.uk/inverter_source.html) by **Richard T. Russell**.

## License

MIT

## Disclaimer

This software communicates directly with your inverter over your local network. Use it at your own risk.
