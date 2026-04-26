import argparse
import time

import rustypot


DEFAULT_ID = 1


def scan(io):
    for servo_id in range(255):
        print(f"scanning for id {servo_id} ...")
        try:
            io.read_present_position(servo_id)
            print(f"Found motor with id {servo_id}")
            return servo_id
        except Exception:
            pass
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="/dev/ttyACM0")
    parser.add_argument("--id", type=int, default=None, help="Servo id to configure.")
    parser.add_argument("--new_id", type=int, default=None, help="Optional new servo id.")
    parser.add_argument("--kp", type=int, default=32)
    parser.add_argument("--ki", type=int, default=0)
    parser.add_argument("--kd", type=int, default=0)
    parser.add_argument("--acceleration", type=int, default=0)
    parser.add_argument("--maximum_acceleration", type=int, default=0)
    parser.add_argument("--goal_position", type=float, default=0.0, help="Radians.")
    args = parser.parse_args()

    io = rustypot.Sts3215PyController(args.port, 1000000, 0.05)
    servo_id = args.id if args.id is not None else DEFAULT_ID

    try:
        io.read_present_position(servo_id)
    except Exception:
        print(f"Could not find motor with id {servo_id}. Scanning for motor ...")
        servo_id = scan(io)
        if servo_id is None:
            raise RuntimeError("Could not find any motor on the bus")

    print(f"Configuring motor {servo_id}")
    io.write_lock(servo_id, 0)
    io.write_mode(servo_id, 0)
    io.write_maximum_acceleration(servo_id, args.maximum_acceleration)
    io.write_acceleration(servo_id, args.acceleration)
    io.write_p_coefficient(servo_id, args.kp)
    io.write_i_coefficient(servo_id, args.ki)
    io.write_d_coefficient(servo_id, args.kd)

    if args.new_id is not None and args.new_id != servo_id:
        print(f"Changing id {servo_id} -> {args.new_id}")
        io.write_id(servo_id, args.new_id)
        servo_id = args.new_id
        time.sleep(0.5)

    io.write_goal_position(servo_id, args.goal_position)
    time.sleep(0.5)

    print("===")
    print("Done configuring motor.")
    print(f"Motor id: {servo_id}")
    print(f"P coefficient: {io.read_p_coefficient(servo_id)}")
    print(f"I coefficient: {io.read_i_coefficient(servo_id)}")
    print(f"D coefficient: {io.read_d_coefficient(servo_id)}")
    print(f"acceleration: {io.read_acceleration(servo_id)}")
    print(f"max_acceleration: {io.read_maximum_acceleration(servo_id)}")
    print(f"mode: {io.read_mode(servo_id)}")
    print("===")


if __name__ == "__main__":
    main()
