#!/usr/bin/env python3
"""Quick hardware check for a BNO055 IMU on Raspberry Pi I2C."""

from __future__ import annotations

import argparse
import sys
import time
from typing import Iterable

import adafruit_bno055
import board


MODE_BY_NAME = {
    "ndof": adafruit_bno055.NDOF_MODE,
    "imuplus": adafruit_bno055.IMUPLUS_MODE,
    "accgyro": adafruit_bno055.ACCGYRO_MODE,
    "amg": adafruit_bno055.AMG_MODE,
}


def format_tuple(value: Iterable[float] | None, unit: str = "") -> str:
    if value is None:
        return "None"
    return "(" + ", ".join(f"{item:8.3f}{unit}" for item in value) + ")"


def scan_i2c(i2c) -> list[int]:
    while not i2c.try_lock():
        time.sleep(0.01)
    try:
        return i2c.scan()
    finally:
        i2c.unlock()


def parse_address(value: str) -> int:
    try:
        return int(value, 0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid I2C address {value!r}; use decimal or hex like 0x28"
        ) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check a BNO055 IMU over Raspberry Pi I2C."
    )
    parser.add_argument(
        "--address",
        type=parse_address,
        default=0x28,
        help="BNO055 I2C address, usually 0x28 or 0x29. Default: 0x28",
    )
    parser.add_argument(
        "--mode",
        choices=sorted(MODE_BY_NAME),
        default="ndof",
        help="BNO055 operation mode used during sampling. Default: ndof",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=20,
        help="Number of samples to print. Default: 20",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.2,
        help="Seconds between samples. Default: 0.2",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.samples < 1:
        print("ERROR: --samples must be >= 1", file=sys.stderr)
        return 2
    if args.interval < 0:
        print("ERROR: --interval must be >= 0", file=sys.stderr)
        return 2

    print("Creating Raspberry Pi I2C bus...")
    i2c = board.I2C()

    devices = scan_i2c(i2c)
    print("I2C devices:", " ".join(f"0x{addr:02x}" for addr in devices) or "none")
    if args.address not in devices:
        print(
            f"WARNING: address 0x{args.address:02x} was not found in the I2C scan.",
            file=sys.stderr,
        )
        print(
            "         Check wiring, power, I2C enablement, or try --address 0x29.",
            file=sys.stderr,
        )

    try:
        sensor = adafruit_bno055.BNO055_I2C(i2c, address=args.address)
        sensor.mode = MODE_BY_NAME[args.mode]
        time.sleep(0.7)
    except Exception as exc:
        print(f"ERROR: failed to initialize BNO055 at 0x{args.address:02x}: {exc}", file=sys.stderr)
        return 1

    print(f"BNO055 initialized at 0x{args.address:02x}, mode={args.mode}")
    print("Move the IMU gently; euler/quaternion/accel values should change.")
    print("Calibration is sys/gyro/accel/mag, each from 0 to 3.")
    print()

    for index in range(1, args.samples + 1):
        try:
            calibration = sensor.calibration_status
            temperature = sensor.temperature
            euler = sensor.euler
            quaternion = sensor.quaternion
            accel = sensor.acceleration
            gyro = sensor.gyro
            mag = sensor.magnetic
            linear_accel = sensor.linear_acceleration
            gravity = sensor.gravity
        except Exception as exc:
            print(f"ERROR: read failed on sample {index}: {exc}", file=sys.stderr)
            return 1

        print(
            f"[{index:03d}] "
            f"cal={calibration} "
            f"temp={temperature}C "
            f"euler={format_tuple(euler, 'deg')} "
            f"quat={format_tuple(quaternion)}"
        )
        print(
            "      "
            f"accel={format_tuple(accel, 'm/s^2')} "
            f"gyro={format_tuple(gyro, 'rad/s')} "
            f"mag={format_tuple(mag, 'uT')}"
        )
        print(
            "      "
            f"linear={format_tuple(linear_accel, 'm/s^2')} "
            f"gravity={format_tuple(gravity, 'm/s^2')}"
        )
        time.sleep(args.interval)

    print()
    print("BNO055 check completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
