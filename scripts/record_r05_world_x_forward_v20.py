#!/usr/bin/env python3
"""
Run a real-robot diagnostic capture for the R05 world-x forward policy.

This script runs from the Windows/local runtime checkout. It syncs the runtime
tree to the duck host, starts scripts/diagnostic_record.py on the robot with
gamepad commands enabled by default, stores the robot-side output under
logs/r05_world_x_forward_v20/<timestamp>/, then downloads the .pkl and .log
files to the same local path.
"""

import argparse
import getpass
import posixpath
import shlex
import time
from pathlib import Path

MODEL_NAME = "r05_world_x_forward_v20.onnx"
RUN_NAME = "r05_world_x_forward_v20"
DEFAULT_HOST = "192.168.0.34"
DEFAULT_USER = "duck"
DEFAULT_REMOTE_DIR = "/home/duck/open_duck_mini_runtime"


def q(value):
    return shlex.quote(str(value))


def build_parser():
    parser = argparse.ArgumentParser(
        description="Collect real-robot diagnostic data for r05_world_x_forward_v20.onnx."
    )
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--user", default=DEFAULT_USER)
    parser.add_argument("--password", default=None)
    parser.add_argument("--remote-dir", default=DEFAULT_REMOTE_DIR)
    parser.add_argument("--model", default=MODEL_NAME)
    parser.add_argument("--config", default="duck_config.json")
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--command-vel-x", type=float, default=0.05)
    parser.add_argument("--command-vel-y", type=float, default=0.0)
    parser.add_argument("--command-yaw", type=float, default=0.0)
    parser.add_argument("--commands", dest="commands", action="store_true", default=True)
    parser.add_argument("--no-commands", dest="commands", action="store_false")
    parser.add_argument("--command-source", choices=("xbox", "keyboard"), default="xbox")
    parser.add_argument("--action-scale", type=float, default=0.18)
    parser.add_argument("--control-freq", type=float, default=50.0)
    parser.add_argument("--kp", type=int, default=22)
    parser.add_argument("--ki", type=int, default=0)
    parser.add_argument("--kd", type=int, default=0)
    parser.add_argument("--pitch-bias", type=float, default=0.0)
    parser.add_argument("--serial-port", default="/dev/ttyACM0")
    parser.add_argument(
        "--no-sync",
        action="store_true",
        help="Skip uploading the runtime checkout before recording.",
    )
    return parser


def main():
    args = build_parser().parse_args()
    try:
        from duck_remote import DuckRemote
    except ImportError as exc:
        raise SystemExit(
            "Missing dependency for remote SSH sync: paramiko.\n"
            "Install it in the Python environment used to run this script:\n"
            "  python -m pip install paramiko"
        ) from exc

    local_dir = Path(__file__).resolve().parents[1]
    local_model = local_dir / args.model
    if not local_model.exists():
        raise SystemExit(
            f"Missing local model: {local_model}\n"
            "Copy the R05 ONNX into the runtime root or pass --model."
        )

    password = args.password or getpass.getpass(f"Password for {args.user}@{args.host}: ")
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    run_id = (
        f"{timestamp}_vx{args.command_vel_x:g}_yaw{args.command_yaw:g}"
        f"_scale{args.action_scale:g}"
    ).replace(".", "p").replace("-", "m")

    local_out_dir = local_dir / "logs" / RUN_NAME / run_id
    local_out_dir.mkdir(parents=True, exist_ok=True)

    remote_out_dir = posixpath.join(args.remote_dir, "logs", RUN_NAME, run_id)
    remote_pkl = posixpath.join(remote_out_dir, "diagnostic.pkl")
    remote_log = posixpath.join(remote_out_dir, "diagnostic.log")
    remote_model = posixpath.join(args.remote_dir, args.model)
    remote_config = posixpath.join(args.remote_dir, args.config)

    python_select = (
        "PYTHON=python3; "
        "[ -x $HOME/.venv/bin/python ] && PYTHON=$HOME/.venv/bin/python; "
        "[ -x $HOME/.venvs/open_duck_mini_runtime/bin/python ] "
        "&& PYTHON=$HOME/.venvs/open_duck_mini_runtime/bin/python; "
    )
    record_cmd = (
        f"cd {q(args.remote_dir)} && "
        f"{python_select}"
        "export SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy PYTHONUNBUFFERED=1; "
        f"mkdir -p {q(remote_out_dir)}; "
        "pkill -f '[v]2_rl_walk_mujoco.py' 2>/dev/null || true; "
        f"$PYTHON -u scripts/diagnostic_record.py "
        f"--onnx_model_path {q(remote_model)} "
        f"--duck_config_path {q(remote_config)} "
        f"--serial_port {q(args.serial_port)} "
        f"--control_freq {args.control_freq:g} "
        f"--action_scale {args.action_scale:g} "
        f"--pid {args.kp} {args.ki} {args.kd} "
        f"--duration {args.duration:g} "
        f"--command_vel_x {args.command_vel_x:g} "
        f"--command_vel_y {args.command_vel_y:g} "
        f"--command_yaw {args.command_yaw:g} "
        f"{'--commands ' if args.commands else ''}"
        f"--command_source {q(args.command_source)} "
        f"--pitch_bias {args.pitch_bias:g} "
        f"--output {q(remote_pkl)} "
        f"2>&1 | tee {q(remote_log)}"
    )

    print(f"[R05 REC] Local output: {local_out_dir}")
    print(f"[R05 REC] Remote output: {remote_out_dir}")
    print(
        "[R05 REC] Params: "
        f"commands={args.commands}, command_source={args.command_source}, "
        f"fallback_vx={args.command_vel_x:g}, fallback_vy={args.command_vel_y:g}, "
        f"fallback_yaw={args.command_yaw:g}, action_scale={args.action_scale:g}, "
        f"duration={args.duration:g}s"
    )

    with DuckRemote(args.host, args.user, password, args.remote_dir) as duck:
        if not args.no_sync:
            duck.sync(local_dir)

        duck.run(
            f"test -f {q(remote_model)} || "
            f"(echo 'missing remote model {remote_model}' >&2; exit 2)"
        )
        duck.run(record_cmd, check=False, stream=True)

        sftp = duck.client.open_sftp()
        try:
            for remote_path, local_name in (
                (remote_pkl, "diagnostic.pkl"),
                (remote_log, "diagnostic.log"),
            ):
                local_path = local_out_dir / local_name
                print(f"[R05 REC] Downloading {remote_path} -> {local_path}")
                sftp.get(remote_path, str(local_path))
        finally:
            sftp.close()

    print("[R05 REC] Done.")
    print(f"[R05 REC] Send this folder back for analysis: {local_out_dir}")


if __name__ == "__main__":
    main()
