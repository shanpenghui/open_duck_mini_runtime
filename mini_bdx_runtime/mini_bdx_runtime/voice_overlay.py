from __future__ import annotations

import json
import math
import socket
import threading
import time
from dataclasses import dataclass
from typing import Any


HOST = "127.0.0.1"
PORT = 18765
JOYSTICK_COMMAND_ID = "joystick_velocity"
TURN_DEGREES_COMMAND_ID = "turn_degrees"
DEFAULT_JOYSTICK_TTL_S = 0.35
MAX_JOYSTICK_TTL_S = 1.0
MIN_TURN_DEGREES = 5.0
MAX_TURN_DEGREES = 180.0
DEFAULT_TURN_YAW = 0.55
TURN_GYRO_SIGN = 1.0
TURN_GYRO_NOISE_DPS = 2.0
TURN_GYRO_MISSING_TIMEOUT_S = 0.4
MAX_DURATION_S = {
    "walk_forward_step": 0.5,
    "walk_forward_steps": 2.5,
    "turn_left_small": 0.3,
    "turn_right_small": 0.3,
    "turn_left_3s": 3.0,
    "turn_right_3s": 3.0,
    TURN_DEGREES_COMMAND_ID: 6.0,
    "strafe_left_step": 0.5,
    "strafe_right_step": 0.5,
    "head_pitch_up": 0.5,
    "head_pitch_down": 0.5,
    "head_yaw_left": 0.5,
    "head_yaw_right": 0.5,
    "head_center": 0.5,
    JOYSTICK_COMMAND_ID: MAX_JOYSTICK_TTL_S,
}
DEFAULT_COOLDOWN_S = 1.0
STOP_HOLD_S = 0.5
HEAD_COMMANDS = {
    "head_pitch_up",
    "head_pitch_down",
    "head_yaw_left",
    "head_yaw_right",
    "head_center",
}


@dataclass
class ActiveOverlay:
    command_id: str
    until: float
    params: dict[str, float]


class VoiceOverlay:
    def __init__(self, host: str = HOST, port: int = PORT, initial_paused: bool = False):
        self.host = host
        self.port = port
        self._lock = threading.Lock()
        self._paused = initial_paused
        self._active: ActiveOverlay | None = None
        self._button_request: str | None = None
        self._stop_until = 0.0
        self._cooldown_until: dict[str, float] = {}
        self._stop_event = threading.Event()
        self._server_socket: socket.socket | None = None
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def apply(self, commands, buttons, paused: bool, gyro_z_rad_s: float | None = None):
        now = time.monotonic()
        output = commands.copy() if hasattr(commands, "copy") else list(commands)

        with self._lock:
            self._paused = paused

            if self._button_request == "pause":
                if not paused:
                    buttons.A.triggered = True
                self._paused = True
                self._button_request = None
            elif self._button_request == "resume":
                if paused:
                    buttons.A.triggered = True
                self._paused = False
                self._button_request = None

            if now < self._stop_until:
                self._zero_commands(output)
                return output, buttons

            if self._active is None:
                return output, buttons

            if now >= self._active.until:
                self._active = None
                return output, buttons

            if self._active.command_id in HEAD_COMMANDS:
                self._apply_head_overlay(output, self._active)
            elif self._active.command_id == TURN_DEGREES_COMMAND_ID:
                self._zero_walk_axes(output)
                done = self._apply_turn_degrees_overlay(output, self._active, gyro_z_rad_s, now)
                if done:
                    self._active = None
                    self._zero_walk_axes(output)
            else:
                self._zero_walk_axes(output)
                self._apply_body_overlay(output, self._active)

        return output, buttons

    def close(self):
        self._stop_event.set()
        sock = self._server_socket
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def _serve(self):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                self._server_socket = sock
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((self.host, self.port))
                sock.listen(4)
                sock.settimeout(0.5)
                print(f"[VoiceOverlay] listening on {self.host}:{self.port}")
                while not self._stop_event.is_set():
                    try:
                        conn, addr = sock.accept()
                    except socket.timeout:
                        continue
                    except OSError:
                        break
                    with conn:
                        self._handle_connection(conn, addr)
        except OSError as exc:
            print(f"[VoiceOverlay] disabled: {exc}")
        finally:
            self._server_socket = None

    def _handle_connection(self, conn: socket.socket, addr):
        try:
            conn.settimeout(0.5)
            raw = self._read_request(conn)
            payload = json.loads(raw.decode("utf-8"))
            response = self._handle_payload(payload)
        except Exception as exc:
            response = {
                "ok": False,
                "message": f"invalid voice overlay request: {exc}",
            }

        try:
            conn.sendall((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))
        except OSError:
            pass

    def _read_request(self, conn: socket.socket) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while total < 4096:
            chunk = conn.recv(512)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if b"\n" in chunk:
                break
        if not chunks:
            raise ValueError("empty request")
        return b"".join(chunks).split(b"\n", 1)[0]

    def _handle_payload(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return self._fail("request must be a JSON object")
        if payload.get("type") != "robot_command":
            return self._fail("unsupported request type")

        command_id = payload.get("command_id")
        if not isinstance(command_id, str):
            return self._fail("command_id is required")

        params = payload.get("params") or {}
        if not isinstance(params, dict):
            return self._fail("params must be an object")

        now = time.monotonic()
        with self._lock:
            if command_id == "stop":
                self._active = None
                self._stop_until = now + STOP_HOLD_S
                return self._ok("\u5df2\u505c\u6b62")

            if command_id == "pause":
                if self._paused:
                    return self._ok("\u5f53\u524d\u5df2\u6682\u505c")
                self._active = None
                self._paused = True
                self._stop_until = now + STOP_HOLD_S
                self._button_request = "pause"
                return self._ok("\u5df2\u6682\u505c")

            if command_id == "resume":
                if not self._paused:
                    return self._ok("\u5f53\u524d\u5df2\u7ee7\u7eed")
                self._paused = False
                self._button_request = "resume"
                return self._ok("\u5df2\u7ee7\u7eed")

            if command_id == "walk_forward_steps_over_limit":
                max_steps = int(float(params.get("max_steps", 5)))
                return self._fail(f"\u6b65\u6570\u592a\u591a\uff0c\u6700\u591a\u652f\u6301{max_steps}\u6b65")

            if command_id == "turn_degrees_over_limit":
                min_degrees = params.get("min_degrees")
                max_degrees = params.get("max_degrees")
                if max_degrees is not None:
                    return self._fail(f"\u8f6c\u5411\u89d2\u5ea6\u592a\u5927\uff0c\u6700\u591a\u652f\u6301{int(float(max_degrees))}\u5ea6")
                if min_degrees is not None:
                    return self._fail(f"\u8f6c\u5411\u89d2\u5ea6\u592a\u5c0f\uff0c\u81f3\u5c11\u9700\u8981{int(float(min_degrees))}\u5ea6")
                return self._fail("\u8f6c\u5411\u89d2\u5ea6\u4e0d\u5728\u5b89\u5168\u8303\u56f4\u5185")

            if command_id not in MAX_DURATION_S:
                return self._fail("unsupported robot command")

            is_head_command = command_id in HEAD_COMMANDS
            if self._paused and not is_head_command:
                return self._fail("\u5f53\u524d\u5df2\u6682\u505c\uff0c\u8bf7\u5148\u8bf4\u7ee7\u7eed")

            if command_id == JOYSTICK_COMMAND_ID:
                ttl_s = self._clamp_float(
                    params.get("ttl_s"),
                    default=DEFAULT_JOYSTICK_TTL_S,
                    low=0.0,
                    high=MAX_JOYSTICK_TTL_S,
                )
                active_params, message = self._build_active_params(command_id, params)
                self._active = ActiveOverlay(
                    command_id=command_id,
                    until=now + ttl_s,
                    params=active_params,
                )
                return self._ok(message)

            cooldown_until = self._cooldown_until.get(command_id, 0.0)
            if now < cooldown_until:
                return self._fail("\u52a8\u4f5c\u51b7\u5374\u4e2d\uff0c\u8bf7\u7a0d\u540e\u518d\u8bd5")

            duration_s = self._clamp_float(
                params.get("duration_s"),
                default=MAX_DURATION_S[command_id],
                low=0.0,
                high=MAX_DURATION_S[command_id],
            )
            default_cooldown = 0.5 if is_head_command else DEFAULT_COOLDOWN_S
            if command_id in {"turn_left_3s", "turn_right_3s"}:
                default_cooldown = 2.0
            if command_id == TURN_DEGREES_COMMAND_ID:
                default_cooldown = 1.5
            cooldown_s = self._clamp_float(params.get("cooldown_s"), default=default_cooldown, low=0.0, high=5.0)

            active_params, message = self._build_active_params(command_id, params)

            self._active = ActiveOverlay(
                command_id=command_id,
                until=now + duration_s,
                params=active_params,
            )
            self._cooldown_until[command_id] = now + duration_s + cooldown_s
            return self._ok(message)

    def _zero_commands(self, commands):
        for index in range(min(7, len(commands))):
            commands[index] = 0.0

    def _zero_walk_axes(self, commands):
        for index in range(min(3, len(commands))):
            commands[index] = 0.0

    def _apply_body_overlay(self, commands, active: ActiveOverlay):
        if active.command_id == JOYSTICK_COMMAND_ID:
            commands[0] = active.params["lin_x"]
            commands[1] = active.params["lin_y"]
            commands[2] = active.params["yaw"]
        elif active.command_id in {"walk_forward_step", "walk_forward_steps"}:
            commands[0] = active.params["lin_x"]
        elif active.command_id in {"turn_left_small", "turn_right_small", "turn_left_3s", "turn_right_3s"}:
            commands[2] = active.params["yaw"]
        elif active.command_id in {"strafe_left_step", "strafe_right_step"}:
            commands[1] = active.params["lin_y"]

    def _apply_head_overlay(self, commands, active: ActiveOverlay):
        if active.command_id == "head_center":
            for index in range(3, min(7, len(commands))):
                commands[index] = 0.0
            return
        for key, index in (
            ("neck_pitch", 3),
            ("head_pitch", 4),
            ("head_yaw", 5),
            ("head_roll", 6),
        ):
            if key in active.params:
                commands[index] = active.params[key]

    def _build_active_params(self, command_id: str, params: dict[str, Any]) -> tuple[dict[str, float], str]:
        if command_id == JOYSTICK_COMMAND_ID:
            lin_x = self._clamp_float(params.get("lin_x"), default=0.0, low=0.0, high=0.15)
            lin_y = self._clamp_float(params.get("lin_y"), default=0.0, low=-0.2, high=0.2)
            yaw = self._clamp_float(params.get("yaw"), default=0.0, low=-1.0, high=1.0)
            return {"lin_x": lin_x, "lin_y": lin_y, "yaw": yaw}, "\u5df2\u63a5\u6536\u6447\u6746\u901f\u5ea6"
        if command_id in {"walk_forward_step", "walk_forward_steps"}:
            lin_x = self._clamp_float(params.get("lin_x"), default=0.15, low=0.0, high=0.15)
            steps = int(float(params.get("steps", 1)))
            return {"lin_x": lin_x}, f"\u5df2\u5411\u524d\u8d70{steps}\u6b65"
        if command_id in {"turn_left_small", "turn_left_3s"}:
            yaw = self._clamp_float(params.get("yaw"), default=1.0, low=0.0, high=1.0)
            message = "\u5df2\u5de6\u8f6c\u4e00\u70b9" if command_id == "turn_left_small" else "\u5df2\u539f\u5730\u5de6\u8f6c"
            return {"yaw": yaw}, message
        if command_id in {"turn_right_small", "turn_right_3s"}:
            yaw = self._clamp_float(params.get("yaw"), default=-1.0, low=-1.0, high=0.0)
            message = "\u5df2\u53f3\u8f6c\u4e00\u70b9" if command_id == "turn_right_small" else "\u5df2\u539f\u5730\u53f3\u8f6c"
            return {"yaw": yaw}, message
        if command_id == TURN_DEGREES_COMMAND_ID:
            degrees = self._clamp_float(params.get("degrees"), default=30.0, low=MIN_TURN_DEGREES, high=MAX_TURN_DEGREES)
            direction = 1.0 if self._clamp_float(params.get("direction"), default=1.0, low=-1.0, high=1.0) >= 0 else -1.0
            yaw = abs(self._clamp_float(params.get("yaw"), default=DEFAULT_TURN_YAW, low=-0.7, high=0.7))
            yaw = max(0.2, min(0.7, yaw))
            side = "\u5de6" if direction > 0 else "\u53f3"
            return {
                "degrees": degrees,
                "direction": direction,
                "yaw": yaw,
                "turned_degrees": 0.0,
                "last_update": 0.0,
                "missing_since": 0.0,
            }, f"\u5df2\u5f00\u59cb\u539f\u5730{side}\u8f6c{degrees:.0f}\u5ea6"
        if command_id == "strafe_left_step":
            lin_y = self._clamp_float(params.get("lin_y"), default=0.2, low=0.0, high=0.2)
            return {"lin_y": lin_y}, "\u5df2\u5411\u5de6\u8d70\u4e00\u6b65"
        if command_id == "strafe_right_step":
            lin_y = self._clamp_float(params.get("lin_y"), default=-0.2, low=-0.2, high=0.0)
            return {"lin_y": lin_y}, "\u5df2\u5411\u53f3\u8d70\u4e00\u6b65"
        if command_id == "head_pitch_up":
            value = self._clamp_float(params.get("head_pitch"), default=0.3, low=0.0, high=0.3)
            return {"head_pitch": value}, "\u5df2\u62ac\u5934"
        if command_id == "head_pitch_down":
            value = self._clamp_float(params.get("head_pitch"), default=-0.78, low=-0.78, high=0.0)
            return {"head_pitch": value}, "\u5df2\u4f4e\u5934"
        if command_id == "head_yaw_left":
            value = self._clamp_float(params.get("head_yaw"), default=0.5, low=0.0, high=0.5)
            return {"head_yaw": value}, "\u5df2\u770b\u5de6\u8fb9"
        if command_id == "head_yaw_right":
            value = self._clamp_float(params.get("head_yaw"), default=-0.5, low=-0.5, high=0.0)
            return {"head_yaw": value}, "\u5df2\u770b\u53f3\u8fb9"
        if command_id == "head_center":
            return {"neck_pitch": 0.0, "head_pitch": 0.0, "head_yaw": 0.0, "head_roll": 0.0}, "\u5df2\u5934\u56de\u6b63"
        return {}, "ok"

    def _apply_turn_degrees_overlay(self, commands, active: ActiveOverlay, gyro_z_rad_s: float | None, now: float) -> bool:
        last_update = active.params.get("last_update", 0.0)
        if last_update:
            dt = max(0.0, min(0.2, now - last_update))
        else:
            dt = 0.0
        active.params["last_update"] = now

        yaw_rate_dps = self._gyro_z_to_dps(gyro_z_rad_s)
        if yaw_rate_dps is None:
            missing_since = active.params.get("missing_since", 0.0) or now
            active.params["missing_since"] = missing_since
            if now - missing_since > TURN_GYRO_MISSING_TIMEOUT_S:
                print("[VoiceOverlay] turn_degrees stopped: missing gyro_z")
                return True
        else:
            active.params["missing_since"] = 0.0
            if abs(yaw_rate_dps) >= TURN_GYRO_NOISE_DPS:
                active.params["turned_degrees"] += abs(yaw_rate_dps) * dt

        target = active.params["degrees"]
        remaining = target - active.params["turned_degrees"]
        if remaining <= 0:
            print(f"[VoiceOverlay] turn_degrees done: target={target:.1f}deg")
            return True

        yaw = active.params["yaw"]
        if remaining < 8.0:
            yaw *= 0.55
        commands[2] = active.params["direction"] * yaw
        return False

    def _gyro_z_to_dps(self, gyro_z_rad_s: float | None) -> float | None:
        try:
            value = float(gyro_z_rad_s)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(value):
            return None
        return math.degrees(value) * TURN_GYRO_SIGN

    def _clamp_float(self, value, *, default: float, low: float, high: float) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = default
        return max(low, min(high, number))

    def _ok(self, message: str) -> dict[str, Any]:
        return {"ok": True, "message": message}

    def _fail(self, message: str) -> dict[str, Any]:
        return {"ok": False, "message": message}
