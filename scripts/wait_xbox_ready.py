#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path


def run(command: list[str], timeout: float = 8.0) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(command, text=True, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None


def bluetooth_info(address: str) -> str:
    result = run(["bluetoothctl", "info", address], timeout=5)
    if result is None:
        return ""
    return result.stdout + result.stderr


def is_connected(address: str) -> bool:
    return "Connected: yes" in bluetooth_info(address)


def joystick_count() -> int:
    import pygame

    pygame.init()
    pygame.joystick.init()
    try:
        return pygame.joystick.get_count()
    finally:
        pygame.joystick.quit()
        pygame.quit()


def describe_joysticks() -> list[str]:
    import pygame

    pygame.init()
    pygame.joystick.init()
    descriptions: list[str] = []
    try:
        for i in range(pygame.joystick.get_count()):
            joystick = pygame.joystick.Joystick(i)
            joystick.init()
            descriptions.append(
                f"{i}: {joystick.get_name()} "
                f"axes={joystick.get_numaxes()} "
                f"buttons={joystick.get_numbuttons()} "
                f"hats={joystick.get_numhats()}"
            )
    finally:
        pygame.joystick.quit()
        pygame.quit()
    return descriptions


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--address", default="91:B4:9E:A2:3C:ED")
    parser.add_argument("--timeout", type=float, default=25.0)
    parser.add_argument("--connect-timeout", type=float, default=10.0)
    args = parser.parse_args()

    run(["sudo", "-n", "modprobe", "hid-xpadneo"], timeout=5)
    run(["sudo", "-n", "modprobe", "hid_xpadneo"], timeout=5)

    deadline = None if args.timeout <= 0 else time.monotonic() + args.timeout
    last_status = ""
    while deadline is None or time.monotonic() < deadline:
        connected = is_connected(args.address)
        if not connected:
            run(["bluetoothctl", "connect", args.address], timeout=args.connect_timeout)
            connected = is_connected(args.address)

        js_nodes = sorted(Path("/dev/input").glob("js*"))
        js0_exists = Path("/dev/input/js0").exists()
        count = joystick_count()
        if connected and js0_exists and count > 0:
            print("[xbox] ready")
            print(f"[xbox] bluetooth {args.address}: Connected: yes")
            print("[xbox] nodes:", ", ".join(str(path) for path in js_nodes))
            for description in describe_joysticks():
                print("[xbox]", description)
            return 0

        status = "connected" if connected else "not connected"
        node_status = "js0 present" if js0_exists else "js0 missing"
        current_status = f"bluetooth {status}, {node_status}, pygame joysticks={count}"
        if current_status != last_status:
            print(f"[xbox] waiting: {current_status}", flush=True)
            last_status = current_status
        time.sleep(1.0)

    print("[xbox] not ready before timeout", file=sys.stderr)
    print(bluetooth_info(args.address), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

