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


def build_runtime_command(args: argparse.Namespace) -> list[str]:
    return [
        args.python,
        "-u",
        "scripts/v2_rl_walk_mujoco.py",
        "--onnx_model_path",
        str(args.onnx_model_path),
        "--duck_config_path",
        str(args.duck_config_path),
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


def print_event(name: str, **payload: object) -> None:
    data = {"event": name}
    data.update(payload)
    print(json.dumps(data, ensure_ascii=False, separators=(",", ":")), flush=True)


class XboxKeyboardBridge:
    def __init__(self, deadzone: float) -> None:
        import pygame

        self.pygame = pygame
        pygame.init()
        pygame.joystick.init()
        if pygame.joystick.get_count() < 1:
            raise RuntimeError("no pygame joystick found")
        self.joystick = pygame.joystick.Joystick(0)
        self.joystick.init()
        self.deadzone = deadzone
        self._a_was_pressed = False
        print_event(
            "xbox_ready",
            name=self.joystick.get_name(),
            axes=self.joystick.get_numaxes(),
            buttons=self.joystick.get_numbuttons(),
            hats=self.joystick.get_numhats(),
        )

    def close(self) -> None:
        self.pygame.joystick.quit()
        self.pygame.quit()

    def read(self) -> tuple[str, bool]:
        self.pygame.event.pump()
        axis_count = self.joystick.get_numaxes()
        lx = self._axis(0, axis_count)
        ly = self._axis(1, axis_count)
        rx = self._axis(2, axis_count)

        candidates: list[tuple[float, str]] = []
        if abs(ly) >= self.deadzone:
            candidates.append((abs(ly), "w" if ly < 0 else "s"))
        if abs(lx) >= self.deadzone:
            candidates.append((abs(lx), "a" if lx < 0 else "d"))
        if abs(rx) >= self.deadzone:
            candidates.append((abs(rx), "e" if rx > 0 else "q"))

        key = max(candidates, default=(0.0, " "), key=lambda item: item[0])[1]
        a_pressed = self.joystick.get_numbuttons() > 0 and bool(self.joystick.get_button(0))
        a_triggered = a_pressed and not self._a_was_pressed
        self._a_was_pressed = a_pressed
        return key, a_triggered

    def _axis(self, index: int, axis_count: int) -> float:
        if index >= axis_count:
            return 0.0
        return float(self.joystick.get_axis(index))


def maybe_wait_for_xbox(args: argparse.Namespace) -> int:
    if args.skip_xbox_wait:
        return 0
    wait_script = SCRIPT_DIR / "wait_xbox_ready.py"
    return subprocess.call(
        [
            args.python,
            str(wait_script),
            "--address",
            args.xbox_address,
            "--timeout",
            str(args.xbox_timeout),
        ]
    )


def detect_stable_arrow(args: argparse.Namespace, model, camera, votes, scores) -> tuple[str, float, str, float]:
    frame = camera.capture_array()
    frame = orient_frame(frame, args.rotate, args.flip_horizontal, args.flip_vertical)
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
    stable_label, stable_confidence = stable_prediction(votes, scores, args.confidence, args.min_votes)
    return stable_label, stable_confidence, raw_label, raw_confidence


def run(args: argparse.Namespace) -> int:
    if maybe_wait_for_xbox(args) != 0:
        return 1

    xbox = XboxKeyboardBridge(args.xbox_deadzone)
    model = load_tflite_model(args.model, LABELS)
    print_event("vision_start", mode="tflite" if model is not None else "geometry")
    camera = create_camera(args.width, args.height)
    runtime_process, master_fd = start_runtime(args)
    print_event("runtime_start", pid=runtime_process.pid, command=build_runtime_command(args))

    votes: deque[str] = deque(maxlen=args.vote_window)
    scores: deque[float] = deque(maxlen=args.vote_window)
    last_key = None
    last_sent_at = 0.0
    paused = True
    auto_turn_until = 0.0
    auto_turn_key = " "
    auto_turn_label = "none"
    cooldown_until = 0.0
    armed = True
    clear_count = 0
    frame_count = 0
    interval_s = 1.0 / max(args.hz, 0.1)

    try:
        while runtime_process.poll() is None:
            loop_started_at = time.monotonic()
            drain_runtime_output(master_fd)
            now = time.monotonic()

            stable_label, stable_confidence, raw_label, raw_confidence = detect_stable_arrow(
                args, model, camera, votes, scores
            )

            if stable_label not in {"left", "right"}:
                clear_count += 1
                if clear_count >= args.rearm_clear_frames:
                    armed = True
            else:
                clear_count = 0

            xbox_key, a_triggered = xbox.read()
            if a_triggered:
                paused = not paused
                send_key(master_fd, "p")
                print_event("xbox_a", runtime_state="paused" if paused else "running")

            if (
                args.enable_vision_turn
                and armed
                and not paused
                and now >= cooldown_until
                and stable_label in {"left", "right"}
            ):
                auto_turn_label = stable_label
                auto_turn_key = "q" if stable_label == "left" else "e"
                auto_turn_until = now + args.turn_duration_s
                cooldown_until = now + args.turn_cooldown_s
                armed = False
                clear_count = 0
                print_event(
                    "vision_turn_start",
                    arrow=stable_label,
                    key=auto_turn_key,
                    duration_s=args.turn_duration_s,
                )

            if now < auto_turn_until:
                key = auto_turn_key
                control = f"vision_turn_{auto_turn_label}"
            elif auto_turn_key != " ":
                key = " "
                control = "vision_turn_done"
                auto_turn_key = " "
                if args.pause_after_turn and not paused:
                    send_key(master_fd, "p")
                    paused = True
                    print_event("vision_turn_pause")
            elif paused:
                key = " "
                control = "paused"
            else:
                key = xbox_key
                control = "xbox_manual"

            if key != last_key or now - last_sent_at >= args.command_repeat_s:
                send_key(master_fd, key)
                last_key = key
                last_sent_at = now

            print(
                json.dumps(
                    {
                        "arrow": stable_label,
                        "confidence": round(stable_confidence, 4),
                        "raw_arrow": raw_label,
                        "raw_confidence": round(raw_confidence, 4),
                        "control": control,
                        "key": "space" if key == " " else key,
                        "paused": paused,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                flush=True,
            )

            frame_count += 1
            if args.max_frames and frame_count >= args.max_frames:
                break
            time.sleep(max(0.0, interval_s - (time.monotonic() - loop_started_at)))

        drain_runtime_output(master_fd)
        return runtime_process.poll() or 0
    except KeyboardInterrupt:
        print_event("interrupted")
        return 130
    finally:
        camera.stop()
        xbox.close()
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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Use Xbox for manual Open Duck control, with Camera V2 left/right arrow turn assist."
    )
    default_runtime_root = Path.home() / "open_duck_mini_runtime"
    parser.add_argument("--python", default=str(default_runtime_root / ".venv/bin/python"))
    parser.add_argument("--runtime-root", type=Path, default=default_runtime_root)
    parser.add_argument("--onnx_model_path", type=Path, default=default_runtime_root / "BEST_WALK_ONNX_2.onnx")
    parser.add_argument("--duck_config_path", type=Path, default=default_runtime_root / "duck_config.json")
    parser.add_argument("--control_freq", type=int, default=50)
    parser.add_argument("-p", "--kp", type=int, default=22)
    parser.add_argument("-d", "--kd", type=int, default=0)
    parser.add_argument("--action_scale", type=float, default=0.2)
    parser.add_argument("--min_motor_voltage", type=float, default=6.5)
    parser.add_argument("--xbox-address", default="C0:D6:D5:E9:D7:A9")
    parser.add_argument("--xbox-timeout", type=float, default=30.0)
    parser.add_argument("--xbox-deadzone", type=float, default=0.35)
    parser.add_argument("--skip-xbox-wait", action="store_true")
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
    parser.add_argument("--turn-duration-s", type=float, default=1.55)
    parser.add_argument("--turn-cooldown-s", type=float, default=4.0)
    parser.add_argument("--rearm-clear-frames", type=int, default=3)
    parser.add_argument("--no-vision-turn", action="store_false", dest="enable_vision_turn")
    parser.add_argument("--no-pause-after-turn", action="store_false", dest="pause_after_turn")
    parser.add_argument("--max-frames", type=int, default=0)
    args = parser.parse_args()

    raise SystemExit(run(args))


if __name__ == "__main__":
    main()
