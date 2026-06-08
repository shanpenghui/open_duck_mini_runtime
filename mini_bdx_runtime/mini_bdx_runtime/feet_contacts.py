import time

import gpiod
import numpy as np
from gpiod.line import Bias, Direction, Value


LEFT_FOOT_GPIO = 227   # PI5，对应原树莓派 GPIO22 / 物理 Pin 15
RIGHT_FOOT_GPIO = 261  # PH3，对应原树莓派 GPIO27 / 物理 Pin 13

GPIO_CHIP = "/dev/gpiochip0"


class FeetContacts:
    def __init__(self):
        self.request = gpiod.request_lines(
            GPIO_CHIP,
            consumer="openduck-feet-contacts",
            config={
                LEFT_FOOT_GPIO: gpiod.LineSettings(
                    direction=Direction.INPUT,
                    bias=Bias.PULL_UP,
                ),
                RIGHT_FOOT_GPIO: gpiod.LineSettings(
                    direction=Direction.INPUT,
                    bias=Bias.PULL_UP,
                ),
            },
        )

    def get(self):
        left = self.request.get_value(LEFT_FOOT_GPIO) == Value.INACTIVE
        right = self.request.get_value(RIGHT_FOOT_GPIO) == Value.INACTIVE
        return np.array([left, right])

    def close(self):
        self.request.release()


if __name__ == "__main__":
    feet_contacts = FeetContacts()
    try:
        while True:
            print(feet_contacts.get())
            time.sleep(0.05)
    except KeyboardInterrupt:
        feet_contacts.close()
