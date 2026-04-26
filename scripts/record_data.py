import argparse
import pickle
import time

import rustypot


def convert_load(raw_load):
    sign = -1
    if raw_load > 1023:
        raw_load -= 1024
        sign = 1
    return sign * raw_load * 0.001


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="/dev/ttyACM0")
    parser.add_argument("--id", type=int, default=1)
    parser.add_argument("--kp", type=int, default=32)
    parser.add_argument("--kd", type=int, default=0)
    parser.add_argument("--acceleration", type=int, default=0)
    parser.add_argument("--goal_position", type=float, default=1.57, help="Radians.")
    parser.add_argument("--duration", type=float, default=3.0)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    io = rustypot.Sts3215PyController(args.port, 1000000, 0.05)
    print(f"acceleration: {args.acceleration}, kp: {args.kp}, kd: {args.kd}")

    io.write_acceleration(args.id, args.acceleration)
    io.write_p_coefficient(args.id, args.kp)
    io.write_d_coefficient(args.id, args.kd)
    io.write_goal_position(args.id, 0.0)
    time.sleep(3)

    times = []
    positions = []
    goal_positions = []
    speeds = []
    loads = []
    currents = []

    io.write_goal_position(args.id, args.goal_position)
    started_at = time.time()
    while True:
        t = time.time() - started_at
        io.write_goal_position(args.id, args.goal_position)
        times.append(t)
        positions.append(io.read_present_position(args.id))
        goal_positions.append(args.goal_position)
        speeds.append(io.read_present_speed(args.id))
        loads.append(convert_load(io.read_present_load(args.id)))
        currents.append(io.read_present_current(args.id))

        if t > args.duration:
            break

        time.sleep(0.01)

    data = {
        "acceleration": args.acceleration,
        "kp": args.kp,
        "kd": args.kd,
        "times": times,
        "positions": positions,
        "goal_positions": goal_positions,
        "speeds": speeds,
        "loads": loads,
        "currents": currents,
    }

    output = args.output or (
        f"data_acceleration_{args.acceleration}_kp_{args.kp}_kd_{args.kd}.pkl"
    )
    with open(output, "wb") as f:
        pickle.dump(data, f)
    print(f"Wrote {output}")


if __name__ == "__main__":
    main()
