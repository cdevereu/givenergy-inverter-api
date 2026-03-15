"""
GivEnergy inverter Modbus protocol layer.
Derived from the BBC BASIC 'invertercode' by Richard T. Russell.

Frame layout (34 bytes):
  [0-1]   Header: 0x59 0x59
  [2-3]   Transaction ID: 0x00 0x01
  [4-5]   Length: 0x00 0x1C (28)
  [6-7]   Unit/Protocol: 0x01 0x02
  [8-17]  Padding: 10 zero bytes
  [18-23] Padding: 6 zero bytes
  [24-25] Data length: 0x00 0x08
  [26]    Slave ID (0x31 = SAFEID)
  [27]    Function code (3=read holding, 4=read input, 6=write single)
  [28-29] Start register (big-endian)
  [30-31] Register count / value (big-endian)
  [32-33] CRC-16/Modbus (little-endian)

We use SAFEID (0x31) exclusively. REALID (0x11) causes data to be
pushed to the GivEnergy portal which we don't want.
"""

import socket
import time
from typing import Optional

SAFEID = 0x31
PORT = 8899

CRC_TABLE = [
    0x0000,0xC0C1,0xC181,0x0140,0xC301,0x03C0,0x0280,0xC241,
    0xC601,0x06C0,0x0780,0xC741,0x0500,0xC5C1,0xC481,0x0440,
    0xCC01,0x0CC0,0x0D80,0xCD41,0x0F00,0xCFC1,0xCE81,0x0E40,
    0x0A00,0xCAC1,0xCB81,0x0B40,0xC901,0x09C0,0x0880,0xC841,
    0xD801,0x18C0,0x1980,0xD941,0x1B00,0xDBC1,0xDA81,0x1A40,
    0x1E00,0xDEC1,0xDF81,0x1F40,0xDD01,0x1DC0,0x1C80,0xDC41,
    0x1400,0xD4C1,0xD581,0x1540,0xD701,0x17C0,0x1680,0xD641,
    0xD201,0x12C0,0x1380,0xD341,0x1100,0xD1C1,0xD081,0x1040,
    0xF001,0x30C0,0x3180,0xF141,0x3300,0xF3C1,0xF281,0x3240,
    0x3600,0xF6C1,0xF781,0x3740,0xF501,0x35C0,0x3480,0xF441,
    0x3C00,0xFCC1,0xFD81,0x3D40,0xFF01,0x3FC0,0x3E80,0xFE41,
    0xFA01,0x3AC0,0x3B80,0xFB41,0x3900,0xF9C1,0xF881,0x3840,
    0x2800,0xE8C1,0xE981,0x2940,0xEB01,0x2BC0,0x2A80,0xEA41,
    0xEE01,0x2EC0,0x2F80,0xEF41,0x2D00,0xEDC1,0xEC81,0x2C40,
    0xE401,0x24C0,0x2580,0xE541,0x2700,0xE7C1,0xE681,0x2640,
    0x2200,0xE2C1,0xE381,0x2340,0xE101,0x21C0,0x2080,0xE041,
    0xA001,0x60C0,0x6180,0xA141,0x6300,0xA3C1,0xA281,0x6240,
    0x6600,0xA6C1,0xA781,0x6740,0xA501,0x65C0,0x6480,0xA441,
    0x6C00,0xACC1,0xAD81,0x6D40,0xAF01,0x6FC0,0x6E80,0xAE41,
    0xAA01,0x6AC0,0x6B80,0xAB41,0x6900,0xA9C1,0xA881,0x6840,
    0x7800,0xB8C1,0xB981,0x7940,0xBB01,0x7BC0,0x7A80,0xBA41,
    0xBE01,0x7EC0,0x7F80,0xBF41,0x7D00,0xBDC1,0xBC81,0x7C40,
    0xB401,0x74C0,0x7580,0xB541,0x7700,0xB7C1,0xB681,0x7640,
    0x7200,0xB2C1,0xB381,0x7340,0xB101,0x71C0,0x7080,0xB041,
    0x5000,0x90C1,0x9181,0x5140,0x9301,0x53C0,0x5280,0x9241,
    0x9601,0x56C0,0x5780,0x9741,0x5500,0x95C1,0x9481,0x5440,
    0x9C01,0x5CC0,0x5D80,0x9D41,0x5F00,0x9FC1,0x9E81,0x5E40,
    0x5A00,0x9AC1,0x9B81,0x5B40,0x9901,0x59C0,0x5880,0x9841,
    0x8801,0x48C0,0x4980,0x8941,0x4B00,0x8BC1,0x8A81,0x4A40,
    0x4E00,0x8EC1,0x8F81,0x4F40,0x8D01,0x4DC0,0x4C80,0x8C41,
    0x4400,0x84C1,0x8581,0x4540,0x8701,0x47C0,0x4680,0x8641,
    0x8201,0x42C0,0x4380,0x8341,0x4100,0x81C1,0x8081,0x4040,
]


def _crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc = (crc >> 8) ^ CRC_TABLE[(crc ^ b) & 0xFF]
    return crc


def _build_frame(func_code: int, reg_start: int, reg_count_or_value: int) -> bytes:
    frame = bytearray(34)
    frame[0] = 0x59; frame[1] = 0x59
    frame[2] = 0x00; frame[3] = 0x01
    frame[4] = 0x00; frame[5] = 0x1C
    frame[6] = 0x01; frame[7] = 0x02
    frame[24] = 0x00; frame[25] = 0x08
    frame[26] = SAFEID
    frame[27] = func_code
    frame[28] = (reg_start >> 8) & 0xFF
    frame[29] = reg_start & 0xFF
    frame[30] = (reg_count_or_value >> 8) & 0xFF
    frame[31] = reg_count_or_value & 0xFF
    crc = _crc16(bytes(frame[26:32]))
    frame[32] = crc & 0xFF
    frame[33] = (crc >> 8) & 0xFF
    return bytes(frame)


def _recv_all(sock: socket.socket, timeout: float = 5.0) -> Optional[bytes]:
    sock.settimeout(timeout)
    try:
        header = sock.recv(19)
        if not header or len(header) < 6:
            return None
        expected_len = header[5] + 6
        remaining = expected_len - len(header)
        data = bytearray(header)
        while remaining > 0:
            chunk = sock.recv(remaining)
            if not chunk:
                break
            data.extend(chunk)
            remaining -= len(chunk)
        return bytes(data)
    except socket.timeout:
        return None


def _send_and_recv(sock: socket.socket, func_code: int, start: int, count: int) -> Optional[bytes]:
    """Send a request on an existing socket and read the response."""
    frame = _build_frame(func_code, start, count)
    sock.sendall(frame)
    resp = _recv_all(sock)
    if resp is None or len(resp) < 164:
        return None
    if resp[26] != SAFEID or resp[27] != func_code:
        return None
    return resp


def _signed16(val: int) -> int:
    return val - 0x10000 if val >= 0x8000 else val


def _reg(resp: bytes, r: int) -> int:
    """Extract unsigned 16-bit register from response. Works for both input
    and holding register responses where register N is at offset 42 + N*2."""
    offset = 42 + r * 2
    return resp[offset] * 256 + resp[offset + 1]


def _hreg(resp: bytes, r: int, base: int = 60) -> int:
    """Extract register from a holding register response starting at `base`."""
    offset = 42 + (r - base) * 2
    return resp[offset] * 256 + resp[offset + 1]


def poll_inverter(ip: str, port: int = PORT) -> Optional[dict]:
    """
    Single-connection poll: opens one TCP socket and sends multiple requests
    with small delays between them to avoid overwhelming the inverter.
    Reads: input regs 0-59, holding regs 0-59, holding regs 60-119.
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5.0)
        sock.connect((ip, port))

        # 1. Input registers 0-59 (func 4) — power/status
        input_resp = _send_and_recv(sock, 4, 0, 60)
        if input_resp is None:
            sock.close()
            return None
        result = _parse_input_registers(input_resp)

        time.sleep(0.5)  # breathing room for the inverter

        # 2. Holding registers 0-59 (func 3) — battery capacity at reg 55
        lower_resp = _send_and_recv(sock, 3, 0, 60)
        capacity = 0
        if lower_resp is not None:
            capacity = _reg(lower_resp, 55)

        time.sleep(0.5)

        # 3. Holding registers 60-119 (func 3) — settings incl. regs 98, 111, 112
        upper_resp = _send_and_recv(sock, 3, 60, 60)

        sock.close()

        # Compute scaling and power limits
        voltage = 5100  # default 51V
        reg111 = 50     # default unlimited
        reg112 = 50
        if upper_resp is not None:
            reg98 = _hreg(upper_resp, 98, 60)
            if reg98 > 10000:
                voltage = 30600  # AiO = 306V
            reg111 = _hreg(upper_resp, 111, 60)
            reg112 = _hreg(upper_resp, 112, 60)

        scaling = round(capacity * voltage / 10000) if capacity > 0 else 0
        result["scaling_w"] = scaling
        result["charge_power_raw"] = reg111
        result["discharge_power_raw"] = reg112
        result["max_charge_power_w"] = "unlimited" if reg111 >= 50 else reg111 * scaling
        result["max_discharge_power_w"] = "unlimited" if reg112 >= 50 else reg112 * scaling

        # Extract schedule/mode registers for quick-control save/restore.
        # Lower holding regs 0-59 contain regs 20, 27, 44, 45, 56, 57, 59.
        # Upper holding regs 60-119 contain regs 94, 95, 96.
        if lower_resp is not None:
            result["reg_20"] = _reg(lower_resp, 20)   # charge-up-to-limit enable
            result["reg_27"] = _reg(lower_resp, 27)   # battery/eco mode
            result["reg_44"] = _reg(lower_resp, 44)   # discharge start time (2)
            result["reg_45"] = _reg(lower_resp, 45)   # discharge finish time (2)
            result["reg_56"] = _reg(lower_resp, 56)   # discharge start time (1)
            result["reg_57"] = _reg(lower_resp, 57)   # discharge finish time (1)
            result["reg_59"] = _reg(lower_resp, 59)   # scheduled discharge enable
        if upper_resp is not None:
            result["reg_94"] = _hreg(upper_resp, 94, 60)   # charge start time (1)
            result["reg_95"] = _hreg(upper_resp, 95, 60)   # charge finish time (1)
            result["reg_96"] = _hreg(upper_resp, 96, 60)   # scheduled charge enable

        return result

    except Exception:
        return None


def _parse_input_registers(resp: bytes) -> dict:
    serial_bytes = resp[28:38]
    serial = serial_bytes.decode("ascii", errors="replace").rstrip("\x00")
    status_val = _reg(resp, 0) % 5
    status_names = ["idle", "normal", "warning", "fault", "flash"]
    ac_voltage = _reg(resp, 5) / 10.0
    ac_freq = _reg(resp, 13) / 100.0
    pv1 = _reg(resp, 18)
    pv2 = _reg(resp, 20)
    grid_raw = _signed16(_reg(resp, 30))
    eps_power = _reg(resp, 31)
    inverter_temp = _signed16(_reg(resp, 41)) / 10.0
    battery_temp = _signed16(_reg(resp, 56)) / 10.0
    battery_percent = _reg(resp, 59)
    reg42 = _reg(resp, 42)
    reg52 = _reg(resp, 52)
    is_ac_coupled = serial.startswith("C")
    if not is_ac_coupled:
        reg42 += eps_power
    grid = -grid_raw
    solar = pv1 + pv2
    if solar < 10:
        solar = 0
    battery = reg42 - grid - solar
    if reg52 == 0:
        battery = 0
    house = battery + grid + solar
    export_today = _reg(resp, 25) / 10.0
    import_today = _reg(resp, 26) / 10.0
    charge_today = _reg(resp, 36) / 10.0
    discharge_today = _reg(resp, 37) / 10.0
    gen_today = (_reg(resp, 17) + _reg(resp, 19)) / 10.0
    return {
        "serial": serial, "status": status_names[status_val],
        "solar_w": solar, "house_w": house, "battery_w": battery, "grid_w": grid,
        "battery_percent": battery_percent, "battery_temp_c": battery_temp,
        "inverter_temp_c": inverter_temp, "ac_voltage": ac_voltage,
        "ac_frequency_hz": ac_freq, "eps_w": eps_power, "pv1_w": pv1, "pv2_w": pv2,
        "energy_today_kwh": {
            "generated": gen_today, "exported": export_today,
            "imported": import_today, "charge": charge_today,
            "discharge": discharge_today,
        },
    }


def write_register(ip: str, register: int, value: int, port: int = PORT) -> bool:
    """Write a single holding register (function code 6). Opens a new connection."""
    frame = _build_frame(6, register, value)
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5.0)
        sock.connect((ip, port))
        sock.sendall(frame)
        resp = _recv_all(sock)
        sock.close()
        return resp is not None and len(resp) >= 44
    except Exception:
        return False


def write_registers(ip: str, reg_value_pairs: list[tuple[int, int]],
                    port: int = PORT, delay: float = 0.3) -> list[dict]:
    """Write multiple holding registers on a single TCP connection.

    Each write is sent sequentially with a small delay between them,
    matching the BBC BASIC original's behaviour of reusing one socket.
    Each write gets an echo response confirming success.

    Returns a list of result dicts: [{"register": R, "value": V, "ok": bool}, ...]
    """
    results = []
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5.0)
        sock.connect((ip, port))

        for i, (register, value) in enumerate(reg_value_pairs):
            frame = _build_frame(6, register, value)
            sock.sendall(frame)
            resp = _recv_all(sock)
            ok = resp is not None and len(resp) >= 44
            results.append({"register": register, "value": value, "ok": ok})
            if not ok:
                # Stop on first failure — don't send more writes on a broken connection
                for remaining_reg, remaining_val in reg_value_pairs[i + 1:]:
                    results.append({"register": remaining_reg, "value": remaining_val,
                                    "ok": False, "skipped": True})
                break
            if i < len(reg_value_pairs) - 1:
                time.sleep(delay)

        sock.close()
    except Exception:
        # If connection failed entirely, mark all unsent as failed
        sent = len(results)
        for register, value in reg_value_pairs[sent:]:
            results.append({"register": register, "value": value, "ok": False})
    return results


def watts_to_register(watts: int, scaling: int) -> int:
    """Convert watts to the 0-50 register value. 50 = unlimited."""
    if scaling <= 0:
        return 50
    val = round(watts / scaling)
    return max(0, min(50, val))
