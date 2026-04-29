import argparse
import time

import rustypot

JOINTS = {
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

MOTOR_P_VALUES = {
    10: 33,
    11: 33,
    12: 33,
    13: 33,
    14: 33,
    20: 33,
    21: 33,
    22: 33,
    23: 33,
    24: 33,
    30: 20,
    31: 20,
    32: 18,
    33: 18,
}


def configure_motor(io, servo_id, kp, ki, kd, acceleration, maximum_acceleration, goal_position):
    io.read_present_position(servo_id)
    io.write_lock(servo_id, False)
    io.write_mode(servo_id, 0)
    io.write_maximum_acceleration(servo_id, maximum_acceleration)
    io.write_acceleration(servo_id, acceleration)
    io.write_p_coefficient(servo_id, kp)
    io.write_i_coefficient(servo_id, ki)
    io.write_d_coefficient(servo_id, kd)
    io.write_goal_position(servo_id, goal_position)
    time.sleep(0.05)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="/dev/ttyACM0")
    parser.add_argument("--ki", type=int, default=0)
    parser.add_argument("--kd", type=int, default=0)
    parser.add_argument("--acceleration", type=int, default=0)
    parser.add_argument("--maximum_acceleration", type=int, default=0)
    parser.add_argument("--goal_position", type=float, default=0.0, help="Radians.")
    args = parser.parse_args()

    io = rustypot.Sts3215PyController(args.port, 1000000, 0.05)

    for joint_name, servo_id in JOINTS.items():
        kp = MOTOR_P_VALUES[servo_id]
        print(f"Configuring {joint_name} ({servo_id}) with P={kp}")
        configure_motor(
            io,
            servo_id,
            kp,
            args.ki,
            args.kd,
            args.acceleration,
            args.maximum_acceleration,
            args.goal_position,
        )
        print(
            f"  P={io.read_p_coefficient(servo_id)} "
            f"I={io.read_i_coefficient(servo_id)} "
            f"D={io.read_d_coefficient(servo_id)} "
            f"acc={io.read_acceleration(servo_id)} "
            f"max_acc={io.read_maximum_acceleration(servo_id)} "
            f"mode={io.read_mode(servo_id)}"
        )

    print("Done configuring all motors.")


if __name__ == "__main__":
    main()
