#!/usr/bin/env python3
import json
import smbus2

BNO055_ADDRESS = 0x28
OFFSET_REG_START = 0x55
OFFSET_FILE = "bno055_offset.json"

# 以寄存器顺序排列 offset 字段
offset_keys = [
    "accel_offset_x", "accel_offset_y", "accel_offset_z",
    "mag_offset_x", "mag_offset_y", "mag_offset_z",
    "gyro_offset_x", "gyro_offset_y", "gyro_offset_z",
    "accel_radius", "mag_radius"
]

# 将整型值转为寄存器字节序（小端）
def s16_to_bytes(val):
    return list(val.to_bytes(2, byteorder='little', signed=True))

# 加载 offset
with open(OFFSET_FILE, "r") as f:
    offset_dict = json.load(f)

# 构造完整字节流
offset_bytes = []
for key in offset_keys:
    offset_bytes += s16_to_bytes(offset_dict[key])

# 写入寄存器
bus = smbus2.SMBus(1)
for i, byte in enumerate(offset_bytes):
    bus.write_byte_data(BNO055_ADDRESS, OFFSET_REG_START + i, byte)

print("✅ 已将 offset 写入 BNO055 RAM 寄存器")
print("💡 注意：断电后 offset 会丢失，如需持久请每次上电写入")

