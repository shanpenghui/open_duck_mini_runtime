#!/usr/bin/env python3
"""
Single-servo step response test for sim2real actuator matching.

Records the actual servo response to a position step command at various kp
settings. This data is used to calibrate the MuJoCo XML actuator parameters
(kp, damping, frictionloss, armature, forcerange) to match real hardware.

Usage:
  # Step response on left_knee (ID 23) at kp=30, 0→1.0 rad
  python scripts/servo_step_response.py --id 23 --kp 30 --goal 1.0

  # Sweep multiple kp values
  python scripts/servo_step_response.py --id 23 --kp 13,30,50 --goal 1.0

  # Test with custom load (hold robot in specific pose)
  python scripts/servo_step_response.py --id 23 --kp 30 --goal 1.0 --duration 5

Output: pkl file with time-series data + auto summary
"""

import argparse
import os
import pickle
import sys
import time

import numpy as np

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_DIR = os.path.dirname(_SCRIPT_DIR)

# ST3215 servo IDs in the duck robot
SERVO_MAP = {
    "left_hip_yaw": 20,
    "left_hip_roll": 21,
    "left_hip_pitch": 22,
    "left_knee": 23,
    "left_ankle": 24,
    "neck_pitch": 30,
    "head_pitch": 31,
    "head_yaw": 32,
    "head_roll": 33,
    "right_hip_yaw": 10,
    "right_hip_roll": 11,
    "right_hip_pitch": 12,
    "right_knee": 13,
    "right_ankle": 14,
}


def resolve_id(id_or_name):
    """Accept servo ID (int) or joint name (string)."""
    try:
        return int(id_or_name)
    except ValueError:
        if id_or_name in SERVO_MAP:
            return SERVO_MAP[id_or_name]
        print(f"Unknown joint name: {id_or_name}")
        print(f"Available: {list(SERVO_MAP.keys())}")
        sys.exit(1)


def scalar_reading(value):
    if isinstance(value, (list, tuple)):
        if len(value) != 1:
            raise ValueError(f"Expected one reading, got {len(value)}: {value!r}")
        return value[0]
    return value


def run_step_test(io, servo_id, kp, kd, goal_pos, duration, settle_time,
                  sample_interval, servo_name):
    """Run one step response test and return recorded data."""

    # Configure servo
    io.write_p_coefficient(servo_id, kp)
    io.write_d_coefficient(servo_id, kd)

    # Move to start position (0.0 or custom start)
    print(f"  Moving to start position (0.0 rad)...")
    io.write_goal_position(servo_id, 0.0)
    time.sleep(settle_time)

    # Verify start position
    start_pos = scalar_reading(io.read_present_position(servo_id))
    print(f"  Start position: {start_pos:.3f} rad ({np.degrees(start_pos):.1f}°)")

    # Recording buffers
    times = []
    positions = []
    velocities = []
    currents = []
    goal_positions = []

    # Start step
    print(f"  Stepping to {goal_pos:.3f} rad ({np.degrees(goal_pos):.1f}°)...")
    io.write_goal_position(servo_id, goal_pos)
    started_at = time.time()

    while True:
        t = time.time() - started_at
        if t > duration:
            break

        # Continuously write goal (in case of bus noise)
        io.write_goal_position(servo_id, goal_pos)

        times.append(t)
        goal_positions.append(goal_pos)

        try:
            positions.append(scalar_reading(io.read_present_position(servo_id)))
        except Exception:
            positions.append(positions[-1] if positions else 0.0)

        try:
            velocities.append(scalar_reading(io.read_present_speed(servo_id)))
        except Exception:
            velocities.append(0.0)

        try:
            currents.append(scalar_reading(io.read_present_current(servo_id)))
        except Exception:
            currents.append(0.0)

        time.sleep(sample_interval)

    # Return to zero
    print(f"  Returning to 0.0...")
    io.write_goal_position(servo_id, 0.0)
    time.sleep(settle_time)

    return {
        "kp": kp,
        "kd": kd,
        "goal_pos": goal_pos,
        "start_pos": start_pos,
        "servo_id": servo_id,
        "servo_name": servo_name,
        "times": np.array(times),
        "positions": np.array(positions),
        "velocities": np.array(velocities),
        "currents_raw": np.array(currents),
        "currents_A": np.array(currents) * 0.0065,  # STS3215: 6.5mA per unit
        "goal_positions": np.array(goal_positions),
        "duration": duration,
        "sample_interval": sample_interval,
    }


def analyze_step(data):
    """Print summary of a single step response."""
    t = data["times"]
    pos = data["positions"]
    vel = data["velocities"]
    goal = data["goal_pos"]
    cur = data["currents_A"]

    # Find 90% rise time
    target = goal
    threshold_90 = 0.9 * target
    threshold_95 = 0.95 * target
    threshold_98 = 0.98 * target

    rise_90 = None
    rise_95 = None
    rise_98 = None
    overshoot = 0.0
    settled_pos = np.mean(pos[-20:]) if len(pos) > 20 else pos[-1]

    for i, p in enumerate(pos):
        if rise_90 is None and abs(p) >= abs(threshold_90):
            rise_90 = t[i]
        if rise_95 is None and abs(p) >= abs(threshold_95):
            rise_95 = t[i]
        if rise_98 is None and abs(p) >= abs(threshold_98):
            rise_98 = t[i]

    # Overshoot
    if target > 0:
        peak = np.max(pos)
    else:
        peak = np.min(pos)
    if abs(target) > 0.01:
        overshoot_pct = (abs(peak) - abs(target)) / abs(target) * 100
    else:
        overshoot_pct = 0.0

    # Steady state error
    ss_error = settled_pos - goal

    # Peak velocity
    peak_vel = np.max(np.abs(vel))

    # Peak current
    peak_cur = np.max(np.abs(cur))

    print(f"\n  ── Step Response Summary (kp={data['kp']}, kd={data['kd']}) ──")
    print(f"  Goal:           {goal:.3f} rad ({np.degrees(goal):.1f}°)")
    print(f"  Settled at:     {settled_pos:.3f} rad ({np.degrees(settled_pos):.1f}°)")
    print(f"  SS error:       {np.degrees(ss_error):.2f}°")
    print(f"  Rise time 90%:  {rise_90:.3f}s" if rise_90 else "  Rise time 90%:  N/A")
    print(f"  Rise time 95%:  {rise_95:.3f}s" if rise_95 else "  Rise time 95%:  N/A")
    print(f"  Rise time 98%:  {rise_98:.3f}s" if rise_98 else "  Rise time 98%:  N/A")
    print(f"  Overshoot:      {overshoot_pct:.1f}%")
    print(f"  Peak velocity:  {peak_vel:.2f} rad/s")
    print(f"  Peak current:   {peak_cur:.3f} A")

    # Diagnose
    if rise_90 is not None and rise_90 > 0.3:
        print(f"  ⚠️  Slow response (>0.3s to 90%) — servo may be under-powered or kp too low")
    if overshoot_pct > 20:
        print(f"  ⚠️  Large overshoot ({overshoot_pct:.0f}%) — kd might help, or reduce kp")
    if abs(np.degrees(ss_error)) > 3:
        print(f"  ⚠️  Steady-state error ({np.degrees(ss_error):.1f}°) — friction or gravity load")

    return {
        "rise_90": rise_90,
        "rise_95": rise_95,
        "rise_98": rise_98,
        "overshoot_pct": overshoot_pct,
        "ss_error_deg": np.degrees(ss_error),
        "peak_vel": peak_vel,
        "peak_cur": peak_cur,
        "settled_pos": settled_pos,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Single-servo step response test for sim2real calibration"
    )
    parser.add_argument("--id", type=str, required=True,
                        help="Servo ID (number) or joint name (e.g. left_knee)")
    parser.add_argument("--port", type=str, default="/dev/ttyACM0")
    parser.add_argument("--kp", type=str, default="30",
                        help="KP value(s), comma-separated for sweep (e.g. 13,30,50)")
    parser.add_argument("--kd", type=int, default=0,
                        help="KD value (damping)")
    parser.add_argument("--goal", type=float, default=1.0,
                        help="Goal position in radians")
    parser.add_argument("--duration", type=float, default=3.0,
                        help="Recording duration per test (seconds)")
    parser.add_argument("--settle_time", type=float, default=2.0,
                        help="Wait time before and after each test (seconds)")
    parser.add_argument("--sample_interval", type=float, default=0.01,
                        help="Time between samples (seconds, default 10ms ≈ 100Hz)")
    parser.add_argument("--output", type=str, default=None,
                        help="Output pkl path (auto-generated if omitted)")
    args = parser.parse_args()

    servo_id = resolve_id(args.id)
    servo_name = [k for k, v in SERVO_MAP.items() if v == servo_id]
    servo_name = servo_name[0] if servo_name else str(servo_id)

    # Parse kp sweep
    kp_values = [int(x.strip()) for x in args.kp.split(",")]

    if args.output is None:
        ts = time.strftime("%Y%m%d_%H%M%S")
        kp_str = "_".join(str(k) for k in kp_values)
        args.output = os.path.join(
            _REPO_DIR,
            f"step_response_{ts}_{servo_name}_kp{kp_str}.pkl"
        )

    print(f"{'='*60}")
    print(f"  SERVO STEP RESPONSE TEST")
    print(f"{'='*60}")
    print(f"  Servo:  {servo_name} (ID {servo_id})")
    print(f"  KP(s):  {kp_values}")
    print(f"  KD:     {args.kd}")
    print(f"  Goal:   {args.goal:.3f} rad ({np.degrees(args.goal):.1f}°)")
    print(f"  Duration: {args.duration}s per test")
    print(f"  Output: {args.output}")
    print(f"{'='*60}")

    # Import rustypot
    try:
        import rustypot
    except ImportError:
        print("[ERROR] rustypot not installed. Run on the robot's Raspberry Pi.")
        sys.exit(1)

    if hasattr(rustypot, "feetech"):
        io = rustypot.feetech(args.port, 1000000)
    else:
        io = rustypot.Sts3215PyController(args.port, 1000000, 0.05)

    # Run tests
    all_results = []
    all_summaries = []

    for i, kp in enumerate(kp_values):
        print(f"\n{'─'*60}")
        print(f"  Test {i+1}/{len(kp_values)}: kp={kp}, kd={args.kd}")
        print(f"{'─'*60}")

        data = run_step_test(
            io, servo_id, kp, args.kd, args.goal,
            args.duration, args.settle_time, args.sample_interval,
            servo_name
        )
        summary = analyze_step(data)

        all_results.append(data)
        all_summaries.append(summary)

        if i < len(kp_values) - 1:
            print(f"\n  Waiting {args.settle_time}s before next test...")
            time.sleep(args.settle_time)

    # Save
    output_data = {
        "tests": all_results,
        "summaries": all_summaries,
        "servo_name": servo_name,
        "servo_id": servo_id,
        "goal_pos": args.goal,
        "kd": args.kd,
    }

    with open(args.output, "wb") as f:
        pickle.dump(output_data, f)

    print(f"\n{'='*60}")
    print(f"  SAVED: {args.output}")
    print(f"  Size: {os.path.getsize(args.output)/1024:.1f} KB")
    print(f"{'='*60}")

    # Comparison table if multiple KPs
    if len(kp_values) > 1:
        print(f"\n  ── KP Comparison ──")
        print(f"  {'KP':>4s}  {'Rise90':>8s}  {'Rise95':>8s}  {'Overshoot':>10s}  {'SS_err':>8s}  {'PeakCur':>8s}")
        print(f"  {'─'*52}")
        for kp, s in zip(kp_values, all_summaries):
            r90 = f"{s['rise_90']:.3f}s" if s['rise_90'] else "N/A"
            r95 = f"{s['rise_95']:.3f}s" if s['rise_95'] else "N/A"
            print(f"  {kp:4d}  {r90:>8s}  {r95:>8s}  "
                  f"{s['overshoot_pct']:9.1f}%  {s['ss_error_deg']:7.2f}°  {s['peak_cur']:7.3f}A")

        print(f"\n  💡 Pick the KP whose rise time and overshoot most closely")
        print(f"     match what MuJoCo <position kp=XX> produces in simulation.")

    print("\n  To disable torque:")
    try:
        io.disable_torque([servo_id])
    except Exception:
        pass
    print("  Done.")


if __name__ == "__main__":
    main()
