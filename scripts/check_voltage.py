import argparse
import os

import numpy as np

from mini_bdx_runtime.duck_config import DuckConfig
from mini_bdx_runtime.rustypot_position_hwi import HWI


REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="/dev/ttyACM0")
    parser.add_argument(
        "--duck_config_path",
        default=os.path.join(REPO_DIR, "duck_config.json"),
    )
    parser.add_argument("--warn_below", type=float, default=7.1)
    parser.add_argument("--fatal_below", type=float, default=6.8)
    args = parser.parse_args()

    config = DuckConfig(args.duck_config_path)
    hwi = HWI(config, args.port)
    voltages = hwi.get_present_voltages()
    if voltages is None:
        raise RuntimeError("Could not read motor voltages")
    currents = hwi.get_present_currents()

    if currents is None:
        for name, voltage in zip(hwi.joint_names, voltages):
            print(f"{name:16s} {float(voltage):.2f} V")
    else:
        for name, voltage, current in zip(hwi.joint_names, voltages, currents):
            print(f"{name:16s} {float(voltage):.2f} V  {float(current):+.3f} A")

    min_voltage = float(np.min(voltages))
    print("===")
    summary = (
        f"min={min_voltage:.2f} V "
        f"max={float(np.max(voltages)):.2f} V "
        f"mean={float(np.mean(voltages)):.2f} V"
    )
    if currents is not None:
        abs_currents = np.abs(currents)
        summary += (
            f"  sum_abs_current={float(np.sum(abs_currents)):.2f} A "
            f"max_abs_current={float(np.max(abs_currents)):.2f} A"
        )
    print(summary)

    if min_voltage < args.fatal_below:
        print(f"[FATAL] below {args.fatal_below:.2f} V; charge battery before walking.")
    elif min_voltage < args.warn_below:
        print(f"[WARN] below {args.warn_below:.2f} V; walking may brown out under load.")


if __name__ == "__main__":
    main()
