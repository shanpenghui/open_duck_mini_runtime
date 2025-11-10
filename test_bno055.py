import time
import board
import busio
import adafruit_bno055

# 初始化 I2C 接口
i2c = busio.I2C(board.SCL, board.SDA)

# 初始化 BNO055 传感器
sensor = adafruit_bno055.BNO055_I2C(i2c, address=0x28)

# 等待传感器初始化
time.sleep(1)

while True:
    print("Euler角度 (heading, roll, pitch):", sensor.euler)
    print("加速度 (m/s^2):", sensor.acceleration)
    print("角速度 (rad/s):", sensor.gyro)
    print("磁力计 (uT):", sensor.magnetic)
    print("温度 (℃):", sensor.temperature)
    print()
    time.sleep(1)
