from pypot.feetech import FeetechSTS3215IO
import argparse
import time

DEFAULT_ID = 1  # A brand new motor should have id 1

joints = {
    "left_hip_yaw": 20,
    "left_hip_roll": 21,
    "left_hip_pitch": 22,
    "left_knee": 23,
    "left_ankle": 24,
    "neck_pitch": 30,
    "head_pitch": 31,
    "head_yaw": 32,
    "head_roll": 33,
    # "left_antenna": None,
    # "right_antenna": None,
    "right_hip_yaw": 10,
    "right_hip_roll": 11,
    "right_hip_pitch": 12,
    "right_knee": 13,
    "right_ankle": 14,
}

parser = argparse.ArgumentParser()
parser.add_argument(
    "--port",
    help="The port the motor is connected to. Default is /dev/ttyACM0. Use `ls /dev/tty* | grep usb` to find the port.",
    default="/dev/ttyACM0",
)
# parser.add_argument("--id", help="The id to set to the motor.", type=str, required=True)
args = parser.parse_args()
io = FeetechSTS3215IO(args.port)

current_id = DEFAULT_ID


def scan():
    id = None
    for i in range(255):

        print(f"scanning for id {i} ...")
        try:
            io.get_present_position([i])
            id = i
            print(f"Found motor with id {id}")
            break
        except Exception:
            pass
    return id

# print("current id: ", current_id)
for joint_name, joint_id in joints.items():
    current_id = joint_id
    try:
        io.get_present_position([current_id])
    except Exception:
        print(
            f"Could not find motor with default id ({current_id})"
        )
        exit()
    kp = io.get_P_coefficient([current_id])
    ki = io.get_I_coefficient([current_id])
    kd = io.get_D_coefficient([current_id])
    max_acceleration = io.get_maximum_acceleration([current_id])
    acceleration = io.get_acceleration([current_id])
    mode = io.get_mode([current_id])

    # print(f"PID : {kp}, {ki}, {kd}")
    # print(f"max_acceleration: {max_acceleration}")
    # print(f"acceleration: {acceleration}")
    # print(f"mode: {mode}")

    io.set_lock({current_id: 0})
    io.set_mode({current_id: 0})
    io.set_maximum_acceleration({current_id: 0})
    io.set_acceleration({current_id: 0})
    io.set_P_coefficient({current_id: 32})
    io.set_I_coefficient({current_id: 0})
    io.set_D_coefficient({current_id: 0})


    time.sleep(1)

    io.set_goal_position({current_id: 0})

    time.sleep(1)

    print("===")
    print("Done configuring motor.")
    print(f"Motor id: {current_id}")
    print(f"P coefficient : {io.get_P_coefficient([current_id])}")
    print(f"I coefficient : {io.get_I_coefficient([current_id])}")
    print(f"D coefficient : {io.get_D_coefficient([current_id])}")
    print(f"acceleration: {io.get_acceleration([current_id])}")
    print(f"max_acceleration: {io.get_maximum_acceleration([current_id])}")
    print(f"mode: {io.get_mode([current_id])}")
    print("===")
