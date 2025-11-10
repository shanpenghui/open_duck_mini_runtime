import pygame
import time

pygame.init()
pygame.joystick.init()

if pygame.joystick.get_count() == 0:
    print("❌ 没检测到任何手柄，请确认已连接")
    exit(1)

joystick = pygame.joystick.Joystick(0)
joystick.init()

print(f"✅ 已检测到手柄: {joystick.get_name()}")
print(f"总轴数: {joystick.get_numaxes()}")

try:
    while True:
        pygame.event.pump()
        axis_values = [round(joystick.get_axis(i), 3) for i in range(joystick.get_numaxes())]
        print("轴值 (axis):", axis_values)
        time.sleep(0.2)
except KeyboardInterrupt:
    print("\n退出测试")
    pygame.quit()
