import time

import gpiod
from gpiod.line import Direction, Value


PROJECTOR_GPIO = 262  # PI6，对应原树莓派 GPIO25 / 物理 Pin 22
GPIO_CHIP = "/dev/gpiochip0"


class Projector:
    def __init__(self):
        self.request = gpiod.request_lines(
            GPIO_CHIP,
            consumer="openduck-projector",
            config={
                PROJECTOR_GPIO: gpiod.LineSettings(
                    direction=Direction.OUTPUT,
                    output_value=Value.INACTIVE,
                ),
            },
        )

        self.on = False

    def switch(self):
        self.on = not self.on

        if self.on:
            self.request.set_value(PROJECTOR_GPIO, Value.ACTIVE)
        else:
            self.request.set_value(PROJECTOR_GPIO, Value.INACTIVE)

    def close(self):
        self.request.release()


if __name__ == "__main__":
    p = Projector()
    try:
        while True:
            p.switch()
            time.sleep(1)
    except KeyboardInterrupt:
        p.close()
