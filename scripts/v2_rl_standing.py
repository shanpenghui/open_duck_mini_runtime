#!/usr/bin/env python3
"""
Run the standing policy on the physical duck robot (or in simulation).

Usage:
    # On robot (RPi):
    python scripts/v2_rl_standing.py

    # With custom config:
    python scripts/v2_rl_standing.py --duck_config_path ~/duck_config.json

    # Dry-run (no commands, headless):
    python scripts/v2_rl_standing.py --no-commands --control_freq 50
"""

import os
import sys

# Resolve repo root (scripts/ -> repo root)
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(_SCRIPT_DIR)
HOME_DIR = os.path.expanduser("~")

# Default ONNX model path (standing model trained on RTX 5090 D)
DEFAULT_ONNX = os.path.join(REPO_DIR, "BEST_STANDING_ONNX.onnx")


def main():
    import argparse
    from scripts.v2_rl_walk_mujoco import RLWalk

    parser = argparse.ArgumentParser(description="Run standing policy on the duck robot")
    parser.add_argument(
        "--onnx_model_path",
        type=str,
        default=DEFAULT_ONNX,
        help=f"Path to standing ONNX model (default: {DEFAULT_ONNX})",
    )
    parser.add_argument(
        "--duck_config_path",
        type=str,
        default=f"{HOME_DIR}/duck_config.json",
    )
    parser.add_argument("-a", "--action_scale", type=float, default=0.15)
    parser.add_argument("-p", type=int, default=22, help="PID P gain")
    parser.add_argument("-i", type=int, default=0, help="PID I gain")
    parser.add_argument("-d", type=int, default=0, help="PID D gain")
    parser.add_argument("-c", "--control_freq", type=int, default=50)
    parser.add_argument("--pitch_bias", type=float, default=0, help="deg")
    parser.add_argument(
        "--commands",
        action="store_true",
        default=False,
        help="enable external commands (xbox/keyboard)",
    )
    parser.add_argument(
        "--no-commands",
        action="store_false",
        dest="commands",
    )
    parser.add_argument(
        "--command_source",
        choices=("xbox", "keyboard"),
        default="xbox",
    )
    parser.add_argument(
        "--cutoff_frequency",
        type=float,
        default=None,
    )
    parser.add_argument(
        "--min_motor_voltage",
        type=float,
        default=6.8,
    )
    parser.add_argument(
        "--power_log_interval",
        type=float,
        default=0.0,
    )
    parser.add_argument("--save_obs", type=str, default=False)
    parser.add_argument("--replay_obs", type=str, default=None)

    args = parser.parse_args()
    pid = [args.p, args.i, args.d]

    print(f"[Standing] Loading model: {args.onnx_model_path}")
    print(f"[Standing] action_scale={args.action_scale}, control_freq={args.control_freq}")

    rl_walk = RLWalk(
        args.onnx_model_path,
        duck_config_path=args.duck_config_path,
        action_scale=args.action_scale,
        pid=pid,
        control_freq=args.control_freq,
        commands=args.commands,
        command_source=args.command_source,
        policy_mode="standing",
        pitch_bias=args.pitch_bias,
        save_obs=args.save_obs,
        replay_obs=args.replay_obs,
        cutoff_frequency=args.cutoff_frequency,
        min_motor_voltage=args.min_motor_voltage,
        power_log_interval=args.power_log_interval,
    )
    print("[Standing] Ready. Standing policy active.")
    rl_walk.run()


if __name__ == "__main__":
    main()
