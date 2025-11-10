import pygame
import time

# 初始化 pygame 和 joystick
pygame.init()
pygame.joystick.init()

# 检查是否连接手柄
if pygame.joystick.get_count() == 0:
    print("未检测到手柄，请检查连接。")
    exit()

# 获取手柄对象
joystick = pygame.joystick.Joystick(0)
joystick.init()
print(f"检测到手柄: {joystick.get_name()}")

# 获取手柄信息
num_axes = joystick.get_numaxes()
num_buttons = joystick.get_numbuttons()
num_hats = joystick.get_numhats()

print(f"轴数量: {num_axes}")
print(f"按钮数量: {num_buttons}")
print(f"Hats数量: {num_hats}")

try:
    while True:
        pygame.event.pump()

        print("\n==== AXES ====")
        for i in range(num_axes):
            val = joystick.get_axis(i)
            print(f"Axis {i}: {val:.3f}")

        print("==== BUTTONS ====")
        for i in range(num_buttons):
            val = joystick.get_button(i)
            print(f"Button {i}: {val}")

        print("==== HAT ====")
        for i in range(num_hats):
            val = joystick.get_hat(i)
            print(f"Hat {i}: {val}")

        time.sleep(0.2)

except KeyboardInterrupt:
    print("退出调试")
    pygame.quit()
