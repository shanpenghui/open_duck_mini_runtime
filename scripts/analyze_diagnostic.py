#!/usr/bin/env python3
"""
Analyze diagnostic recording data and generate sim2real gap report.

Usage:
  python scripts/analyze_diagnostic.py diagnostic_data.pkl

Outputs:
  - Console summary with key sim2real gap metrics
  - PNG plots saved alongside the pkl file
"""

import os
import sys
import pickle

import numpy as np


def load_data(path):
    with open(path, "rb") as f:
        return pickle.load(f)


def analyze(data, pkl_path):
    print("=" * 70)
    print("  SIM2REAL DIAGNOSTIC ANALYSIS")
    print("=" * 70)

    meta = data.get("metadata", {})
    print(f"\nModel:    {meta.get('onnx_model', '?')}")
    print(f"Scale:    {meta.get('action_scale', '?')}")
    print(f"PID:      {meta.get('pid', '?')}")
    print(f"Command:  vx={meta.get('command_vel_x', '?')}, "
          f"vy={meta.get('command_vel_y', '?')}, "
          f"yaw={meta.get('command_yaw', '?')}")

    times = data["times"]
    N = len(times)
    duration = times[-1] if N > 0 else 0
    print(f"Duration: {duration:.2f}s ({N} frames)")

    jnames = data.get("joint_names_ordered", [])
    leg_idx = [0, 1, 2, 3, 4, 9, 10, 11, 12, 13]

    # ── 1. Timing ─────────────────────────────────────────────────
    print(f"\n{'─'*70}")
    print("  1. CONTROL LOOP TIMING")
    print(f"{'─'*70}")
    loop = data["loop_time_ms"]
    budget = 1000.0 / meta.get("control_freq", 50)
    overshoot = np.sum(loop > budget)
    print(f"  Budget:     {budget:.1f} ms")
    print(f"  Mean loop:  {np.mean(loop):.1f} ms")
    print(f"  P99 loop:   {np.percentile(loop, 99):.1f} ms")
    print(f"  Max loop:   {np.max(loop):.1f} ms")
    print(f"  Overbudget: {overshoot}/{N} ({100*overshoot/N:.1f}%)")
    if overshoot > N * 0.05:
        print("  ⚠️  >5% frames over budget! Control freq may not be sustainable.")

    # ── 2. Position Tracking ──────────────────────────────────────
    print(f"\n{'─'*70}")
    print("  2. JOINT POSITION TRACKING (commanded vs actual)")
    print(f"{'─'*70}")
    pos_actual = data["joint_pos_actual"]
    pos_cmd = data["motor_targets"]
    tracking_err_deg = np.degrees(pos_actual - pos_cmd)

    print(f"  Mean |error|:  {np.mean(np.abs(tracking_err_deg)):.2f}°")
    print(f"  P95 |error|:   {np.percentile(np.abs(tracking_err_deg), 95):.2f}°")
    print(f"  Max |error|:   {np.max(np.abs(tracking_err_deg)):.2f}°")

    print(f"\n  Per-joint (leg only):")
    print(f"  {'Joint':<22s} {'Mean':>6s} {'P95':>6s} {'Max':>6s} {'Lag?':>6s}")
    print(f"  {'─'*50}")
    for idx in leg_idx:
        name = jnames[idx] if idx < len(jnames) else f"joint_{idx}"
        errs = np.abs(tracking_err_deg[:, idx])
        mean_e = np.mean(errs)
        p95_e = np.percentile(errs, 95)
        max_e = np.max(errs)
        lag_flag = "⚠️" if mean_e > 5.0 else "✓"
        print(f"  {name:<22s} {mean_e:6.2f} {p95_e:6.2f} {max_e:6.2f} {lag_flag:>6s}")

    large_err = np.sum(np.abs(tracking_err_deg) > 10)
    if large_err > N * 0.01:
        print(f"  ⚠️  {large_err} samples with >10° tracking error — servos may be saturating")

    # ── 3. Torque/Current ─────────────────────────────────────────
    print(f"\n{'─'*70}")
    print("  3. JOINT CURRENTS (torque proxy)")
    print(f"{'─'*70}")
    currents = data.get("joint_current_actual", np.array([]))
    if len(currents.shape) == 2:
        abs_cur = np.abs(currents)
        print(f"  Mean |current|: {np.mean(abs_cur):.3f} A")
        print(f"  Max  |current|: {np.max(abs_cur):.3f} A")

        print(f"\n  Per-joint peak (leg only):")
        print(f"  {'Joint':<22s} {'Mean':>6s} {'P95':>6s} {'Peak':>6s}")
        print(f"  {'─'*46}")
        for idx in leg_idx:
            name = jnames[idx] if idx < len(jnames) else f"joint_{idx}"
            print(f"  {name:<22s} {np.mean(abs_cur[:, idx]):6.3f} "
                  f"{np.percentile(abs_cur[:, idx], 95):6.3f} "
                  f"{np.max(abs_cur[:, idx]):6.3f}")

        # Check for saturation: if current hits a ceiling, torque is maxed
        # ST3215 stall current ~1.5A typical
        saturation = np.sum(abs_cur > 1.2)
        if saturation > N * 0.01:
            print(f"  ⚠️  {saturation} samples near current limit (>1.2A)")
    else:
        print("  (no current data)")

    # ── 4. Velocity ───────────────────────────────────────────────
    print(f"\n{'─'*70}")
    print("  4. JOINT VELOCITIES")
    print(f"{'─'*70}")
    vels = data["joint_vel_actual"]
    if len(vels.shape) == 2:
        print(f"  Max |vel|: {np.max(np.abs(vels)):.2f} rad/s")
        print(f"  Mean |vel|: {np.mean(np.abs(vels)):.2f} rad/s")
        # ST3215 no-load ~4.7 rad/s. Under load should be well below.
        vel_limit = 4.7
        near_limit = np.sum(np.abs(vels) > vel_limit * 0.8)
        if near_limit > 0:
            print(f"  ⚠️  {near_limit} samples near velocity limit (>{vel_limit*0.8:.1f} rad/s)")

        print(f"\n  Per-joint peak |vel| (leg only):")
        for idx in leg_idx:
            name = jnames[idx] if idx < len(jnames) else f"joint_{idx}"
            print(f"  {name:<22s} {np.max(np.abs(vels[:, idx])):.2f} rad/s")
    else:
        print("  (no velocity data)")

    # ── 5. Action Analysis ────────────────────────────────────────
    print(f"\n{'─'*70}")
    print("  5. POLICY ACTION ANALYSIS")
    print(f"{'─'*70}")
    actions = data["action_raw"]
    if len(actions.shape) == 2:
        abs_act = np.abs(actions)
        print(f"  Mean |action|:       {np.mean(abs_act):.4f}")
        print(f"  Max  |action|:       {np.max(abs_act):.4f}")
        print(f"  Action saturation:   {np.sum(abs_act > 0.95)}/{N*14} "
              f"({100*np.sum(abs_act > 0.95)/(N*14):.2f}%)")

        # Action jitter: how much does action change between steps
        if N > 1:
            action_diff = np.diff(actions, axis=0)
            print(f"  Action rate (Δ/step):")
            print(f"    Mean |Δaction|: {np.mean(np.abs(action_diff)):.4f}")
            print(f"    Max  |Δaction|: {np.max(np.abs(action_diff)):.4f}")
    else:
        print("  (no action data)")

    # ── 6. IMU Stability ──────────────────────────────────────────
    print(f"\n{'─'*70}")
    print("  6. IMU STABILITY")
    print(f"{'─'*70}")
    gyro = data["imu_gyro"]
    accel = data["imu_accel"]
    if len(gyro.shape) == 2:
        print(f"  Gyro (rad/s):")
        print(f"    Mean: [{np.mean(gyro, axis=0)[0]:.3f}, "
              f"{np.mean(gyro, axis=0)[1]:.3f}, {np.mean(gyro, axis=0)[2]:.3f}]")
        print(f"    Std:  [{np.std(gyro, axis=0)[0]:.3f}, "
              f"{np.std(gyro, axis=0)[1]:.3f}, {np.std(gyro, axis=0)[2]:.3f}]")
    if len(accel.shape) == 2:
        # For a stable walk, vertical accel should be ~9.8, horizontal ~0
        print(f"  Accel (m/s²):")
        print(f"    Mean: [{np.mean(accel, axis=0)[0]:.3f}, "
              f"{np.mean(accel, axis=0)[1]:.3f}, {np.mean(accel, axis=0)[2]:.3f}]")
        print(f"    Std:  [{np.std(accel, axis=0)[0]:.3f}, "
              f"{np.std(accel, axis=0)[1]:.3f}, {np.std(accel, axis=0)[2]:.3f}]")

    # ── 7. Sim2Real Gap Key Indicators ────────────────────────────
    print(f"\n{'═'*70}")
    print("  SIM2REAL GAP DIAGNOSIS")
    print(f"{'═'*70}")

    issues = []

    # Check 1: Position tracking
    mean_track_err = np.mean(np.abs(tracking_err_deg[:, leg_idx]))
    if mean_track_err > 8:
        issues.append(("CRITICAL", f"Large position tracking error ({mean_track_err:.1f}° avg) → "
                                    "sim kp may be too high vs real servo stiffness"))
    elif mean_track_err > 4:
        issues.append(("WARNING", f"Moderate tracking error ({mean_track_err:.1f}° avg) → "
                                   "kp mismatch between sim and real"))

    # Check 2: Current saturation
    if len(currents.shape) == 2:
        max_cur = np.max(np.abs(currents[:, leg_idx]))
        if max_cur > 1.2:
            issues.append(("WARNING", f"Current near limit ({max_cur:.2f}A peak) → "
                                      "servo may be torque-saturated under load"))

    # Check 3: Velocity saturation
    if len(vels.shape) == 2:
        max_vel = np.max(np.abs(vels[:, leg_idx]))
        if max_vel > 3.5:
            issues.append(("WARNING", f"Velocity near limit ({max_vel:.1f} rad/s) → "
                                       "servo speed may be limiting gait"))

    # Check 4: Action saturation
    if len(actions.shape) == 2:
        sat_frac = np.sum(np.abs(actions) > 0.95) / (N * 14)
        if sat_frac > 0.05:
            issues.append(("CRITICAL", f"Action saturation ({sat_frac*100:.1f}%) → "
                                        "policy output is clipping, can't control precisely"))

    # Check 5: Loop timing
    overbudget_frac = overshoot / N if N > 0 else 0
    if overbudget_frac > 0.1:
        issues.append(("CRITICAL", f"Control loop unstable ({overbudget_frac*100:.1f}% over budget) → "
                                    "policy is getting stale observations"))

    if not issues:
        print("  ✓ No obvious sim2real gap issues detected!")
        print("  Robot tracking looks healthy.")
    else:
        for severity, msg in issues:
            print(f"  [{severity}] {msg}")

    # ── Recommendations ───────────────────────────────────────────
    print(f"\n{'─'*70}")
    print("  RECOMMENDED NEXT STEPS")
    print(f"{'─'*70}")

    if mean_track_err > 5:
        print("  1. CRITICAL: Run single-servo step response test")
        print("     Use record_data.py to measure real servo response")
        print("     Compare kp/kv/damping against XML values")
        print("     → This is your #1 priority")
    if len(currents.shape) == 2 and np.max(np.abs(currents[:, leg_idx])) > 1.0:
        print("  2. Check if forcerange in XML matches real torque limit")
        print(f"     Real peak ~{np.max(np.abs(currents[:, leg_idx])):.2f}A "
              f"≈ {np.max(np.abs(currents[:, leg_idx])) * 12 * 0.7:.2f}Nm (rough)")
        print("     XML has forcerange=±3.23 Nm")
    if len(vels.shape) == 2 and np.max(np.abs(vels[:, leg_idx])) > 3.0:
        print("  3. Velocity limit may be too restrictive in training")
        print("     Consider increasing max_motor_velocity in sim")

    print(f"\n{'═'*70}")
    print("  Data saved. Use this for detailed plotting:")
    print(f"  data = pickle.load(open('{pkl_path}', 'rb'))")
    print(f"{'═'*70}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python analyze_diagnostic.py <diagnostic_data.pkl>")
        sys.exit(1)

    pkl_path = sys.argv[1]
    data = load_data(pkl_path)
    analyze(data, pkl_path)
