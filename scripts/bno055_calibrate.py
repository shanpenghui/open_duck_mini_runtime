import time
import json
import statistics
import smbus2
import board
import busio
import adafruit_bno055

# I2C地址与寄存器
BNO055_ADDRESS = 0x28
OFFSET_REG_START = 0x55
REQUIRED_SAMPLES = 50
OFFSET_FILE = "bno055_offset.json"

# offset字段
offset_keys = [
    "accel_offset_x", "accel_offset_y", "accel_offset_z",
    "mag_offset_x", "mag_offset_y", "mag_offset_z",
    "gyro_offset_x", "gyro_offset_y", "gyro_offset_z",
    "accel_radius", "mag_radius"
]
offset_buffer = {k: [] for k in offset_keys}

# 初始化 Adafruit 与 SMBus（共用 I2C）
i2c = busio.I2C(board.SCL, board.SDA)
sensor = adafruit_bno055.BNO055_I2C(i2c)
bus = smbus2.SMBus(1)

# 原始寄存器读取
def read_offset_from_register():
    data = bus.read_i2c_block_data(BNO055_ADDRESS, OFFSET_REG_START, 22)
    def s16(lsb, msb): return int.from_bytes([lsb, msb], byteorder='little', signed=True)
    values = [
        s16(data[0], data[1]), s16(data[2], data[3]), s16(data[4], data[5]),
        s16(data[6], data[7]), s16(data[8], data[9]), s16(data[10], data[11]),
        s16(data[12], data[13]), s16(data[14], data[15]), s16(data[16], data[17]),
        s16(data[18], data[19]), s16(data[20], data[21]),
    ]
    return dict(zip(offset_keys, values))

print("📡 正在实时监控校准状态并采样 offset（目标：50 次 System=3）...")

try:
    while True:
        sys, gyro, accel, mag = sensor.calibration_status
        euler = sensor.euler

        if euler is not None:
            print(f"🧭 Euler角：{euler} ｜ System={sys} Gyro={gyro} Accel={accel} Mag={mag}")
        else:
            print(f"⚠️ 姿态角无效 ｜ System={sys} Gyro={gyro} Accel={accel} Mag={mag}")

        if sys == 3 and gyro == 3 and accel == 3 and mag == 3:
            try:
                offsets = read_offset_from_register()
                for k in offset_keys:
                    offset_buffer[k].append(offsets[k])
                print(f"✅ 已记录 {len(offset_buffer[offset_keys[0]])}/{REQUIRED_SAMPLES}")
            except Exception as e:
                print(f"❌ 读取寄存器失败: {e}")

        if len(offset_buffer[offset_keys[0]]) >= REQUIRED_SAMPLES:
            print("🎯 收集完成，正在计算均值...")
            avg_offset = {k: int(round(statistics.mean(offset_buffer[k]))) for k in offset_keys}
            with open(OFFSET_FILE, "w") as f:
                json.dump(avg_offset, f, indent=4)
            print(f"✅ 已保存平均 offset 到 {OFFSET_FILE}")
            break

        time.sleep(0.2)

except KeyboardInterrupt:
    print("👋 已手动中断，未保存")

