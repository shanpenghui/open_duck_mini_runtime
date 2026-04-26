import time

import numpy as np
import rustypot
from mini_bdx_runtime.duck_config import DuckConfig


class FeetechSTS3215Adapter:
    def __init__(self, usb_port: str, baudrate: int = 1000000, timeout: float = 0.05):
        if hasattr(rustypot, "feetech"):
            self.io = rustypot.feetech(usb_port, baudrate)
            self.legacy_api = True
        else:
            self.io = rustypot.Sts3215PyController(usb_port, baudrate, timeout)
            self.legacy_api = False

    def set_kps(self, ids, values):
        if self.legacy_api:
            return self.io.set_kps(ids, values)
        return self.io.sync_write_p_coefficient(ids, [int(round(value)) for value in values])

    def set_kds(self, ids, values):
        if self.legacy_api:
            return self.io.set_kds(ids, values)
        return self.io.sync_write_d_coefficient(ids, [int(round(value)) for value in values])

    def write_goal_position(self, ids, values):
        if self.legacy_api:
            return self.io.write_goal_position(ids, values)
        if len(ids) == 1:
            return self.io.write_goal_position(ids[0], values[0])
        return self.io.sync_write_goal_position(ids, values)

    def read_present_position(self, ids):
        if self.legacy_api:
            return self.io.read_present_position(ids)
        return self.io.sync_read_present_position(ids)

    def read_present_velocity(self, ids):
        if self.legacy_api:
            return self.io.read_present_velocity(ids)
        return self.io.sync_read_present_speed(ids)

    def read_present_voltage(self, ids):
        if self.legacy_api:
            return self.io.read_present_voltage(ids)
        return self.io.sync_read_present_voltage(ids)

    def read_present_current(self, ids):
        if self.legacy_api:
            if hasattr(self.io, "read_present_current"):
                return self.io.read_present_current(ids)
            return self.io.get_present_current(ids)
        return self.io.sync_read_present_current(ids)

    def disable_torque(self, ids):
        if self.legacy_api:
            return self.io.disable_torque(ids)
        return self.io.sync_write_torque_enable(ids, [False] * len(ids))


class HWI:
    def __init__(self, duck_config: DuckConfig, usb_port: str = "/dev/ttyACM0"):

        self.duck_config = duck_config

        # Order matters here
        self.joints = {
            "left_hip_yaw": 20,
            "left_hip_roll": 21,
            "left_hip_pitch": 22,
            "left_knee": 23,
            "left_ankle": 24,
            "neck_pitch": 30,
            "head_pitch": 31,
            "head_yaw": 32,
            "head_roll": 33,
            # "left_antenna": None,
            # "right_antenna": None,
            "right_hip_yaw": 10,
            "right_hip_roll": 11,
            "right_hip_pitch": 12,
            "right_knee": 13,
            "right_ankle": 14,
        }

        self.zero_pos = {
            "left_hip_yaw": 0,
            "left_hip_roll": 0,
            "left_hip_pitch": 0,
            "left_knee": 0,
            "left_ankle": 0,
            "neck_pitch": 0,
            "head_pitch": 0,
            "head_yaw": 0,
            "head_roll": 0,
            # "left_antenna":0,
            # "right_antenna":0,
            "right_hip_yaw": 0,
            "right_hip_roll": 0,
            "right_hip_pitch": 0,
            "right_knee": 0,
            "right_ankle": 0,
        }

        self.init_pos = {
            "left_hip_yaw": 0.002,
            "left_hip_roll": 0.053,
            "left_hip_pitch": -0.63,
            "left_knee": 1.368,
            "left_ankle": -0.784,
            "neck_pitch": 0.0,
            "head_pitch": 0.0,
            "head_yaw": 0,
            "head_roll": 0,
            # "left_antenna": 0,
            # "right_antenna": 0,
            "right_hip_yaw": -0.003,
            "right_hip_roll": -0.065,
            "right_hip_pitch": 0.635,
            "right_knee": 1.379,
            "right_ankle": -0.796,
        }

        self.joints_offsets = self.duck_config.joints_offset

        self.kps = np.ones(len(self.joints)) * 32  # default kp
        self.kds = np.ones(len(self.joints)) * 0  # default kd
        self.low_torque_kps = np.ones(len(self.joints)) * 2

        self.io = FeetechSTS3215Adapter(usb_port, 1000000)

        # Track which servos failed to configure
        self.failed_servos = set()
        self.joint_ids = tuple(self.joints.values())
        self.joint_names = tuple(self.joints.keys())
        self.joint_name_by_id = {sid: name for name, sid in self.joints.items()}
        self.position_fallback_delay = 0.0005
        self._last_position_write_warning = 0.0

    def _servo_name(self, servo_id):
        return self.joint_name_by_id.get(servo_id, str(servo_id))

    def _write_servo_with_retry(self, write_fn, servo_id, max_retries=3, delay=0.1):
        """Write to a single servo with retry logic. Returns True on success."""
        for attempt in range(max_retries):
            try:
                write_fn(servo_id)
                return True
            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(delay)
                else:
                    name = self._servo_name(servo_id)
                    print(f"[WARN] {write_fn.__name__ if hasattr(write_fn, '__name__') else 'write'} servo {servo_id} ({name}) failed after {max_retries} retries: {e}")
                    return False

    def set_kps(self, kps):
        self.kps = kps
        self.failed_servos.clear()
        for i, (name, sid) in enumerate(self.joints.items()):
            ok = self._write_servo_with_retry(
                lambda s=sid, k=self.kps[i]: self.io.set_kps([s], [k]),
                sid, max_retries=3, delay=0.1
            )
            if not ok:
                self.failed_servos.add(sid)
                print(f"[ERROR] set_kps failed for servo {sid} ({name})! Check wiring!")
            time.sleep(0.01)

    def set_kds(self, kds):
        self.kds = kds
        for i, (name, sid) in enumerate(self.joints.items()):
            ok = self._write_servo_with_retry(
                lambda s=sid, k=self.kds[i]: self.io.set_kds([s], [k]),
                sid, max_retries=3, delay=0.1
            )
            if not ok:
                self.failed_servos.add(sid)
                print(f"[ERROR] set_kds failed for servo {sid} ({name})! Check wiring!")
            time.sleep(0.01)

    def set_kp(self, id, kp):
        self.io.set_kps([id], [kp])

    def turn_on(self):
        # Phase 1: low kps (safe torque)
        for i, sid in enumerate(self.joint_ids):
            self._write_servo_with_retry(
                lambda s=sid, k=self.low_torque_kps[i]: self.io.set_kps([s], [k]),
                sid, max_retries=3, delay=0.1
            )
            time.sleep(0.01)
        print("turn on : low KPS set")
        time.sleep(1)

        # Phase 2: move to init position (one-by-one)
        self.set_position_all(self.init_pos)
        print("turn on : init pos set")

        time.sleep(1)

        # Phase 3: high kps (full torque)
        for i, sid in enumerate(self.joint_ids):
            self._write_servo_with_retry(
                lambda s=sid, k=self.kps[i]: self.io.set_kps([s], [k]),
                sid, max_retries=3, delay=0.1
            )
            time.sleep(0.01)
        print("turn on : high kps")

        # Report failed servos
        if self.failed_servos:
            names = []
            for sid in self.failed_servos:
                names.append(f"{sid}({self._servo_name(sid)})")
            print(f"[FATAL] Servos with communication errors: {', '.join(names)}")
            print("[FATAL] Check wiring and connections! Aborting.")
            raise RuntimeError(
                f"Servo communication failed for IDs: {self.failed_servos}. "
                f"Check wiring before restarting."
            )

    def turn_off(self):
        self.io.disable_torque(list(self.joint_ids))

    def set_position(self, joint_name, pos):
        """
        pos is in radians
        """
        id = self.joints[joint_name]
        pos = pos + self.joints_offsets[joint_name]
        self.io.write_goal_position([id], [pos])

    def set_position_all(self, joints_positions):
        """
        joints_positions is a dictionary with joint names as keys and joint positions as values
        Warning: expects radians
        Uses fast bulk writes during normal walking and falls back to one-by-one writes
        if a transient bus error or unresponsive servo breaks the bulk transaction.
        """
        ids = []
        positions = []
        for joint, position in joints_positions.items():
            ids.append(self.joints[joint])
            positions.append(position + self.joints_offsets[joint])

        try:
            self.io.write_goal_position(ids, positions)
            return
        except Exception as e:
            now = time.monotonic()
            if now - self._last_position_write_warning > 1.0:
                print(f"[WARN] bulk goal position write failed, falling back to single writes: {e}")
                self._last_position_write_warning = now

        for sid, pos in zip(ids, positions):
            try:
                self.io.write_goal_position([sid], [pos])
            except Exception:
                pass
            if self.position_fallback_delay:
                time.sleep(self.position_fallback_delay)

    def get_present_positions(self, ignore=None):
        """
        Returns the present positions in radians
        """
        ignore = set(ignore or [])

        try:
            present_positions = self.io.read_present_position(
                list(self.joint_ids)
            )
        except Exception as e:
            print(e)
            return None

        present_positions = [
            pos - self.joints_offsets[joint]
            for joint, pos in zip(self.joint_names, present_positions)
            if joint not in ignore
        ]
        return np.array(np.around(present_positions, 3))

    def get_present_velocities(self, rad_s=True, ignore=None):
        """
        Returns the present velocities in rad/s (default) or rev/min
        """
        ignore = set(ignore or [])

        try:
            present_velocities = self.io.read_present_velocity(
                list(self.joint_ids)
            )
        except Exception as e:
            print(e)
            return None

        present_velocities = [
            vel
            for joint, vel in zip(self.joint_names, present_velocities)
            if joint not in ignore
        ]

        return np.array(np.around(present_velocities, 3))

    def get_present_voltages(self, ignore=None):
        """
        Returns present servo bus voltages in volts.
        """
        ignore = set(ignore or [])

        try:
            present_voltages = self.io.read_present_voltage(list(self.joint_ids))
        except Exception as e:
            print(e)
            return None

        voltages = [
            voltage * 0.1
            for joint, voltage in zip(self.joint_names, present_voltages)
            if joint not in ignore
        ]
        return np.array(np.around(voltages, 2))

    def get_present_currents(self, ignore=None):
        """
        Returns present servo currents in amps.
        STS3215 current feedback uses 6.5mA per raw unit.
        """
        ignore = set(ignore or [])

        try:
            present_currents = self.io.read_present_current(list(self.joint_ids))
        except Exception as e:
            print(e)
            return None

        currents = [
            current * 0.0065
            for joint, current in zip(self.joint_names, present_currents)
            if joint not in ignore
        ]
        return np.array(np.around(currents, 3))
