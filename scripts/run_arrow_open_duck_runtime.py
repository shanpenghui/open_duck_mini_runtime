#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import pty
import select
import signal
import subprocess
import sys
import tempfile
import time
from collections import deque
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from recognize_arrow_camera_v2 import (  # noqa: E402
    LABELS,
    crop_roi,
    create_camera,
    detect_arrow,
    load_tflite_model,
    orient_frame,
    parse_roi,
    predict_tflite,
    stable_prediction,
)


KEY_BY_LABEL = {
    "left": "a",
    "right": "d",
    "forward": "w",
    "back": "e",
    "stop": " ",
    "none": " ",
}

STATE_BY_LABEL = {
    "forward": "walk_forward",
    "back": "turn_right",
    "left": "strafe_left",
    "right": "strafe_right",
    "stop": "hold_still",
    "none": "hold_still",
}


def build_runtime_command(args: argparse.Namespace) -> list[str]:
    return [
        args.python,
        "-u",
        str(args.runtime_script),
        "--onnx_model_path",
        str(args.onnx_model_path),
        "--duck_config_path",
        str(args.runtime_duck_config_path),
        "--commands",
        "--command_source",
        "keyboard",
        "-c",
        str(args.control_freq),
        "-p",
        str(args.kp),
        "-d",
        str(args.kd),
        "--action_scale",
        str(args.action_scale),
        "--min_motor_voltage",
        str(args.min_motor_voltage),
    ]


def start_runtime(args: argparse.Namespace) -> tuple[subprocess.Popen[bytes], int]:
    master_fd, slave_fd = pty.openpty()
    env = os.environ.copy()
    runtime_root = str(args.runtime_root)
    package_root = str(Path(runtime_root) / "mini_bdx_runtime")
    env["PYTHONPATH"] = f"{package_root}:{runtime_root}:{env.get('PYTHONPATH', '')}"
    env.setdefault("SDL_VIDEODRIVER", "dummy")
    env.setdefault("SDL_AUDIODRIVER", "dummy")

    process = subprocess.Popen(
        build_runtime_command(args),
        cwd=runtime_root,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        env=env,
        start_new_session=True,
        close_fds=True,
    )
    os.close(slave_fd)
    return process, master_fd


def drain_runtime_output(master_fd: int) -> str:
    chunks = []
    while True:
        readable, _, _ = select.select([master_fd], [], [], 0)
        if not readable:
            return "".join(chunks)
        try:
            data = os.read(master_fd, 4096)
        except OSError:
            return "".join(chunks)
        if not data:
            return "".join(chunks)
        text = data.decode(errors="replace")
        chunks.append(text)
        sys.stdout.write(text)
        sys.stdout.flush()


def send_key(master_fd: int, key: str) -> None:
    os.write(master_fd, key.encode("utf-8"))


def config_starts_paused(config_path: Path) -> bool:
    try:
        with config_path.open("r", encoding="utf-8") as f:
            config = json.load(f)
    except Exception:
        return False
    return bool(config.get("start_paused", False))


def prepare_runtime_config(args: argparse.Namespace) -> Path:
    if args.keep_config_start_paused:
        args.runtime_duck_config_path = args.duck_config_path
        return args.duck_config_path

    try:
        with args.duck_config_path.open("r", encoding="utf-8") as f:
            config = json.load(f)
    except Exception:
        args.runtime_duck_config_path = args.duck_config_path
        return args.duck_config_path

    config["start_paused"] = False
    temp = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix="arrow_duck_config_",
        suffix=".json",
        delete=False,
    )
    with temp:
        json.dump(config, temp, indent=4)
        temp.write("\n")
    args.runtime_duck_config_path = Path(temp.name)
    return args.runtime_duck_config_path


def print_arrow(
    label: str,
    confidence: float,
    raw_label: str,
    raw_confidence: float,
    state: str,
    key: str,
) -> None:
    print(
        json.dumps(
            {
                "arrow": label,
                "confidence": round(confidence, 4),
                "raw_arrow": raw_label,
                "raw_confidence": round(raw_confidence, 4),
                "state": state,
                "key": "space" if key == " " else key,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        flush=True,
    )


def save_debug_frame(frame, path: Path | None) -> None:
    if path is None:
        return
    import cv2

    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))


def run(args: argparse.Namespace) -> int:
    model = load_tflite_model(args.model, LABELS)
    mode = "tflite" if model is not None else "geometry"
    print(json.dumps({"event": "vision_start", "mode": mode}, ensure_ascii=False), flush=True)

    camera = create_camera(args.width, args.height)
    if args.vision_only:
        print(json.dumps({"event": "vision_only", "runtime": "disabled"}, ensure_ascii=False), flush=True)
        return run_vision_only(args, model, camera)

    prepare_runtime_config(args)
    runtime_process, master_fd = start_runtime(args)
    print(
        json.dumps(
            {
                "event": "runtime_start",
                "pid": runtime_process.pid,
                "command": build_runtime_command(args),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    votes: deque[str] = deque(maxlen=args.vote_window)
    scores: deque[float] = deque(maxlen=args.vote_window)
    last_key = None
    last_sent_at = 0.0
    current_state = "hold_still"
    should_auto_unpause = (
        args.auto_unpause
        and args.keep_config_start_paused
        and config_starts_paused(args.duck_config_path)
    )
    auto_unpause_sent = False
    runtime_started_at = time.monotonic()
    frame_count = 0
    interval_s = 1.0 / max(args.hz, 0.1)

    try:
        while runtime_process.poll() is None:
            loop_started_at = time.monotonic()
            runtime_output = drain_runtime_output(master_fd)
            if should_auto_unpause and not auto_unpause_sent:
                ready = (
                    "Done instantiating RLWalk" in runtime_output
                    or "[KeyboardController]" in runtime_output
                    or time.monotonic() - runtime_started_at > args.auto_unpause_delay
                )
                if ready:
                    send_key(master_fd, "p")
                    auto_unpause_sent = True
                    print(
                        json.dumps(
                            {"event": "auto_unpause", "key": "p"},
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        flush=True,
                    )

            frame = camera.capture_array()
            frame = orient_frame(frame, args.rotate, args.flip_horizontal, args.flip_vertical)
            if frame_count == 0:
                save_debug_frame(frame, args.save_frame)
            roi = crop_roi(frame, args.roi)
            if model is not None:
                raw_label, raw_confidence = predict_tflite(model, roi)
            else:
                raw_label, raw_confidence, _ = detect_arrow(
                    roi,
                    args.min_area,
                    args.threshold_mode,
                    args.detector,
                    args.sample_dir,
                    args.sample_rotation_deg,
                    args.sample_rotation_step,
                )

            label = raw_label if raw_confidence >= args.confidence else "none"
            votes.append(label)
            scores.append(raw_confidence)
            stable_label, stable_confidence = stable_prediction(
                votes, scores, args.confidence, args.min_votes
            )

            current_state = STATE_BY_LABEL.get(stable_label, "hold_still")
            key = KEY_BY_LABEL.get(stable_label, " ")
            now = time.monotonic()
            if key != last_key or now - last_sent_at >= args.command_repeat_s:
                send_key(master_fd, key)
                last_key = key
                last_sent_at = now

            print_arrow(stable_label, stable_confidence, raw_label, raw_confidence, current_state, key)

            frame_count += 1
            if args.max_frames and frame_count >= args.max_frames:
                break

            elapsed = time.monotonic() - loop_started_at
            time.sleep(max(0.0, interval_s - elapsed))

        drain_runtime_output(master_fd)
        return runtime_process.poll() or 0
    except KeyboardInterrupt:
        print(json.dumps({"event": "interrupted"}, ensure_ascii=False), flush=True)
        return 130
    finally:
        camera.stop()
        try:
            send_key(master_fd, " ")
        except OSError:
            pass
        if runtime_process.poll() is None:
            os.killpg(runtime_process.pid, signal.SIGINT)
            try:
                runtime_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(runtime_process.pid, signal.SIGTERM)
        os.close(master_fd)
        if (
            not args.keep_config_start_paused
            and getattr(args, "runtime_duck_config_path", None) is not None
            and args.runtime_duck_config_path != args.duck_config_path
        ):
            try:
                args.runtime_duck_config_path.unlink()
            except OSError:
                pass


def run_vision_only(args: argparse.Namespace, model: dict[str, object] | None, camera) -> int:
    votes: deque[str] = deque(maxlen=args.vote_window)
    scores: deque[float] = deque(maxlen=args.vote_window)
    interval_s = 1.0 / max(args.hz, 0.1)
    frame_count = 0
    try:
        while True:
            loop_started_at = time.monotonic()
            frame = camera.capture_array()
            frame = orient_frame(frame, args.rotate, args.flip_horizontal, args.flip_vertical)
            if frame_count == 0:
                save_debug_frame(frame, args.save_frame)
            roi = crop_roi(frame, args.roi)
            if model is not None:
                raw_label, raw_confidence = predict_tflite(model, roi)
            else:
                raw_label, raw_confidence, _ = detect_arrow(
                    roi,
                    args.min_area,
                    args.threshold_mode,
                    args.detector,
                    args.sample_dir,
                    args.sample_rotation_deg,
                    args.sample_rotation_step,
                )
            label = raw_label if raw_confidence >= args.confidence else "none"
            votes.append(label)
            scores.append(raw_confidence)
            stable_label, stable_confidence = stable_prediction(
                votes, scores, args.confidence, args.min_votes
            )
            state = STATE_BY_LABEL.get(stable_label, "hold_still")
            key = KEY_BY_LABEL.get(stable_label, " ")
            print_arrow(stable_label, stable_confidence, raw_label, raw_confidence, state, key)
            frame_count += 1
            if args.max_frames and frame_count >= args.max_frames:
                return 0
            time.sleep(max(0.0, interval_s - (time.monotonic() - loop_started_at)))
    except KeyboardInterrupt:
        return 130
    finally:
        camera.stop()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Control Open Duck Mini runtime with Camera V2 arrow recognition."
    )
    parser.add_argument("--python", default="/home/duck/.venv/bin/python")
    parser.add_argument("--runtime-root", type=Path, default=Path("/home/duck/open_duck_mini_runtime"))
    parser.add_argument(
        "--runtime-script",
        type=Path,
        default=Path("/home/duck/open_duck_mini_runtime/scripts/v2_rl_walk_mujoco.py"),
    )
    parser.add_argument(
        "--onnx_model_path",
        type=Path,
        default=Path("/home/duck/open_duck_mini_runtime/BEST_WALK_ONNX_2.onnx"),
    )
    parser.add_argument(
        "--duck_config_path",
        type=Path,
        default=Path("/home/duck/open_duck_mini_runtime/duck_config.json"),
    )
    parser.add_argument("--control_freq", type=int, default=50)
    parser.add_argument("-p", "--kp", type=int, default=22)
    parser.add_argument("-d", "--kd", type=int, default=0)
    parser.add_argument("--action_scale", type=float, default=0.2)
    parser.add_argument("--min_motor_voltage", type=float, default=6.5)
    parser.add_argument("--model", type=Path, default=Path("models/arrow_classifier_int8.tflite"))
    parser.add_argument("--width", type=int, default=320)
    parser.add_argument("--height", type=int, default=240)
    parser.add_argument("--roi", type=parse_roi, default="80,40,160,160", help="x,y,width,height")
    parser.add_argument("--rotate", type=int, choices=[0, 90, 180, 270], default=0)
    parser.add_argument("--flip-horizontal", action="store_true")
    parser.add_argument("--flip-vertical", action="store_true")
    parser.add_argument("--hz", type=float, default=4.0)
    parser.add_argument("--confidence", type=float, default=0.6)
    parser.add_argument("--vote-window", type=int, default=5)
    parser.add_argument("--min-votes", type=int, default=3)
    parser.add_argument("--min-area", type=float, default=120.0)
    parser.add_argument("--detector", choices=["sample", "template", "tip"], default="sample")
    parser.add_argument("--sample-dir", type=Path, default=Path("assets/arrow_samples"))
    parser.add_argument("--sample-rotation-deg", type=int, default=25)
    parser.add_argument("--sample-rotation-step", type=int, default=5)
    parser.add_argument("--threshold-mode", choices=["auto", "dark", "light"], default="auto")
    parser.add_argument("--command-repeat-s", type=float, default=0.25)
    parser.add_argument("--auto-unpause-delay", type=float, default=4.0)
    parser.add_argument(
        "--no-auto-unpause",
        action="store_false",
        dest="auto_unpause",
        help="do not send keyboard 'p' to unpause when duck_config start_paused is true",
    )
    parser.add_argument(
        "--keep-config-start-paused",
        action="store_true",
        help="pass duck_config_path as-is instead of using a temporary start_paused=false copy",
    )
    parser.add_argument("--max-frames", type=int, default=0)
    parser.add_argument(
        "--save-frame",
        type=Path,
        default=None,
        help="save the first full camera frame to this image path",
    )
    parser.add_argument(
        "--vision-only",
        action="store_true",
        help="print arrow directions without launching Open Duck runtime",
    )
    args = parser.parse_args()

    raise SystemExit(run(args))


if __name__ == "__main__":
    main()
