import select
import sys
import termios
import threading
import time
import tty

import numpy as np

from mini_bdx_runtime.buttons import Buttons
from mini_bdx_runtime.xbox_controller import (
    HEAD_PITCH_RANGE,
    HEAD_ROLL_RANGE,
    HEAD_YAW_RANGE,
    NECK_PITCH_RANGE,
    X_RANGE,
    Y_RANGE,
    YAW_RANGE,
)


class KeyboardController:
    def __init__(self, command_freq):
        self.command_freq = command_freq
        self.last_commands = np.zeros(7)
        self.buttons = Buttons()
        self.head_control_mode = False
        self._lock = threading.Lock()
        self._button_state = {
            "A": False,
            "B": False,
            "X": False,
            "Y": False,
            "LB": False,
            "RB": False,
            "dpad_up": False,
            "dpad_down": False,
        }
        self._stop = False
        self._old_termios = None

        if sys.stdin.isatty():
            self._old_termios = termios.tcgetattr(sys.stdin)
            tty.setcbreak(sys.stdin.fileno())
            threading.Thread(target=self._keyboard_worker, daemon=True).start()
            self._print_help()
        else:
            print("[KeyboardController] stdin is not a TTY; commands will stay zero.")

    def _print_help(self):
        print(
            "[KeyboardController] keys: "
            "W/S forward/back, A/D strafe, Q/E yaw, SPACE stop, "
            "H head mode, P pause, U/J phase +/-"
        )

    def _keyboard_worker(self):
        while not self._stop:
            readable, _, _ = select.select([sys.stdin], [], [], 1 / self.command_freq)
            if not readable:
                with self._lock:
                    self._clear_momentary_buttons()
                continue
            key = sys.stdin.read(1)
            self._handle_key(key)

    def _clear_momentary_buttons(self):
        for key in self._button_state:
            self._button_state[key] = False

    def _handle_key(self, key):
        key = key.lower()
        with self._lock:
            self._clear_commands_for_mode()

            if key == " ":
                self.last_commands[:] = 0.0
            elif key == "p":
                self._button_state["A"] = True
            elif key == "h":
                self.head_control_mode = not self.head_control_mode
                self._button_state["Y"] = True
                print(f"[KeyboardController] head_control_mode={self.head_control_mode}")
            elif key == "u":
                self._button_state["dpad_up"] = True
            elif key == "j":
                self._button_state["dpad_down"] = True
            elif key == "l":
                self._button_state["LB"] = True
            elif not self.head_control_mode:
                self._handle_walk_key(key)
            else:
                self._handle_head_key(key)

    def _clear_commands_for_mode(self):
        if self.head_control_mode:
            self.last_commands[:3] = 0.0
        else:
            self.last_commands[:3] = 0.0

    def _handle_walk_key(self, key):
        if key == "w":
            self.last_commands[0] = X_RANGE[1]
        elif key == "s":
            self.last_commands[0] = X_RANGE[0]
        elif key == "a":
            self.last_commands[1] = Y_RANGE[1]
        elif key == "d":
            self.last_commands[1] = Y_RANGE[0]
        elif key == "q":
            self.last_commands[2] = YAW_RANGE[1]
        elif key == "e":
            self.last_commands[2] = YAW_RANGE[0]

    def _handle_head_key(self, key):
        if key == "w":
            self.last_commands[4] = HEAD_PITCH_RANGE[1]
        elif key == "s":
            self.last_commands[4] = HEAD_PITCH_RANGE[0]
        elif key == "a":
            self.last_commands[5] = HEAD_YAW_RANGE[1]
        elif key == "d":
            self.last_commands[5] = HEAD_YAW_RANGE[0]
        elif key == "q":
            self.last_commands[6] = HEAD_ROLL_RANGE[1]
        elif key == "e":
            self.last_commands[6] = HEAD_ROLL_RANGE[0]
        elif key == "r":
            self.last_commands[3] = NECK_PITCH_RANGE[1]
        elif key == "f":
            self.last_commands[3] = NECK_PITCH_RANGE[0]

    def get_last_command(self):
        with self._lock:
            self.buttons.update(
                self._button_state["A"],
                self._button_state["B"],
                self._button_state["X"],
                self._button_state["Y"],
                self._button_state["LB"],
                self._button_state["RB"],
                self._button_state["dpad_up"],
                self._button_state["dpad_down"],
            )
            commands = np.around(self.last_commands.copy(), 3)
            self._clear_momentary_buttons()
            return commands, self.buttons, 0.0, 0.0

    def close(self):
        self._stop = True
        if self._old_termios is not None:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, self._old_termios)

    def __del__(self):
        self.close()


if __name__ == "__main__":
    controller = KeyboardController(20)
    try:
        while True:
            print(controller.get_last_command()[0])
            time.sleep(0.05)
    except KeyboardInterrupt:
        controller.close()
