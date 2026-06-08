import time
from threading import Thread

import gpiod
import numpy as np
from gpiod.line import Direction, Value


LEFT_EYE_GPIO = 228   # PH4，对应原树莓派 GPIO24 / 物理 Pin 18
RIGHT_EYE_GPIO = 270  # PI14，对应原树莓派 GPIO23 / 物理 Pin 16

GPIO_CHIP = "/dev/gpiochip0"


class Eyes:
    def __init__(self):
        self.request = gpiod.request_lines(
            GPIO_CHIP,
            consumer="openduck-eyes",
            config={
                LEFT_EYE_GPIO: gpiod.LineSettings(
                    direction=Direction.OUTPUT,
                    output_value=Value.ACTIVE,
                ),
                RIGHT_EYE_GPIO: gpiod.LineSettings(
                    direction=Direction.OUTPUT,
                    output_value=Value.ACTIVE,
                ),
            },
        )

        self.blink_duration = 0.1

        Thread(target=self.run, daemon=True).start()

    def run(self):
        while True:
            self.request.set_value(RIGHT_EYE_GPIO, Value.INACTIVE)
            self.request.set_value(LEFT_EYE_GPIO, Value.INACTIVE)
            time.sleep(self.blink_duration)

            self.request.set_value(RIGHT_EYE_GPIO, Value.ACTIVE)
            self.request.set_value(LEFT_EYE_GPIO, Value.ACTIVE)

            next_blink = np.random.rand() * 4
            time.sleep(next_blink)

    def close(self):
        self.request.release()


if __name__ == "__main__":
    e = Eyes()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        e.close()
