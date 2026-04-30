#!/usr/bin/env python3
"""
Diagnostic data recorder for sim2real gap analysis.

Records a time-synchronized data stream from a walking policy running on the
real robot, capturing everything needed to compare against simulation:
  - Joint positions (commanded vs actual)
  - Joint velocities
  - Joint currents (torque proxy)
  - IMU data (gyro, accel)
  - Foot contacts
  - Policy observations (obs vector sent to ONNX)
  - Policy actions (raw output from ONNX)
  - Motor targets (after action_scale application)
  - Loop timing

Usage:
  python scripts/diagnostic_record.py \
    --onnx_model_path BEST_WALK_ONNX_2.onnx \
    --duration 15 \
    --command_vel_x 0.15 \
    --action_scale 0.25 \
    --output diagnostic_data.pkl

This will command a constant forward velocity and record everything for
`duration` seconds. The robot must be standing and ready when you start.

Output is a pickle dict with numpy arrays, easily loadable for analysis:
  data = pickle.load(open("diagnostic_data.pkl", "rb"))
  plt.plot(data["times"], data["joint_pos_actual"][:, 0])  # left_hip_yaw
"""

import argparse
import os
import pickle
import sys
import time

import numpy as np

# Ensure the repo root is on sys.path so local imports work
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_DIR = os.path.dirname(_SCRIPT_DIR)
sys.path.insert(0, os.path.join(_REPO_DIR, "mini_bdx_runtime"))

from mini_bdx_runtime.rustypot_position_hwi import HWI
from mini_bdx_runtime.onnx_infer import OnnxInfer
from mini_bdx_runtime.raw_imu import Imu
from mini_bdx_runtime.feet_contacts import FeetContacts
from mini_bdx_runtime.rl_utils import make_action_dict
from mini_bdx_runtime.duck_config import DuckConfig
from mini_bdx_runtime.poly_reference_motion import PolyReferenceMotion


def main():
    parser = argparse.ArgumentParser(
        description="Record diagnostic data from a walking duck for sim2real analysis"
    )
    parser.add_argument("--onnx_model_path", type=str, required=True)
    parser.add_argument("--duck_config_path", type=str, default=None)
    parser.add_argument("--serial_port", type=str, default="/dev/ttyACM0")
    parser.add_argument("--control_freq", type=float, default=50)
    parser.add_argument("--action_scale", type=float, default=0.25)
    parser.add_argument("--pid", type=int, nargs=3, default=[30, 0, 0])
    parser.add_argument("--duration", type=float, default=15.0,
                        help="Recording duration in seconds")
    parser.add_argument("--command_vel_x", type=float, default=0.15,
                        help="Constant forward velocity command (m/s)")
    parser.add_argument("--command_vel_y", type=float, default=0.0)
    parser.add_argument("--command_yaw", type=float, default=0.0)
    parser.add_argument("--output", type=str, default=None,
                        help="Output pkl path (auto-generated if omitted)")
    parser.add_argument("--pitch_bias", type=float, default=0)
    args = parser.parse_args()

    if args.output is None:
        ts = time.strftime("%Y%m%d_%H%M%S")
        args.output = os.path.join(
            _REPO_DIR, f"diagnostic_{ts}_{args.command_vel_x}vx.pkl"
        )

    # ── Init hardware ────────────────────────────────────────────────
    print("[INIT] Loading config...")
    home_dir = os.path.expanduser("~")
    config_path = args.duck_config_path or f"{home_dir}/duck_config.json"
    duck_config = DuckConfig(config_json_path=config_path)

    print("[INIT] Loading ONNX model...")
    policy = OnnxInfer(args.onnx_model_path, awd=True)

    print("[INIT] Connecting to servos...")
    hwi = HWI(duck_config, args.serial_port)

    num_dofs = 14  # 10 leg joints + 4 head joints

    # Set PID and turn on
    kps = [args.pid[0]] * num_dofs
    kps[5:9] = [8, 8, 8, 8]  # lower head kps
    kds = [args.pid[2]] * num_dofs
    hwi.set_kps(kps)
    hwi.set_kds(kds)
    hwi.turn_on()
    time.sleep(2)

    init_pos = list(hwi.init_pos.values())

    print("[INIT] Starting IMU...")
    imu = Imu(
        sampling_freq=int(args.control_freq),
        user_pitch_bias=args.pitch_bias,
        upside_down=duck_config.imu_upside_down,
    )
    time.sleep(1)

    print("[INIT] Starting foot contacts...")
    feet_contacts = FeetContacts()

    print("[INIT] Loading reference motion...")
    prm_path = os.path.join(_REPO_DIR, "scripts", "polynomial_coefficients.pkl")
    prm = PolyReferenceMotion(prm_path)

    # ── State ─────────────────────────────────────────────────────────
    last_action = np.zeros(num_dofs)
    last_last_action = np.zeros(num_dofs)
    last_last_last_action = np.zeros(num_dofs)
    motor_targets = np.array(init_pos.copy())
    imitation_i = 0.0

    # ── Recording buffers ────────────────────────────────────────────
    ctrl_dt = 1.0 / args.control_freq
    max_steps = int(args.duration * args.control_freq) + 100  # safety margin

    rec = {
        "times": [],
        # Joint data: actual sensor readings
        "joint_pos_actual": [],       # (N, 14) rad - what servo reports
        "joint_vel_actual": [],       # (N, 14) rad/s
        "joint_current_actual": [],   # (N, 14) A - torque proxy
        "joint_voltage_actual": [],   # (N, 14) V - supply voltage
        # Command chain
        "obs": [],                    # (N, 101) - full obs vector
        "action_raw": [],             # (N, 14) - raw policy output (normalized)
        "motor_targets": [],          # (N, 14) - init_pos + action * action_scale
        # IMU
        "imu_gyro": [],              # (N, 3) rad/s
        "imu_accel": [],             # (N, 3) m/s²
        # Foot contacts
        "feet_contacts": [],         # (N, 2) bool
        # Timing
        "loop_time_ms": [],          # (N,) ms per loop iteration
        "obs_build_time_ms": [],     # (N,) ms to build obs
        "infer_time_ms": [],         # (N,) ms for ONNX inference
        "write_time_ms": [],         # (N,) ms to write positions to servos
        # Phase
        "imitation_phase": [],       # (N, 2)
    }

    # Metadata (stored once)
    metadata = {
        "onnx_model": os.path.basename(args.onnx_model_path),
        "action_scale": args.action_scale,
        "control_freq": args.control_freq,
        "pid": args.pid,
        "command_vel_x": args.command_vel_x,
        "command_vel_y": args.command_vel_y,
        "command_yaw": args.command_yaw,
        "duration_planned": args.duration,
        "init_pos": init_pos,
        "joint_names": list(hwi.joints.keys()),
        "num_dofs": num_dofs,
        "duck_weight_kg": "2.2",  # user-provided
    }

    # Constant command
    cmd = [
        args.command_vel_x,  # lin_vel_x
        args.command_vel_y,  # lin_vel_y
        args.command_yaw,    # ang_vel_yaw
        0.0,                 # neck_pitch
        0.0,                 # head_pitch
        0.0,                 # head_yaw
        0.0,                 # head_roll
    ]

    print(f"\n{'='*60}")
    print(f"[REC] Recording {args.duration}s at {args.control_freq}Hz")
    print(f"[REC] Command: vx={args.command_vel_x}, vy={args.command_vel_y}, yaw={args.command_yaw}")
    print(f"[REC] action_scale={args.action_scale}, pid={args.pid}")
    print(f"[REC] Output: {args.output}")
    print(f"{'='*60}")
    print("[REC] Starting in 3 seconds... Robot should be standing!")
    time.sleep(3)

    # ── Main loop ─────────────────────────────────────────────────────
    start_t = time.time()
    i = 0

    try:
        while True:
            loop_start = time.time()
            t = time.time() - start_t

            if t >= args.duration:
                print(f"\n[REC] Duration reached ({t:.2f}s)")
                break

            # ── Build obs (same as v2_rl_walk_mujoco.py) ──────────
            obs_t0 = time.time()

            imu_data = imu.get_data()
            gyro = imu_data["gyro"]
            accel = imu_data["accelero"]

            # Same filtering as v2_rl_walk_mujoco
            accel = np.clip(accel, -10, 10)
            gyro = np.clip(gyro, -5, 5)

            dof_pos = hwi.get_present_positions(
                ignore=["left_antenna", "right_antenna"]
            )
            dof_vel = hwi.get_present_velocities(
                ignore=["left_antenna", "right_antenna"]
            )

            if dof_pos is None or dof_vel is None:
                print("[WARN] Servo read failed, skipping step")
                time.sleep(ctrl_dt)
                continue

            feet = feet_contacts.get()

            # Imitation phase
            imitation_i += 1
            imitation_i = imitation_i % prm.nb_steps_in_period
            imitation_phase = np.array([
                np.cos(imitation_i / prm.nb_steps_in_period * 2 * np.pi),
                np.sin(imitation_i / prm.nb_steps_in_period * 2 * np.pi),
            ])

            obs = np.concatenate([
                gyro,                             # 3
                accel,                            # 3
                cmd,                              # 7
                dof_pos - np.array(init_pos),     # 14
                dof_vel * 0.05,                   # 14
                last_action,                      # 14
                last_last_action,                 # 14
                last_last_last_action,            # 14
                motor_targets,                    # 14
                feet,                             # 2
                imitation_phase,                  # 2
            ])  # total = 101

            obs_build_ms = (time.time() - obs_t0) * 1000

            # ── Inference ──────────────────────────────────────────
            infer_t0 = time.time()
            action = policy.infer(obs)
            infer_ms = (time.time() - infer_t0) * 1000

            # ── Apply action ───────────────────────────────────────
            last_last_last_action = last_last_action.copy()
            last_last_action = last_action.copy()
            last_action = action.copy()

            motor_targets = np.array(init_pos) + action * args.action_scale

            # Add head commands
            head_motor_targets = cmd[3:] + motor_targets[5:9]
            motor_targets[5:9] = head_motor_targets

            # ── Write to servos ────────────────────────────────────
            write_t0 = time.time()
            action_dict = make_action_dict(
                motor_targets.tolist(), list(hwi.joints.keys())
            )
            hwi.set_position_all(action_dict)
            write_ms = (time.time() - write_t0) * 1000

            # ── Read extra telemetry (currents + voltages) ─────────
            currents = hwi.get_present_currents()
            voltages = hwi.get_present_voltages()

            # ── Record ─────────────────────────────────────────────
            loop_ms = (time.time() - loop_start) * 1000

            rec["times"].append(t)
            rec["joint_pos_actual"].append(dof_pos.copy())
            rec["joint_vel_actual"].append(dof_vel.copy())
            rec["joint_current_actual"].append(
                currents.copy() if currents is not None else np.full(num_dofs, np.nan)
            )
            rec["joint_voltage_actual"].append(
                voltages.copy() if voltages is not None else np.full(num_dofs, np.nan)
            )
            rec["obs"].append(obs.copy())
            rec["action_raw"].append(action.copy())
            rec["motor_targets"].append(motor_targets.copy())
            rec["imu_gyro"].append(gyro.copy())
            rec["imu_accel"].append(accel.copy())
            rec["feet_contacts"].append(feet.copy())
            rec["loop_time_ms"].append(loop_ms)
            rec["obs_build_time_ms"].append(obs_build_ms)
            rec["infer_time_ms"].append(infer_ms)
            rec["write_time_ms"].append(write_ms)
            rec["imitation_phase"].append(imitation_phase.copy())

            # Progress
            if i % 50 == 0:
                pos_err = np.max(np.abs(dof_pos - motor_targets))
                max_cur = np.max(np.abs(currents)) if currents is not None else 0
                print(
                    f"[{t:6.2f}s] loop={loop_ms:5.1f}ms "
                    f"pos_track_err={np.degrees(pos_err):5.1f}° "
                    f"max_cur={max_cur:5.2f}A "
                    f"max_action={np.max(np.abs(action)):.3f}"
                )

            i += 1

            # Sleep to maintain control freq
            elapsed = time.time() - loop_start
            time.sleep(max(0, ctrl_dt - elapsed))

    except KeyboardInterrupt:
        print("\n[REC] Interrupted by user")
    finally:
        hwi.turn_off()

    # ── Save ──────────────────────────────────────────────────────────
    print(f"\n[SAVE] Converting to numpy arrays...")

    for key in rec:
        if len(rec[key]) > 0:
            rec[key] = np.array(rec[key])
        else:
            rec[key] = np.array([])

    rec["metadata"] = metadata
    rec["joint_names_ordered"] = [
        "left_hip_yaw", "left_hip_roll", "left_hip_pitch",
        "left_knee", "left_ankle",
        "neck_pitch", "head_pitch", "head_yaw", "head_roll",
        "right_hip_yaw", "right_hip_roll", "right_hip_pitch",
        "right_knee", "right_ankle",
    ]

    # Also store the obs layout for easy indexing
    rec["obs_layout"] = {
        "gyro": (0, 3),
        "accel": (3, 6),
        "command": (6, 13),
        "dof_pos_delta": (13, 27),
        "dof_vel_scaled": (27, 41),
        "last_action": (41, 55),
        "last_last_action": (55, 69),
        "last_last_last_action": (69, 83),
        "motor_targets": (83, 97),
        "feet_contacts": (97, 99),
        "imitation_phase": (99, 101),
    }

    with open(args.output, "wb") as f:
        pickle.dump(rec, f)

    actual_duration = rec["times"][-1] if len(rec["times"]) > 0 else 0
    print(f"[SAVE] Wrote {args.output}")
    print(f"[SAVE] {len(rec['times'])} frames, {actual_duration:.2f}s")
    print(f"[SAVE] File size: {os.path.getsize(args.output) / 1024:.1f} KB")
    print("[SAVE] Done!")

    # Quick summary stats
    if len(rec["loop_time_ms"]) > 0:
        print(f"\n── Timing Summary ──")
        print(f"  Loop:  mean={np.mean(rec['loop_time_ms']):.1f}ms  "
              f"max={np.max(rec['loop_time_ms']):.1f}ms  "
              f"(budget={ctrl_dt*1000:.1f}ms)")
        print(f"  Obs:   mean={np.mean(rec['obs_build_time_ms']):.1f}ms")
        print(f"  Infer: mean={np.mean(rec['infer_time_ms']):.1f}ms")
        print(f"  Write: mean={np.mean(rec['write_time_ms']):.1f}ms")

    if len(rec["joint_pos_actual"]) > 0 and len(rec["motor_targets"]) > 0:
        tracking_err = rec["joint_pos_actual"] - rec["motor_targets"]
        print(f"\n── Joint Tracking Summary ──")
        print(f"  Mean tracking error: {np.degrees(np.mean(np.abs(tracking_err))):.2f}°")
        print(f"  Max tracking error:  {np.degrees(np.max(np.abs(tracking_err))):.2f}°")

        # Per-joint breakdown (leg joints only, skip head)
        leg_indices = [0,1,2,3,4, 9,10,11,12,13]
        jnames = rec["joint_names_ordered"]
        print(f"  Per-joint max error (leg joints):")
        for idx in leg_indices:
            err = np.degrees(np.max(np.abs(tracking_err[:, idx])))
            print(f"    {jnames[idx]:20s}: {err:.2f}°")

    if len(rec["joint_current_actual"]) > 0:
        currents = rec["joint_current_actual"]
        print(f"\n── Current Summary ──")
        print(f"  Mean |current|: {np.mean(np.abs(currents)):.3f} A")
        print(f"  Max  |current|: {np.max(np.abs(currents)):.3f} A")
        # Peak current per joint
        print(f"  Peak |current| per joint:")
        for idx in leg_indices:
            peak = np.max(np.abs(currents[:, idx]))
            print(f"    {jnames[idx]:20s}: {peak:.3f} A")


if __name__ == "__main__":
    main()
