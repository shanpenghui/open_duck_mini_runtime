import numpy as np
import time

try:
    import RPi.GPIO as GPIO
    HAS_RPI_GPIO = True
except ModuleNotFoundError:
    GPIO = None
    HAS_RPI_GPIO = False


LEFT_ANTENNA_PIN = 13
RIGHT_ANTENNA_PIN = 12
LEFT_SIGN = 1
RIGHT_SIGN = -1


class Antennas:
    def __init__(self):
        self.enabled = HAS_RPI_GPIO

        if not self.enabled:
            print("[Antennas] RPi.GPIO not found. Antennas disabled on Orange Pi for now.")
            self.pwm1 = None
            self.pwm2 = None
            return

        GPIO.setmode(GPIO.BCM)
        GPIO.setup(LEFT_ANTENNA_PIN, GPIO.OUT)
        GPIO.setup(RIGHT_ANTENNA_PIN, GPIO.OUT)

        self.pwm1 = GPIO.PWM(LEFT_ANTENNA_PIN, 50)
        self.pwm2 = GPIO.PWM(RIGHT_ANTENNA_PIN, 50)

        self.pwm1.start(0)
        self.pwm2.start(0)

    def map_input_to_angle(self, value):
        return 90 + (value * 90)

    def set_position_left(self, position):
        self.set_position(1, position, LEFT_SIGN)

    def set_position_right(self, position):
        self.set_position(2, position, RIGHT_SIGN)

    def set_position(self, servo, value, sign=1):
        if not self.enabled:
            return

        if -1 <= value <= 1:
            angle = self.map_input_to_angle(value * sign)
            duty = 2 + (angle / 18)

            if servo == 1:
                self.pwm1.ChangeDutyCycle(duty)
            elif servo == 2:
                self.pwm2.ChangeDutyCycle(duty)
            else:
                print("Invalid servo number!")
        else:
            print("Invalid input! Enter a value between -1 and 1.")

    def stop(self):
        if not self.enabled:
            return

        self.pwm1.stop()
        self.pwm2.stop()
        GPIO.cleanup()


if __name__ == "__main__":
    antennas = Antennas()

    s = time.time()
    while True:
        antennas.set_position_left(np.sin(2 * np.pi * 1 * time.time()))
        antennas.set_position_right(np.sin(2 * np.pi * 1 * time.time()))

        time.sleep(1 / 50)

        if time.time() - s > 5:
            break
