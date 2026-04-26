import time
import pickle

import numpy as np
from mini_bdx_runtime.rustypot_position_hwi import HWI
from mini_bdx_runtime.onnx_infer import OnnxInfer

from mini_bdx_runtime.raw_imu import Imu
from mini_bdx_runtime.poly_reference_motion import PolyReferenceMotion
from mini_bdx_runtime.xbox_controller import XBoxController
from mini_bdx_runtime.feet_contacts import FeetContacts
from mini_bdx_runtime.eyes import Eyes
from mini_bdx_runtime.sounds import Sounds
from mini_bdx_runtime.antennas import Antennas
from mini_bdx_runtime.projector import Projector
from mini_bdx_runtime.rl_utils import make_action_dict, LowPassActionFilter
from mini_bdx_runtime.duck_config import DuckConfig

import os

HOME_DIR = os.path.expanduser("~")

# Auto-detect the runtime directory (wherever this repo is cloned)
# Walk up from this script to find the repo root
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(_SCRIPT_DIR)  # scripts/ -> repo root

# For headless environments (RPi without display), SDL must use dummy drivers
# This prevents pygame.init() from hanging when no display is available
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")


class RLWalk:
    def __init__(
            self,
            onnx_model_path: str,
            duck_config_path: str = f"{HOME_DIR}/duck_config.json",
            serial_port: str = "/dev/ttyACM0",
            control_freq: float = 50,
            pid=[30, 0, 0],
            action_scale=0.25,
            commands=False,
            pitch_bias=0,
            save_obs=False,
            replay_obs=None,
            cutoff_frequency=None,
            min_motor_voltage=6.8,
            power_log_interval=0.0,
    ):

        self.duck_config = DuckConfig(config_json_path=duck_config_path)

        self.commands = commands
        self.pitch_bias = pitch_bias

        self.onnx_model_path = onnx_model_path
        self.policy = OnnxInfer(self.onnx_model_path, awd=True)

        self.num_dofs = 14
        self.max_motor_velocity = 5.24  # rad/s

        # Control
        self.control_freq = control_freq
        self.pid = pid

        self.save_obs = save_obs
        if self.save_obs:
            self.saved_obs = []

        self.replay_obs = replay_obs
        if self.replay_obs is not None:
            self.replay_obs = pickle.load(open(self.replay_obs, "rb"))

        self.action_filter = None
        if cutoff_frequency is not None:
            self.action_filter = LowPassActionFilter(
                self.control_freq, cutoff_frequency
            )

        self.hwi = HWI(self.duck_config, serial_port)
        # print(f"[INFO] Joints in HWI: {list(self.hwi.joints.keys())}")

        self.start()

        self.imu = Imu(
            sampling_freq=int(self.control_freq),
            user_pitch_bias=self.pitch_bias,
            upside_down=self.duck_config.imu_upside_down,
        )

        self.feet_contacts = FeetContacts()

        # Scales
        self.action_scale = action_scale
        self.min_motor_voltage = min_motor_voltage
        self.voltage_check_interval = max(1, int(self.control_freq))
        self.power_log_interval = power_log_interval
        self.power_log_interval_steps = (
            max(1, int(self.control_freq * self.power_log_interval))
            if self.power_log_interval > 0
            else 0
        )

        self.last_action = np.zeros(self.num_dofs)
        self.last_last_action = np.zeros(self.num_dofs)
        self.last_last_last_action = np.zeros(self.num_dofs)

        self.init_pos = list(self.hwi.init_pos.values())

        self.motor_targets = np.array(self.init_pos.copy())
        self.prev_motor_targets = np.array(self.init_pos.copy())

        self.last_commands = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

        self.paused = self.duck_config.start_paused

        self.command_freq = 20  # hz
        if self.commands:
            self.xbox_controller = XBoxController(self.command_freq)

        # Reference motion, but we only really need the length of one phase
        # TODO
        self.PRM = PolyReferenceMotion(os.path.join(REPO_DIR, "scripts", "polynomial_coefficients.pkl"))
        self.imitation_i = 0
        self.imitation_phase = np.array([0, 0])
        self.phase_frequency_factor = 1.0
        self.phase_frequency_factor_offset = (
            self.duck_config.phase_frequency_factor_offset
        )

        # Optional expression features
        if self.duck_config.eyes:
            self.eyes = Eyes()
        if self.duck_config.projector:
            self.projector = Projector()
        if self.duck_config.speaker:
            self.sounds = Sounds(
                volume=2.0, sound_directory=os.path.join(REPO_DIR, "mini_bdx_runtime", "assets/")
            )
        if self.duck_config.antennas:
            self.antennas = Antennas()

    def get_obs(self):

        imu_data = self.imu.get_data()
        gyro_deg = np.degrees(imu_data["gyro"])
        # imu_data["gyro"][1] = imu_data["gyro"][1]*1.1
        # print("[IMU] gyro_deg:", gyro_deg)
        # 加限幅 & 异常值剔除
        # 限幅过滤：加速度最大 ±10 m/s²，陀螺仪最大 ±5 rad/s
        accel = np.clip(imu_data["accelero"], -10, 10)
        gyro = np.clip(imu_data["gyro"], -5, 5)
        # 简单离群值剔除：超过阈值认为是错误，直接用上一个值（需要保留）
        if hasattr(self, "last_good_gyro"):
            if np.any(np.abs(gyro - self.last_good_gyro) > 2.0):  # 限波动
                gyro = self.last_good_gyro
            else:
                self.last_good_gyro = gyro.copy()
        else:
            self.last_good_gyro = gyro.copy()

        if hasattr(self, "last_good_accel"):
            if np.any(np.abs(accel) > 9.8):
                accel = self.last_good_accel
            else:
                self.last_good_accel = accel.copy()
        else:
            self.last_good_accel = accel.copy()
        # 替换原始数据
        imu_data["gyro"] = gyro
        imu_data["accelero"] = accel

        # print(f"[IMU DEBUG] Gyro Z: {imu_data['gyro'][2]:.4f}, Accel X: {imu_data['accelero'][0]:.4f}")

        dof_pos = self.hwi.get_present_positions(
            ignore=[
                "left_antenna",
                "right_antenna",
            ]
        )  # rad

        dof_vel = self.hwi.get_present_velocities(
            ignore=[
                "left_antenna",
                "right_antenna",
            ]
        )  # rad/s

        if dof_pos is None or dof_vel is None:
            return None

        if len(dof_pos) != self.num_dofs:
            print(f"ERROR len(dof_pos) != {self.num_dofs}")
            return None

        if len(dof_vel) != self.num_dofs:
            print(f"ERROR len(dof_vel) != {self.num_dofs}")
            return None

        cmds = self.last_commands

        feet_contacts = self.feet_contacts.get()

        obs = np.concatenate(
            [
                imu_data["gyro"],
                imu_data["accelero"],
                cmds,
                dof_pos - self.init_pos,
                dof_vel * 0.05,
                self.last_action,
                self.last_last_action,
                self.last_last_last_action,
                self.motor_targets,
                feet_contacts,
                self.imitation_phase,
                ]
        )

        return obs

    def start(self):
        kps = [self.pid[0]] * 14
        kds = [self.pid[2]] * 14

        # lower head kps
        kps[5:9] = [8, 8, 8, 8]

        self.hwi.set_kps(kps)
        self.hwi.set_kds(kds)
        self.hwi.turn_on()

        time.sleep(2)

    def check_motor_voltage(self, log_power=False):
        voltages = self.hwi.get_present_voltages()
        if voltages is None or len(voltages) == 0:
            return True

        min_voltage = float(np.min(voltages))
        if log_power:
            currents = self.hwi.get_present_currents()
            if currents is not None and len(currents) > 0:
                abs_currents = np.abs(currents)
                max_current_i = int(np.argmax(abs_currents))
                print(
                    f"[POWER] min_voltage={min_voltage:.2f}V "
                    f"sum_abs_current={float(np.sum(abs_currents)):.2f}A "
                    f"max_abs_current={float(abs_currents[max_current_i]):.2f}A "
                    f"({self.hwi.joint_names[max_current_i]})"
                )

        if min_voltage < self.min_motor_voltage:
            print(
                f"[FATAL] Motor bus voltage too low: {min_voltage:.2f}V "
                f"(threshold {self.min_motor_voltage:.2f}V). Turning off torque."
            )
            self.hwi.turn_off()
            return False

        if min_voltage < self.min_motor_voltage + 0.3:
            print(
                f"[WARN] Motor bus voltage is low: {min_voltage:.2f}V "
                f"(threshold {self.min_motor_voltage:.2f}V)"
            )
        return True

    def get_phase_frequency_factor(self, x_velocity):

        max_phase_frequency = 1.2
        min_phase_frequency = 1.0

        # Perform linear interpolation
        freq = min_phase_frequency + (abs(x_velocity) / 0.15) * (
                max_phase_frequency - min_phase_frequency
        )

        return freq

    def run(self):
        i = 0
        try:
            # print("Starting")
            start_t = time.time()
            while True:
                left_trigger = 0
                right_trigger = 0
                t = time.time()

                if self.commands:
                    self.last_commands, self.buttons, left_trigger, right_trigger = (
                        self.xbox_controller.get_last_command()
                    )
                    # if i % 20 == 0:
                    #     print(f"[CMD] lin_x={self.last_commands[0]:.3f}, lin_y={self.last_commands[1]:.3f}, yaw={self.last_commands[2]:.3f}")

                    # print(f"[Debug] Joystick raw command: {self.last_commands}")
                    # print(f"[Debug] Trigger values: Left {left_trigger}, Right {right_trigger}")
                    # print(f"[Debug] Buttons: A={self.buttons.A.is_pressed}, Dpad_Left={self.buttons.dpad_left.is_pressed}, Dpad_Right={self.buttons.dpad_right.is_pressed}, ...")
                    # print(f"[RAW AXIS] axis0={self.xbox_controller.p1.get_axis(0):.3f}, axis1={self.xbox_controller.p1.get_axis(1):.3f}, axis2={self.xbox_controller.p1.get_axis(2):.3f}, axis3={self.xbox_controller.p1.get_axis(3):.3f}")
                    # print(f"[CMD DEBUG] Joystick Angular Cmd (Yaw): {self.last_commands[2]:.4f}")


                    if self.buttons.dpad_up.triggered:
                        self.phase_frequency_factor_offset += 0.05
                        print(
                            f"Phase frequency factor offset {round(self.phase_frequency_factor_offset, 3)}"
                        )

                    if self.buttons.dpad_down.triggered:
                        self.phase_frequency_factor_offset -= 0.05
                        print(
                            f"Phase frequency factor offset {round(self.phase_frequency_factor_offset, 3)}"
                        )

                    if self.buttons.LB.is_pressed:
                        self.phase_frequency_factor = 1.3
                    else:
                        self.phase_frequency_factor = 1.0

                    if self.buttons.X.triggered:
                        if self.duck_config.projector:
                            self.projector.switch()

                    if self.buttons.B.triggered:
                        if self.duck_config.speaker:
                            self.sounds.play_random_sound()

                    if self.duck_config.antennas:
                        self.antennas.set_position_left(right_trigger)
                        self.antennas.set_position_right(left_trigger)

                    if self.buttons.A.triggered:
                        self.paused = not self.paused
                        if self.paused:
                            print("PAUSE")
                        else:
                            print("UNPAUSE")

                if i % self.voltage_check_interval == 0:
                    log_power = (
                        self.power_log_interval_steps > 0
                        and i % self.power_log_interval_steps == 0
                    )
                    if not self.check_motor_voltage(log_power=log_power):
                        break

                if self.paused:
                    time.sleep(0.1)
                    continue

                obs = self.get_obs()
                if obs is None:
                    continue

                if i % 50 == 0:
                    gyro = obs[0:3]
                    accel = obs[3:6]
                    # print(f"[IMU] Gyro Z: {gyro[2]:.3f}, Accel X: {accel[0]:.3f}, Accel Y: {accel[1]:.3f}")

                    left_foot = obs[-4]  # 默认是feet_contacts[0]
                    right_foot = obs[-3] # 默认是feet_contacts[1]
                    # print(f"[Contact] Left: {left_foot:.1f}, Right: {right_foot:.1f}")

                self.imitation_i += 1 * (
                        self.phase_frequency_factor + self.phase_frequency_factor_offset
                )
                self.imitation_i = self.imitation_i % self.PRM.nb_steps_in_period
                self.imitation_phase = np.array(
                    [
                        np.cos(
                            self.imitation_i / self.PRM.nb_steps_in_period * 2 * np.pi
                        ),
                        np.sin(
                            self.imitation_i / self.PRM.nb_steps_in_period * 2 * np.pi
                        ),
                    ]
                )

                if self.save_obs:
                    self.saved_obs.append(obs)

                if self.replay_obs is not None:
                    if i < len(self.replay_obs):
                        obs = self.replay_obs[i]
                    else:
                        print("BREAKING ")
                        break

                # print(f"[OBS] yaw_cmd in obs[8]: {obs[8]:.3f}, should match yaw: {self.last_commands[2]:.3f}")

                action = self.policy.infer(obs)
                # if i % 20 == 0:
                # print(f"[DEBUG][Step {i}] last_commands: {np.round(self.last_commands, 3)}")
                # print(f"[DEBUG][Step {i}] Input Obs (yaw cmd): {obs[8]:.3f}")

                # if i % 20 == 0:
                #     print(f"[Action] {np.round(action, 3)}")

                # 原地动作时加“策略抑制”
                # if np.linalg.norm(self.last_commands[:2]) < 0.05:
                #     action[:] = 0.0

                # === 限幅死区抖动抑制 ===
                # if np.abs(action).max() < 0.1:
                #     action[:] = 0.0

                # if i % 50 == 0:
                #     print(f"[Action] Max: {np.max(action):.3f}, Min: {np.min(action):.3f}, Mean: {np.mean(action):.3f}")
                #     print(f"[Action raw] {np.round(action, 3)}")  # 新增行

                # if i % 50 == 0:
                    # print(f"[Action] Max: {np.max(action):.3f}, Min: {np.min(action):.3f}, Mean: {np.mean(action):.3f}")

                self.last_last_last_action = self.last_last_action.copy()
                self.last_last_action = self.last_action.copy()
                self.last_action = action.copy()

                # action = np.zeros(10)

                self.motor_targets = self.init_pos + action * self.action_scale
                if i % 50 == 0:
                    diff = self.motor_targets - self.prev_motor_targets
                    # print(f"[MotorTargets] ΔMax: {np.max(diff):.3f}, ΔMean: {np.mean(diff):.3f}")

                # self.motor_targets = np.clip(
                #     self.motor_targets,
                #     self.prev_motor_targets
                #     - self.max_motor_velocity * (1 / self.control_freq),  # control dt
                #     self.prev_motor_targets
                #     + self.max_motor_velocity * (1 / self.control_freq),  # control dt
                # )

                if self.action_filter is not None:
                    self.action_filter.push(self.motor_targets)
                    filtered_motor_targets = self.action_filter.get_filtered_action()
                    if (
                        time.time() - start_t > 1
                    ):  # give time to the filter to stabilize
                        self.motor_targets = filtered_motor_targets

                self.prev_motor_targets = self.motor_targets.copy()

                head_motor_targets = self.last_commands[3:] + self.motor_targets[5:9]
                self.motor_targets[5:9] = head_motor_targets
                # print(self.hwi.joints.keys())

                action_dict = make_action_dict(
                    self.motor_targets, list(self.hwi.joints.keys())
                )

                self.hwi.set_position_all(action_dict)

                i += 1

                took = time.time() - t
                # print("Full loop took", took, "fps : ", np.around(1 / took, 2))
                if (1 / self.control_freq - took) < 0:
                    print(
                        "Policy control budget exceeded by",
                        np.around(took - 1 / self.control_freq, 3),
                    )
                time.sleep(max(0, 1 / self.control_freq - took))

        except KeyboardInterrupt:
            if self.duck_config.antennas:
                self.antennas.stop()
        finally:
            self.hwi.turn_off()

        if self.save_obs:
            pickle.dump(self.saved_obs, open("robot_saved_obs.pkl", "wb"))
        print("TURNING OFF")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--onnx_model_path", type=str, required=True)
    parser.add_argument(
        "--duck_config_path",
        type=str,
        required=False,
        default=f"{HOME_DIR}/duck_config.json",
    )
    parser.add_argument("-a", "--action_scale", type=float, default=0.2)
    parser.add_argument("-p", type=int, default=22)
    parser.add_argument("-i", type=int, default=0)
    parser.add_argument("-d", type=int, default=0)
    parser.add_argument("-c", "--control_freq", type=int, default=50)
    parser.add_argument("--pitch_bias", type=float, default=0, help="deg")
    parser.add_argument(
        "--commands",
        action="store_true",
        default=True,
        help="external commands, keyboard or gamepad. Launch control_server.py on host computer",
    )
    parser.add_argument(
        "--no-commands",
        action="store_false",
        dest="commands",
        help="disable gamepad commands, useful for headless startup checks",
    )
    parser.add_argument(
        "--save_obs",
        type=str,
        required=False,
        default=False,
        help="save the run's observations",
    )
    parser.add_argument(
        "--replay_obs",
        type=str,
        required=False,
        default=None,
        help="replay the observations from a previous run (can be from the robot or from mujoco)",
    )
    parser.add_argument("--cutoff_frequency", type=float, default=None)
    parser.add_argument("--min_motor_voltage", type=float, default=6.8)
    parser.add_argument(
        "--power_log_interval",
        type=float,
        default=0.0,
        help="Seconds between voltage/current log lines. 0 disables power logs.",
    )

    args = parser.parse_args()
    pid = [args.p, args.i, args.d]

    print("Done parsing args")
    rl_walk = RLWalk(
        args.onnx_model_path,
        duck_config_path=args.duck_config_path,
        action_scale=args.action_scale,
        pid=pid,
        control_freq=args.control_freq,
        commands=args.commands,
        pitch_bias=args.pitch_bias,
        save_obs=args.save_obs,
        replay_obs=args.replay_obs,
        cutoff_frequency=args.cutoff_frequency,
        min_motor_voltage=args.min_motor_voltage,
        power_log_interval=args.power_log_interval,
    )
    print("Done instantiating RLWalk")
    rl_walk.run()
