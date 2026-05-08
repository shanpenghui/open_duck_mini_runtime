#!/usr/bin/env python3
"""Record fixed-command real-robot trials for the BDXR-inspired walking model.

The robot starts in a paused hold state. Press Xbox A to run one scripted
sequence. The script returns to the paused hold state after the sequence and
writes CSV/NPZ/JSON logs. Press A again to record another trial. Press B while
a trial is running to abort that trial safely.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
import sys
import time

import numpy as np

REPO_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_DIR))
sys.path.insert(0, str(REPO_DIR / "mini_bdx_runtime"))

HOME_DIR = os.path.expanduser("~")
make_action_dict = None
RLWalk = None
DEFAULT_BDXR_INSPIRED_ONNX = str(REPO_DIR / "WALK_BDXR_INSPIRED_272M.onnx")


def default_sequence() -> list[dict[str, float | str]]:
    return [
        {"name": "stand_pre", "duration": 2.0, "cmd_x": 0.0, "cmd_y": 0.0, "cmd_yaw": 0.0},
        {"name": "forward_005", "duration": 6.0, "cmd_x": 0.05, "cmd_y": 0.0, "cmd_yaw": 0.0},
        {"name": "stop_after_005", "duration": 2.0, "cmd_x": 0.0, "cmd_y": 0.0, "cmd_yaw": 0.0},
        {"name": "forward_010", "duration": 8.0, "cmd_x": 0.10, "cmd_y": 0.0, "cmd_yaw": 0.0},
        {"name": "stop_after_010", "duration": 2.0, "cmd_x": 0.0, "cmd_y": 0.0, "cmd_yaw": 0.0},
        {"name": "forward_015", "duration": 10.0, "cmd_x": 0.15, "cmd_y": 0.0, "cmd_yaw": 0.0},
        {"name": "stand_post", "duration": 3.0, "cmd_x": 0.0, "cmd_y": 0.0, "cmd_yaw": 0.0},
    ]


def load_sequence(path: str | None) -> list[dict[str, float | str]]:
    if not path:
        return default_sequence()
    with open(path, "r", encoding="utf-8") as f:
        sequence = json.load(f)
    for idx, phase in enumerate(sequence):
        for key in ("name", "duration", "cmd_x", "cmd_y", "cmd_yaw"):
            if key not in phase:
                raise ValueError(f"sequence[{idx}] missing {key}")
    return sequence


def sequence_duration(sequence: list[dict[str, float | str]]) -> float:
    return float(sum(float(phase["duration"]) for phase in sequence))


def phase_at(sequence: list[dict[str, float | str]], elapsed: float) -> tuple[int, dict[str, float | str], float]:
    cursor = 0.0
    for idx, phase in enumerate(sequence):
        duration = float(phase["duration"])
        if elapsed < cursor + duration:
            return idx, phase, elapsed - cursor
        cursor += duration
    return len(sequence) - 1, sequence[-1], float(sequence[-1]["duration"])


def now_stamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def set_zero_command(robot: RLWalk) -> None:
    robot.last_commands = [0.0] * 7


def hold_default_pose(robot: RLWalk) -> None:
    if make_action_dict is None:
        raise RuntimeError("runtime symbols not loaded")
    set_zero_command(robot)
    robot.motor_targets = np.asarray(robot.init_pos, dtype=float)
    robot.prev_motor_targets = robot.motor_targets.copy()
    action_dict = make_action_dict(robot.motor_targets, list(robot.hwi.joints.keys()))
    robot.hwi.set_position_all(action_dict)


def update_phase(robot: RLWalk) -> None:
    robot.imitation_i += 1.0 * (
        robot.phase_frequency_factor + robot.phase_frequency_factor_offset
    )
    robot.imitation_i = robot.imitation_i % robot.PRM.nb_steps_in_period
    phase = robot.imitation_i / robot.PRM.nb_steps_in_period * 2.0 * np.pi
    robot.imitation_phase = np.array([np.cos(phase), np.sin(phase)])


def read_power(robot: RLWalk) -> tuple[np.ndarray, np.ndarray]:
    voltages = robot.hwi.get_present_voltages()
    currents = robot.hwi.get_present_currents()
    if voltages is None:
        voltages = np.full(robot.num_dofs, np.nan)
    if currents is None:
        currents = np.full(robot.num_dofs, np.nan)
    return np.asarray(voltages, dtype=float), np.asarray(currents, dtype=float)


def infer_and_apply(robot: RLWalk, obs: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if make_action_dict is None:
        raise RuntimeError("runtime symbols not loaded")
    action = robot.policy.infer(obs)
    robot.last_last_last_action = robot.last_last_action.copy()
    robot.last_last_action = robot.last_action.copy()
    robot.last_action = action.copy()
    previous_targets = robot.motor_targets.copy()
    robot.motor_targets = np.asarray(robot.init_pos, dtype=float) + action * robot.action_scale
    robot.prev_motor_targets = robot.motor_targets.copy()

    head_motor_targets = np.asarray(robot.last_commands[3:], dtype=float) + robot.motor_targets[5:9]
    robot.motor_targets[5:9] = head_motor_targets
    action_dict = make_action_dict(robot.motor_targets, list(robot.hwi.joints.keys()))
    robot.hwi.set_position_all(action_dict)
    return action, robot.motor_targets.copy(), robot.motor_targets.copy() - previous_targets


def empty_arrays() -> dict[str, list]:
    return {
        "time_s": [],
        "loop_dt_s": [],
        "phase_idx": [],
        "phase_elapsed_s": [],
        "cmd": [],
        "obs": [],
        "action": [],
        "target": [],
        "target_delta": [],
        "joint_pos": [],
        "joint_vel": [],
        "contacts": [],
        "voltages": [],
        "currents": [],
        "obs_finite": [],
        "action_finite": [],
        "missed_deadline": [],
    }


def append_sample(
    arrays: dict[str, list],
    *,
    time_s: float,
    loop_dt_s: float,
    phase_idx: int,
    phase_elapsed_s: float,
    cmd: list[float],
    obs: np.ndarray,
    action: np.ndarray,
    target: np.ndarray,
    target_delta: np.ndarray,
    init_pos: np.ndarray,
    voltages: np.ndarray,
    currents: np.ndarray,
    missed_deadline: bool,
):
    joint_pos_error = obs[13 : 13 + target.size]
    joint_pos = np.asarray(init_pos, dtype=float) + joint_pos_error
    joint_vel = obs[27:41] / 0.05
    contacts = obs[97:99]
    arrays["time_s"].append(time_s)
    arrays["loop_dt_s"].append(loop_dt_s)
    arrays["phase_idx"].append(phase_idx)
    arrays["phase_elapsed_s"].append(phase_elapsed_s)
    arrays["cmd"].append(np.asarray(cmd, dtype=float))
    arrays["obs"].append(obs.copy())
    arrays["action"].append(action.copy())
    arrays["target"].append(target.copy())
    arrays["target_delta"].append(target_delta.copy())
    arrays["joint_pos"].append(joint_pos.copy())
    arrays["joint_vel"].append(joint_vel.copy())
    arrays["contacts"].append(contacts.copy())
    arrays["voltages"].append(voltages.copy())
    arrays["currents"].append(currents.copy())
    arrays["obs_finite"].append(bool(np.isfinite(obs).all()))
    arrays["action_finite"].append(bool(np.isfinite(action).all()))
    arrays["missed_deadline"].append(bool(missed_deadline))


def write_outputs(
    out_dir: Path,
    trial_name: str,
    arrays: dict[str, list],
    metadata: dict,
    sequence: list[dict[str, float | str]],
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    np_arrays = {key: np.asarray(value) for key, value in arrays.items()}
    npz_path = out_dir / f"{trial_name}.npz"
    csv_path = out_dir / f"{trial_name}.csv"
    json_path = out_dir / f"{trial_name}.json"

    np.savez_compressed(npz_path, **np_arrays)

    phase_names = [str(phase["name"]) for phase in sequence]
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        fieldnames = [
            "time_s",
            "phase",
            "phase_elapsed_s",
            "cmd_x",
            "cmd_y",
            "cmd_yaw",
            "gyro_x",
            "gyro_y",
            "gyro_z",
            "accel_x",
            "accel_y",
            "accel_z",
            "joint_vel_abs_max",
            "action_abs_max",
            "action_saturation_frac",
            "target_delta_abs_max",
            "left_contact",
            "right_contact",
            "min_voltage",
            "sum_abs_current",
            "obs_finite",
            "action_finite",
            "missed_deadline",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for i, time_s in enumerate(np_arrays["time_s"]):
            obs = np_arrays["obs"][i]
            action = np_arrays["action"][i]
            target_delta = np_arrays["target_delta"][i]
            voltages = np_arrays["voltages"][i]
            currents = np_arrays["currents"][i]
            cmd = np_arrays["cmd"][i]
            contacts = np_arrays["contacts"][i]
            writer.writerow(
                {
                    "time_s": float(time_s),
                    "phase": phase_names[int(np_arrays["phase_idx"][i])],
                    "phase_elapsed_s": float(np_arrays["phase_elapsed_s"][i]),
                    "cmd_x": float(cmd[0]),
                    "cmd_y": float(cmd[1]),
                    "cmd_yaw": float(cmd[2]),
                    "gyro_x": float(obs[0]),
                    "gyro_y": float(obs[1]),
                    "gyro_z": float(obs[2]),
                    "accel_x": float(obs[3]),
                    "accel_y": float(obs[4]),
                    "accel_z": float(obs[5]),
                    "joint_vel_abs_max": float(np.nanmax(np.abs(np_arrays["joint_vel"][i]))),
                    "action_abs_max": float(np.nanmax(np.abs(action))),
                    "action_saturation_frac": float(np.nanmean(np.abs(action) > 0.95)),
                    "target_delta_abs_max": float(np.nanmax(np.abs(target_delta))),
                    "left_contact": float(contacts[0]),
                    "right_contact": float(contacts[1]),
                    "min_voltage": float(np.nanmin(voltages)),
                    "sum_abs_current": float(np.nansum(np.abs(currents))),
                    "obs_finite": bool(np_arrays["obs_finite"][i]),
                    "action_finite": bool(np_arrays["action_finite"][i]),
                    "missed_deadline": bool(np_arrays["missed_deadline"][i]),
                }
            )

    summary = {
        **metadata,
        "sequence": sequence,
        "samples": int(len(np_arrays["time_s"])),
        "duration_s": float(np_arrays["time_s"][-1]) if len(np_arrays["time_s"]) else 0.0,
        "mean_loop_dt_s": float(np.mean(np_arrays["loop_dt_s"])) if len(np_arrays["loop_dt_s"]) else None,
        "missed_deadline_frac": float(np.mean(np_arrays["missed_deadline"])) if len(np_arrays["missed_deadline"]) else None,
        "max_action_abs": float(np.nanmax(np.abs(np_arrays["action"]))) if len(np_arrays["action"]) else None,
        "mean_action_saturation_frac": float(np.nanmean(np.abs(np_arrays["action"]) > 0.95)) if len(np_arrays["action"]) else None,
        "max_target_delta_abs": float(np.nanmax(np.abs(np_arrays["target_delta"]))) if len(np_arrays["target_delta"]) else None,
        "min_voltage": float(np.nanmin(np_arrays["voltages"])) if len(np_arrays["voltages"]) else None,
        "max_sum_abs_current": float(np.nanmax(np.nansum(np.abs(np_arrays["currents"]), axis=1))) if len(np_arrays["currents"]) else None,
        "files": {
            "csv": str(csv_path),
            "npz": str(npz_path),
            "json": str(json_path),
        },
    }
    json_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[record] wrote {csv_path}")
    print(f"[record] wrote {npz_path}")
    print(f"[record] wrote {json_path}")


def run_trial(
    robot: RLWalk,
    sequence: list[dict[str, float | str]],
    out_dir: Path,
    trial_idx: int,
    args: argparse.Namespace,
) -> None:
    arrays = empty_arrays()
    total_duration = sequence_duration(sequence)
    period = 1.0 / robot.control_freq
    power_period_steps = max(1, int(robot.control_freq / args.power_sample_hz))
    voltages = np.full(robot.num_dofs, np.nan)
    currents = np.full(robot.num_dofs, np.nan)
    start_wall = time.time()
    last_loop_t = start_wall
    step = 0
    aborted = False

    print(f"[record] trial {trial_idx:03d} started, duration={total_duration:.1f}s")
    while True:
        loop_t = time.time()
        elapsed = loop_t - start_wall
        if elapsed >= total_duration:
            break

        _, buttons, _, _ = robot.command_controller.get_last_command()
        if buttons.B.triggered:
            aborted = True
            print("[record] B pressed, aborting trial")
            break

        phase_idx, phase, phase_elapsed = phase_at(sequence, elapsed)
        robot.last_commands = [
            float(phase["cmd_x"]),
            float(phase["cmd_y"]),
            float(phase["cmd_yaw"]),
            0.0,
            0.0,
            0.0,
            0.0,
        ]

        if step % robot.voltage_check_interval == 0 and not robot.check_motor_voltage(log_power=False):
            aborted = True
            break

        obs = robot.get_obs()
        if obs is None:
            continue
        update_phase(robot)
        action, target, target_delta = infer_and_apply(robot, obs)
        if step % power_period_steps == 0:
            voltages, currents = read_power(robot)

        loop_dt = loop_t - last_loop_t
        last_loop_t = loop_t
        took = time.time() - loop_t
        missed_deadline = took > period
        append_sample(
            arrays,
            time_s=elapsed,
            loop_dt_s=loop_dt,
            phase_idx=phase_idx,
            phase_elapsed_s=phase_elapsed,
            cmd=robot.last_commands,
            obs=obs,
            action=action,
            target=target,
            target_delta=target_delta,
            init_pos=np.asarray(robot.init_pos, dtype=float),
            voltages=voltages,
            currents=currents,
            missed_deadline=missed_deadline,
        )

        if step % max(1, int(robot.control_freq)) == 0:
            print(
                f"[record] t={elapsed:5.2f}s phase={phase['name']} "
                f"cmd=({robot.last_commands[0]:+.2f},{robot.last_commands[1]:+.2f},{robot.last_commands[2]:+.2f}) "
                f"action_abs={float(np.max(np.abs(action))):.3f}"
            )

        step += 1
        time.sleep(max(0.0, period - took))

    hold_default_pose(robot)
    metadata = {
        "trial_idx": trial_idx,
        "aborted": aborted,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(start_wall)),
        "onnx_model_path": args.onnx_model_path,
        "model_tag": args.model_tag,
        "action_scale": robot.action_scale,
        "control_freq": robot.control_freq,
        "pid": robot.pid,
        "pitch_bias": robot.pitch_bias,
        "init_pos": list(map(float, robot.init_pos)),
        "joint_names": list(robot.hwi.joints.keys()),
        "notes": args.notes,
    }
    write_outputs(out_dir, f"trial_{trial_idx:03d}", arrays, metadata, sequence)
    print(f"[record] trial {trial_idx:03d} finished; robot returned to hold")


def wait_for_a(robot: RLWalk, prompt: str) -> None:
    print(prompt)
    hold_last = 0.0
    while True:
        _, buttons, _, _ = robot.command_controller.get_last_command()
        if buttons.A.triggered:
            return
        if time.time() - hold_last > 0.5:
            hold_default_pose(robot)
            hold_last = time.time()
        time.sleep(0.02)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--onnx_model_path", default=DEFAULT_BDXR_INSPIRED_ONNX)
    parser.add_argument("--model_tag", default="bdxr_inspired_272m")
    parser.add_argument("--duck_config_path", default=f"{HOME_DIR}/duck_config.json")
    parser.add_argument("--serial_port", default="/dev/ttyACM0")
    parser.add_argument("-a", "--action_scale", type=float, default=0.2)
    parser.add_argument("-p", type=int, default=22)
    parser.add_argument("-i", type=int, default=0)
    parser.add_argument("-d", type=int, default=0)
    parser.add_argument("-c", "--control_freq", type=int, default=50)
    parser.add_argument("--pitch_bias", type=float, default=0.0)
    parser.add_argument("--cutoff_frequency", type=float, default=None)
    parser.add_argument("--min_motor_voltage", type=float, default=6.8)
    parser.add_argument("--power_sample_hz", type=float, default=5.0)
    parser.add_argument("--sequence_json", default=None)
    parser.add_argument("--output_dir", default=str(REPO_DIR / "robot_logs" / "bdxr_inspired_272m"))
    parser.add_argument("--trials", type=int, default=0, help="0 means keep waiting for A after each trial")
    parser.add_argument("--notes", default="")
    args = parser.parse_args()

    global make_action_dict, RLWalk
    from mini_bdx_runtime.rl_utils import make_action_dict as _make_action_dict
    from scripts.v2_rl_walk_mujoco import RLWalk as _RLWalk

    make_action_dict = _make_action_dict
    RLWalk = _RLWalk

    sequence = load_sequence(args.sequence_json)
    out_dir = Path(args.output_dir).expanduser() / now_stamp()
    pid = [args.p, args.i, args.d]

    if not Path(args.onnx_model_path).exists():
        raise FileNotFoundError(args.onnx_model_path)

    print("[record] initializing robot")
    robot = RLWalk(
        args.onnx_model_path,
        duck_config_path=args.duck_config_path,
        serial_port=args.serial_port,
        action_scale=args.action_scale,
        pid=pid,
        control_freq=args.control_freq,
        commands=True,
        command_source="xbox",
        pitch_bias=args.pitch_bias,
        save_obs=False,
        replay_obs=None,
        cutoff_frequency=args.cutoff_frequency,
        min_motor_voltage=args.min_motor_voltage,
        power_log_interval=0.0,
    )

    trial_idx = 1
    try:
        hold_default_pose(robot)
        print("[record] robot is in paused hold state")
        while args.trials == 0 or trial_idx <= args.trials:
            wait_for_a(robot, f"[record] press Xbox A to start trial {trial_idx:03d}; Ctrl+C exits")
            run_trial(robot, sequence, out_dir, trial_idx, args)
            trial_idx += 1
    except KeyboardInterrupt:
        print("\n[record] interrupted")
    finally:
        hold_default_pose(robot)
        if robot.command_controller is not None and hasattr(robot.command_controller, "close"):
            robot.command_controller.close()
        robot.hwi.turn_off()
        print("[record] torque off")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
