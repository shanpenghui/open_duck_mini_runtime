import pygame
from threading import Thread
from queue import Queue
import time
import numpy as np
from mini_bdx_runtime.buttons import Buttons

def apply_deadzone(val, threshold=0.25):
    return 0.0 if abs(val) < threshold else val

X_RANGE = [-0.15, 0.15]
Y_RANGE = [-0.2, 0.2]
YAW_RANGE = [-1.0, 1.0]

# rads
NECK_PITCH_RANGE = [-0.34, 1.1]
HEAD_PITCH_RANGE = [-0.78, 0.3]
HEAD_YAW_RANGE = [-0.5, 0.5]
HEAD_ROLL_RANGE = [-0.5, 0.5]


class XBoxController:
    def __init__(self, command_freq, only_head_control=False):
        self.command_freq = command_freq
        self.head_control_mode = only_head_control
        self.only_head_control = only_head_control

        self.last_commands = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        self.last_left_trigger = 0.0
        self.last_right_trigger = 0.0
        pygame.init()
        self.p1 = pygame.joystick.Joystick(0)
        self.p1.init()
        print(f"Loaded joystick with {self.p1.get_numaxes()} axes.")
        self.cmd_queue = Queue(maxsize=1)

        self.A_pressed = False
        self.B_pressed = False
        self.X_pressed = False
        self.Y_pressed = False
        self.LB_pressed = False
        self.RB_pressed = False

        self.buttons = Buttons()
        self.raw_buttons = ()

        Thread(target=self.commands_worker, daemon=True).start()

    def commands_worker(self):
        while True:
            self.cmd_queue.put(self.get_commands())
            time.sleep(1 / self.command_freq)

    def get_commands(self):
        last_commands = self.last_commands
        left_trigger = self.last_left_trigger
        right_trigger = self.last_right_trigger
        # if not hasattr(self, "_print_count"):
        #     self._print_count = 0
        # if self._print_count < 10:
        #     print("[DEBUG] Axis values:")
        #     for axis_id in range(self.p1.get_numaxes()):
        #         val = self.p1.get_axis(axis_id)
        #         print(f"    Axis {axis_id}: {val:.3f}")
        #     self._print_count += 1

        # ✅ 按照你的实际测试重新定义轴映射：
        # 左摇杆：axis0 左右（左负右正），axis1 前后（前负后正）
        # 右摇杆 X（Yaw 控制）：axis3 左负右正
        # LT（左扳机）：axis2，范围 -1（松）到 1（按下）
        # ⚠️ 右扳机未定义，这里设置为 0.0，等你补全
        # 打印全部6个轴的原始值，用于确认手柄各个控制轴
        # axis_values = [self.p1.get_axis(i) for i in range(self.p1.get_numaxes())]
        # print("[Joystick Axis Values]", ", ".join([f"A{i}: {v:+.3f}" for i, v in enumerate(axis_values)]))

        l_x = apply_deadzone(-1 * self.p1.get_axis(0))  # 左摇杆左右
        l_y = apply_deadzone(-1 * self.p1.get_axis(1))  # 左摇杆前后，前为正
        # Current Xbox Series controller maps right-stick horizontal to axis 2.
        r_x_raw = self.p1.get_axis(2)
        r_x = apply_deadzone(-1 * r_x_raw)

        # 打印 r_x 处理前后的值
        # print(f"[r_x] Raw: {r_x_raw:+.3f}, After Deadzone & Flip: {r_x:+.3f}")

        r_y = 0  #

        # 扳机处理
        # left_trigger = np.around((self.p1.get_axis(2) + 1) / 2, 3)
        # right_trigger = 0.0  # 当前无效，可后续更新

        if left_trigger < 0.1:
            left_trigger = 0
        if right_trigger < 0.1:
            right_trigger = 0

        if not self.head_control_mode:
            lin_vel_y = l_x
            lin_vel_x = l_y
            ang_vel = r_x
            if lin_vel_x >= 0:
                lin_vel_x *= np.abs(X_RANGE[1])
            else:
                lin_vel_x *= np.abs(X_RANGE[0])

            if lin_vel_y >= 0:
                lin_vel_y *= np.abs(Y_RANGE[1])
            else:
                lin_vel_y *= np.abs(Y_RANGE[0])

            if ang_vel >= 0:
                ang_vel *= np.abs(YAW_RANGE[1])
            else:
                ang_vel *= np.abs(YAW_RANGE[0])

            last_commands[0] = lin_vel_x
            last_commands[1] = lin_vel_y
            last_commands[2] = ang_vel
        else:
            last_commands[0] = 0.0
            last_commands[1] = 0.0
            last_commands[2] = 0.0
            last_commands[3] = 0.0  # neck pitch 0 for now

            head_yaw = l_x
            head_pitch = l_y
            head_roll = r_x

            if head_yaw >= 0:
                head_yaw *= np.abs(HEAD_YAW_RANGE[0])
            else:
                head_yaw *= np.abs(HEAD_YAW_RANGE[1])

            if head_pitch >= 0:
                head_pitch *= np.abs(HEAD_PITCH_RANGE[0])
            else:
                head_pitch *= np.abs(HEAD_PITCH_RANGE[1])

            if head_roll >= 0:
                head_roll *= np.abs(HEAD_ROLL_RANGE[0])
            else:
                head_roll *= np.abs(HEAD_ROLL_RANGE[1])

            last_commands[4] = head_pitch
            last_commands[5] = head_yaw
            last_commands[6] = head_roll

        for event in pygame.event.get():
            if event.type == pygame.JOYBUTTONDOWN:
                # print("[EVENT] JOYBUTTONDOWN received")
                if self.p1.get_button(0):  # A button
                    self.A_pressed = True

                if self.p1.get_button(1):  # B button
                    self.B_pressed = True

                if self.p1.get_button(3):  # X button
                    self.X_pressed = True

                if self.p1.get_button(4):  # Y button
                    self.Y_pressed = True
                    # print("[BUTTON] Y pressed")
                    if not self.only_head_control:
                        self.head_control_mode = not self.head_control_mode

                if self.p1.get_button(6):  # LB button
                    self.LB_pressed = True

                if self.p1.get_button(7):  # RB button
                    self.RB_pressed = True

            if event.type == pygame.JOYBUTTONUP:
                self.A_pressed = False
                self.B_pressed = False
                self.X_pressed = False
                self.Y_pressed = False
                self.LB_pressed = False
                self.RB_pressed = False

            # for i in range(10):
            #     if self.p1.get_button(i):
            #         print(f"Button {i} pressed")

        pygame.event.pump()  # process event queue
        self.raw_buttons = tuple(
            bool(self.p1.get_button(i)) for i in range(self.p1.get_numbuttons())
        )
        up_down = self.p1.get_hat(0)[1]

        return (
            np.around(last_commands, 3),
            self.A_pressed,
            self.B_pressed,
            self.X_pressed,
            self.Y_pressed,
            self.LB_pressed,
            self.RB_pressed,
            left_trigger,
            right_trigger,
            up_down,
        )

    def get_last_command(self):
        A_pressed = False
        B_pressed = False
        X_pressed = False
        Y_pressed = False
        LB_pressed = False
        RB_pressed = False
        up_down = 0
        try:
            (
                self.last_commands,
                A_pressed,
                B_pressed,
                X_pressed,
                Y_pressed,
                LB_pressed,
                RB_pressed,
                self.last_left_trigger,
                self.last_right_trigger,
                up_down,
            ) = self.cmd_queue.get(
                False
            )  # non blocking
        except Exception:
            pass

        self.buttons.update(
            A_pressed,
            B_pressed,
            X_pressed,
            Y_pressed,
            LB_pressed,
            RB_pressed,
            up_down == 1,
            up_down == -1,
            )

        # print(f"[INFO] Current mode: head_control_mode = {self.head_control_mode}")
        return (
            self.last_commands,
            self.buttons,
            self.last_left_trigger,
            self.last_right_trigger,
        )

    def get_raw_button(self, index):
        try:
            return bool(self.raw_buttons[int(index)])
        except (IndexError, TypeError, ValueError):
            return False

    def get_pressed_button_indexes(self):
        return [i for i, pressed in enumerate(self.raw_buttons) if pressed]

if __name__ == "__main__":
    controller = XBoxController(20)

    while True:
        print(controller.get_last_command())
        time.sleep(0.05)
