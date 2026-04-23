import time

import numpy as np
import rustypot
from mini_bdx_runtime.duck_config import DuckConfig


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

        self.io = rustypot.feetech(usb_port, 1000000)

        # Track which servos failed to configure
        self.failed_servos = set()

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
                    joint_name = [k for k, v in self.joints.items() if v == servo_id]
                    name = joint_name[0] if joint_name else str(servo_id)
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
        ids = list(self.joints.values())
        # Phase 1: low kps (safe torque)
        for i, sid in enumerate(ids):
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
        for i, sid in enumerate(ids):
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
                jname = [k for k, v in self.joints.items() if v == sid]
                names.append(f"{sid}({jname[0]})" if jname else str(sid))
            print(f"[FATAL] Servos with communication errors: {', '.join(names)}")
            print("[FATAL] Check wiring and connections! Aborting.")
            raise RuntimeError(
                f"Servo communication failed for IDs: {self.failed_servos}. "
                f"Check wiring before restarting."
            )

    def turn_off(self):
        self.io.disable_torque(list(self.joints.values()))

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
        Writes one-by-one to avoid bulk write timeout issues with unresponsive servos.
        """
        ids_positions = {
            self.joints[joint]: position + self.joints_offsets[joint]
            for joint, position in joints_positions.items()
        }

        # One-by-one writes to avoid one bad servo disrupting all joints
        for sid, pos in ids_positions.items():
            try:
                self.io.write_goal_position([sid], [pos])
            except Exception as e:
                # Don't crash on transient errors during main loop, just warn
                pass
            time.sleep(0.001)  # minimal delay to not overload serial bus

    def get_present_positions(self, ignore=[]):
        """
        Returns the present positions in radians
        """

        try:
            present_positions = self.io.read_present_position(
                list(self.joints.values())
            )
        except Exception as e:
            print(e)
            return None

        present_positions = [
            pos - self.joints_offsets[joint]
            for joint, pos in zip(self.joints.keys(), present_positions)
            if joint not in ignore
        ]
        return np.array(np.around(present_positions, 3))

    def get_present_velocities(self, rad_s=True, ignore=[]):
        """
        Returns the present velocities in rad/s (default) or rev/min
        """
        try:
            present_velocities = self.io.read_present_velocity(
                list(self.joints.values())
            )
        except Exception as e:
            print(e)
            return None

        present_velocities = [
            vel
            for joint, vel in zip(self.joints.keys(), present_velocities)
            if joint not in ignore
        ]

        return np.array(np.around(present_velocities, 3))
