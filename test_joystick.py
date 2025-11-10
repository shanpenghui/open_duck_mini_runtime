import pygame
import time

def main():
    pygame.init()
    joystick = pygame.joystick.Joystick(0)
    joystick.init()

    print(f"✅ 检测到手柄：{joystick.get_name()}")
    print(f"🎮 可用轴数量：{joystick.get_numaxes()}")
    print(f"🎮 可用按钮数量：{joystick.get_numbuttons()}")
    print("🎯 请操作手柄，观察轴变化（Ctrl+C 退出）")

    try:
        while True:
            pygame.event.pump()  # 刷新事件队列

            axes = [joystick.get_axis(i) for i in range(joystick.get_numaxes())]
            axis_str = ", ".join([f"axis{i}={v:+.3f}" for i, v in enumerate(axes)])
            print(f"[RAW AXIS] {axis_str}")

            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\n✅ 测试结束")
    finally:
        pygame.quit()

if __name__ == "__main__":
    main()
