# OpenDuck D435 导航和定位第一版规划

## 目标

第一版先让 OpenDuck 学会“看距离、记日志、离线分析”，不让机器人自己跑。

链路是：

```text
RealSense D435 RGB/Depth
-> 保存彩色图、深度图、相机内参
-> 计算前方/左侧/右侧最近障碍距离
-> 输出 JSON
-> 后续再接建图、定位、导航 dry-run
```

第一版明确不做：

- 不控制机器人。
- 不发送 `joystick_velocity`、前进、转向、横移、速度控制等命令。
- 不修改 `/ws` 主链路。
- 不修改 Android APP 手柄控制。
- 不在欠压状态下长时间跑 D435 和推理。

## 当前风险

已经看到过：

```text
vcgencmd get_throttled -> throttled=0x50005
dmesg -> Undervoltage detected!
```

大白话解释：树莓派曾经供电不稳。D435 出流、ONNX 推理、CPU 拉高都会让电流变大；供电不稳时，轻则 SSH 掉线，重则直接关机。

继续导航测试前建议：

1. 树莓派 5 使用官方 27W / 5.1V 5A USB-C PD 电源。
2. D435 接独立供电 USB3 Hub。
3. 测试时先拔掉鼠标、USB 串口、其他不必要 USB 设备。
4. 用短一点、质量好的 USB3 数据线。

检查命令：

```bash
vcgencmd get_throttled
dmesg -T | grep -i -E "voltage|under|thrott|power" | tail -20
```

如果还有新的 `Undervoltage detected!`，先处理供电，不继续跑导航。

## 树莓派健康检查

新增工具：

```text
server/pi_health_check_tool.py
```

这个工具只做“体检”，不会控制机器人，不会发送 `/ws` 命令，不会改 Android APP 手柄控制，也不会让视觉链路接管机器人。

为什么要先检查供电：

树莓派 5、RealSense D435 和 ONNX 推理一起跑时，电流会明显变大。如果供电不稳，树莓派可能会欠压、降频、USB 掉线，D435 也可能突然断流。之前已经看到过：

```text
throttled=0x50005
Undervoltage detected!
```

所以每次跑 D435、YOLO、感知融合或导航 dry-run 前，建议先做一次健康检查。这样可以先判断“电源、温度、磁盘、内存、USB、D435、Python 依赖、OpenDuck 工具”是不是大体正常，避免一上来就查复杂代码。

### 查看健康检查工具状态

在树莓派上进入项目目录：

```bash
cd /home/duck/apps/voice-assistant
```

运行：

```bash
./.venv/bin/python -m server.pi_health_check_tool status
```

这个命令只说明工具本身可用，不会去重扫硬件。

如果想在终端里看更清楚的文本版，运行：

```bash
./.venv/bin/python -m server.pi_health_check_tool status --format text
```

### 执行完整健康检查

运行：

```bash
./.venv/bin/python -m server.pi_health_check_tool check
```

如果是在 SSH 终端里人工查看，推荐运行文本版：

```bash
./.venv/bin/python -m server.pi_health_check_tool check --format text
```

它会只读检查：

- `vcgencmd get_throttled`：看有没有欠压或限频风险。
- `vcgencmd measure_temp`：看 CPU 温度。
- `df -h /`：看根分区磁盘空间。
- `free -h`：看内存。
- `lsusb` 和 `v4l2-ctl --list-devices`：看 USB 和 D435。
- `dmesg` 相关日志：看最近有没有欠压、USB 断开、USB reset。
- 当前 Python 解释器能不能 import `cv2`、`numpy`、`onnxruntime`。
- OpenDuck 现有状态函数能不能 import 并调用。

这些检查都不会控制机器人。命令不存在、权限不足或返回非 0 时，也只会写进 JSON，不会让程序崩溃。

### 保存 JSON 结果

如果要把结果保存成文件，运行：

```bash
./.venv/bin/python -m server.pi_health_check_tool check --output /tmp/openduck_health.json
```

建议把 `/tmp/openduck_health.json` 发回 Windows 保存，方便后续排查。

也可以一边在终端看文本版，一边保存 JSON：

```bash
./.venv/bin/python -m server.pi_health_check_tool check --format text --output /tmp/openduck_health.json
```

### 复制健康检查结果回 Windows

在 Windows PowerShell 里运行：

```powershell
scp duck@10.64.16.56:/tmp/openduck_health.json C:\Users\wx151\Downloads\openduck_health.json
```

如果树莓派 IP 变了，把 `10.64.16.56` 换成当前树莓派 IP。

### 如何判断 overall_status

打开 JSON 后，优先看：

```text
overall_status
```

判断方式：

- `ok`：整体正常，可以继续做 D435、YOLO、感知融合或导航 dry-run。
- `warning`：程序能跑，但有风险。例如 Windows 本地没有树莓派硬件命令，或者树莓派曾经出现过欠压、USB reset。建议先看 `summary` 和 `checks` 里的具体原因。
- `critical`：有明显问题。例如 D435 没检测到、温度太高、磁盘空间非常紧张。建议先处理问题，不继续跑导航相关测试。

再看：

```text
robot_action_executed
```

它应该一直是 `false`。这表示健康检查没有控制机器人。

## 阶段 1：D435 深度采集

新增工具：

```text
server/realsense_depth_tool.py
```

查看依赖：

```bash
cd /home/duck/apps/voice-assistant
./.venv/bin/python -m server.realsense_depth_tool status
```

如果缺少 `pyrealsense2`，会返回：

```json
{
  "ok": true,
  "pyrealsense2_importable": false
}
```

安装依赖时先不要乱编译，优先尝试：

```bash
./.venv/bin/python -m pip install pyrealsense2 numpy opencv-python-headless
```

如果 Python 3.13/aarch64 没有 `pyrealsense2` 可用 wheel，记录报错。不要硬编译破坏当前环境。

当前 D435 的 V4L2 节点已经确认：

```text
/dev/video0 -> Z16 16-bit Depth，深度流
/dev/video2 -> GREY/UYVY/Y8I/Y12I，红外/灰度相关流
/dev/video4 -> YUYV 彩色流
```

所以 `pyrealsense2` pip 不可用时，第一版深度采集改走 V4L2：

```text
/dev/video0
-> Z16 16-bit Depth
-> OpenCV/V4L2
-> depth_raw.png
-> depth_m.npy
-> depth_vis.png
-> obstacle_summary.json
-> navigation_dry_run.json
```

采集一组深度数据：

```bash
./.venv/bin/python -m server.realsense_depth_tool capture \
  --output-dir /tmp/openduck_depth_test \
  --backend v4l2 \
  --device /dev/video0 \
  --width 640 \
  --height 480 \
  --fps 15 \
  --warmup-frames 5 \
  --no-color
```

输出文件：

```text
/tmp/openduck_depth_test/depth_raw.png
/tmp/openduck_depth_test/depth_m.npy
/tmp/openduck_depth_test/depth_vis.png
/tmp/openduck_depth_test/color.jpg
/tmp/openduck_depth_test/intrinsics.json
/tmp/openduck_depth_test/obstacle_summary.json
/tmp/openduck_depth_test/navigation_dry_run.json
```

说明：

- `depth_raw.png` 是原始深度图，通常是毫米级整数。
- `depth_m.npy` 是 Python 数组，单位是米，更适合程序继续算。
- `depth_vis.png` 是给人看的彩色深度图，暖色通常表示更近，黑色表示无效深度。
- `intrinsics.json` 在 V4L2 版本里只记录设备、格式和比例；完整相机内参后续仍需要 `pyrealsense2/librealsense` 或相机标定文件。
- `obstacle_summary.json` 是前方障碍距离摘要。
- `navigation_dry_run.json` 是只读导航建议，不会控制机器人。

注意：V4L2 深度比例默认用 `0.001` 米/单位，也就是 raw 值 1000 约等于 1 米。后续如果拿到 RealSense 标定信息，再精确修正。

## 阶段 2：障碍物距离摘要

如果已经有 `depth_m.npy`，可以离线算一次：

```bash
./.venv/bin/python -m server.realsense_depth_tool obstacle-summary \
  --depth /tmp/openduck_depth_test/depth_m.npy \
  --block-distance 0.7
```

输出大概长这样：

```json
{
  "ok": true,
  "source": "realsense-depth",
  "left_min_distance_m": 0.91,
  "front_min_distance_m": 0.62,
  "right_min_distance_m": 1.35,
  "block_distance_m": 0.7,
  "blocked": true,
  "robot_action_executed": false
}
```

大白话解释：

- `front_min_distance_m` 是正前方比较近的障碍距离。
- `blocked: true` 表示前方距离小于阈值，例如 0.7 米。
- 这只是观察结果，不会让机器人停、转、走。

## 阶段 3：深度可视化

如果已经有 `depth_m.npy`，可以单独生成彩色深度图：

```bash
./.venv/bin/python -m server.realsense_depth_tool visualize \
  --depth /tmp/openduck_depth_test/depth_m.npy \
  --output /tmp/openduck_depth_test/depth_vis.png \
  --min-m 0.2 \
  --max-m 4.0
```

复制回 Windows 查看：

```powershell
scp duck@10.64.16.56:/tmp/openduck_depth_test/depth_vis.png C:\Users\wx151\Downloads\depth_vis.png
```

大白话解释：

- 黑色：没有有效深度。
- 暖色：更近。
- 冷色：更远。

## 阶段 4：导航 dry-run

第一版导航只输出建议，不执行动作：

```bash
./.venv/bin/python -m server.realsense_depth_tool navigation-dry-run \
  --summary /tmp/openduck_depth_test/obstacle_summary.json
```

输出大概长这样：

```json
{
  "ok": true,
  "source": "navigation-dry-run",
  "mode": "navigation_dry_run",
  "suggested_action": "go_forward",
  "reason": "front_clear",
  "front_min_distance_m": 1.939,
  "left_min_distance_m": 0.434,
  "right_min_distance_m": 2.644,
  "blocked": false,
  "robot_action_executed": false
}
```

建议含义：

- `hold_position`：数据质量太低或前方距离未知，只建议原地等待。
- `turn_left` / `turn_right`：前方被挡，根据左右距离给出转向建议。
- `go_slow_forward`：前方没挡住但比较近。
- `go_forward`：前方比较安全。

注意：这些只是 JSON 建议，不会调用机器人运动命令。

## 阶段 5：连续观察模式

连续观察模式会每隔一段时间抓一次深度图，并把导航建议写到 JSONL 日志里。它仍然只是观察和建议，不会控制机器人。

```bash
./.venv/bin/python -m server.realsense_depth_tool observe-loop \
  --backend v4l2 \
  --device /dev/video0 \
  --output-root /tmp/openduck_depth_observe \
  --count 10 \
  --interval 1 \
  --width 640 \
  --height 480 \
  --fps 15 \
  --warmup-frames 2
```

输出目录类似：

```text
/tmp/openduck_depth_observe/
  navigation_dry_run.jsonl
  001_20260705_143900/
  002_20260705_143901/
  ...
```

每个子目录里会有：

```text
depth_raw.png
depth_m.npy
depth_vis.png
obstacle_summary.json
navigation_dry_run.json
```

把日志复制回 Windows：

```powershell
scp duck@10.64.16.56:/tmp/openduck_depth_observe/navigation_dry_run.jsonl C:\Users\wx151\Downloads\navigation_dry_run.jsonl
```

## 阶段 6：和 YOLO 结合

当前 YOLO 低功耗路线已经跑通：

```bash
./.venv/bin/python -m server.onnx_vision_tool detect \
  --image /tmp/realsense_d435.jpg \
  --model /home/duck/.openduck/models/yolo11n_320.onnx \
  --threads 1 \
  --annotated-output /tmp/realsense_d435_onnx.jpg
```

推荐组合方式：

```text
YOLO 负责回答：前方是什么东西？
D435 深度负责回答：它离我多远？
```

不要只靠 YOLO 避障。YOLO 可能漏检，深度距离更适合做安全底线。

新增融合工具：

```text
server/navigation_perception_tool.py
```

查看状态：

```bash
./.venv/bin/python -m server.navigation_perception_tool status
```

只融合深度摘要：

```bash
./.venv/bin/python -m server.navigation_perception_tool observe \
  --depth-summary /tmp/openduck_depth_test/obstacle_summary.json \
  --output /tmp/openduck_depth_test/perception_fusion.json
```

融合深度摘要和 YOLO 物体识别：

```bash
./.venv/bin/python -m server.navigation_perception_tool observe \
  --depth-summary /tmp/openduck_depth_test/obstacle_summary.json \
  --image /tmp/realsense_d435.jpg \
  --onnx-model /home/duck/.openduck/models/yolo11n_320.onnx \
  --annotated-output /tmp/openduck_depth_test/yolo_annotated.jpg \
  --output /tmp/openduck_depth_test/perception_fusion.json \
  --threads 1
```

输出会包含：

```text
scene_risk
obstacle_summary
navigation_dry_run
objects
object_count
robot_action_executed: false
```

这一步仍然只是感知融合，不控制机器人。

融合连续观察模式：

```bash
./.venv/bin/python -m server.navigation_perception_tool observe-loop \
  --output-root /tmp/openduck_fusion_loop \
  --count 5 \
  --interval 1 \
  --depth-backend v4l2 \
  --depth-device /dev/video0 \
  --color-device /dev/video4 \
  --onnx-model /home/duck/.openduck/models/yolo11n_320.onnx \
  --threads 1
```

它会自动完成：

```text
采深度 -> 拍 RGB -> YOLO -> 融合 -> 写 perception_fusion.jsonl
```

每一轮都会生成：

```text
depth_vis.png
realsense_rgb.jpg
yolo_annotated.jpg
perception_fusion.json
```

总日志：

```text
/tmp/openduck_fusion_loop/perception_fusion.jsonl
```

## 阶段 7：定位和建图

D435 没有 IMU，所以能做 RGB-D 建图，但稳定性不如 D435i。

建议路线：

1. 先用 `realsense_depth_tool.py` 采集 RGB、Depth、内参。
2. 再研究 RTAB-Map RGB-D 建图。
3. 如果要上 ROS 2 + Nav2，建议单独 SD 卡或隔离环境测试。
4. 不要把 ROS 2 直接塞进当前 voice-assistant `.venv`。

第一版定位/导航 dry-run 输出应该像这样：

```json
{
  "ok": true,
  "mode": "navigation_dry_run",
  "pose": {"x": 0.42, "y": 1.13, "yaw": 0.18},
  "target": {"x": 2.0, "y": 1.0},
  "suggested_action": "turn_left",
  "robot_action_executed": false
}
```

也就是说，只建议，不执行。

## Windows 复制文件到树莓派

复制新工具：

```powershell
scp "E:\desktop\树莓派5\server\realsense_depth_tool.py" duck@10.64.16.56:/home/duck/apps/voice-assistant/server/
scp "E:\desktop\树莓派5\server\navigation_safety_gate_tool.py" duck@10.64.16.56:/home/duck/apps/voice-assistant/server/
```

复制规划文档：

```powershell
scp "E:\desktop\树莓派5\docs\navigation_localization_plan.md" duck@10.64.16.56:/home/duck/apps/voice-assistant/docs/
```

如果要先备份旧文件：

```powershell
ssh duck@10.64.16.56 'cd /home/duck/apps/voice-assistant && ts=$(date +%Y%m%d_%H%M%S) && [ -f server/realsense_depth_tool.py ] && cp -a server/realsense_depth_tool.py server/realsense_depth_tool.py.bak.$ts'
```

## 本地测试

在 Windows 项目根目录至少运行：

```bash
python -m server.realsense_depth_tool status
python -m server.realsense_depth_tool obstacle-summary --depth 不存在的深度.npy
python -m server.realsense_depth_tool visualize --depth 不存在的深度.npy
python -m server.realsense_depth_tool navigation-dry-run --summary 不存在的摘要.json
python -m server.realsense_depth_tool observe-loop --count 1 --device 不存在的视频设备
python -m server.navigation_perception_tool status
python -m server.navigation_perception_tool observe --depth-summary 不存在的摘要.json
python -m server.navigation_perception_tool observe-loop --count 1 --depth-device 不存在的视频设备
python -m compileall server
```

这些测试不访问机器人，不访问 runtime，不控制机器人。

## 阶段 8：导航决策 dry-run

现在已经能拿到：

```text
D435 深度
RGB 图片
YOLO 物体识别
perception_fusion.json / perception_fusion.jsonl
```

下一步不是直接让机器人动，而是先加一个“决策闸门”：

```text
感知结果 -> 导航决策 dry-run -> 输出动作意图和原因
```

新增工具：

```text
server/navigation_decision_tool.py
```

它只读取 JSON 文件，不采相机、不跑 YOLO、不访问 /ws、不控制机器人。

### 查看状态

```bash
cd /home/duck/apps/voice-assistant

./.venv/bin/python -m server.navigation_decision_tool status
```

### 对单帧融合结果做决策

```bash
cd /home/duck/apps/voice-assistant

./.venv/bin/python -m server.navigation_decision_tool decide \
  --perception /tmp/openduck_fusion_loop_20/020_20260705_150719/perception_fusion.json \
  --output /tmp/openduck_fusion_loop_20/navigation_decision.json
```

输出里重点看：

```text
decision.intent
decision.reason
decision.motion_profile
robot_action_executed
```

其中：

```text
hold_position：保持不动
go_slow_forward：慢速向前建议
go_forward：向前建议
turn_left：左转建议
turn_right：右转建议
```

注意：这些只是建议，不会真的发给机器人。

### 对 20 帧日志做决策统计

```bash
cd /home/duck/apps/voice-assistant

./.venv/bin/python -m server.navigation_decision_tool decide-log \
  --input-jsonl /tmp/openduck_fusion_loop_20/perception_fusion.jsonl \
  --output-jsonl /tmp/openduck_fusion_loop_20/navigation_decision.jsonl \
  --summary-output /tmp/openduck_fusion_loop_20/navigation_decision_summary.json
```

这个命令会把每一帧的感知结果变成一条导航决策，并汇总：

```text
intent_counts：每种动作建议出现多少次
reason_counts：每种原因出现多少次
front_min_distance_m：20 帧里最近的前方距离
front_avg_distance_m：平均前方距离
robot_action_executed: false
```

### 复制结果回 Windows

```powershell
scp duck@10.64.16.56:/tmp/openduck_fusion_loop_20/navigation_decision_summary.json C:\Users\wx151\Downloads\navigation_decision_summary.json
scp duck@10.64.16.56:/tmp/openduck_fusion_loop_20/navigation_decision.jsonl C:\Users\wx151\Downloads\navigation_decision.jsonl
```

### 安全边界

- 没有修改 `/ws` 主链路。
- 没有修改 Android APP 手柄控制。
- 没有让视觉链路控制机器人。
- 没有写入 SSH 密码、API key 或 token。
- 没有提交 Git。

## 安全闸门 dry-run、手动授权、急停、供电稳定性确认

现在导航链路已经能输出 `navigation_decision.json`，但这还不能直接拿去让机器人动。原因很简单：视觉和深度数据可能会误判，树莓派供电也可能不稳，如果直接把建议接到运动控制，机器人可能在错误时间前进或转向。

所以这里新增一个安全闸门：

```text
健康检查 JSON
+ 导航决策 JSON
+ 手动授权状态
+ 急停状态
-> 安全闸门 dry-run 输出
```

新增工具：

```text
server/navigation_safety_gate_tool.py
```

这个工具只做判断，不控制机器人，不发 `/ws`，不调用 `robot_executor`，不调用 `robot_commands`。输出里的 `execution_enabled` 永远是 `false`，`robot_action_executed` 永远是 `false`。即使闸门判断“如果将来做低速测试，可以允许”，也只会输出：

```json
{
  "would_allow_low_speed_execution": true,
  "execution_enabled": false,
  "robot_action_executed": false
}
```

### 为什么不能直接让机器人动

现在这条链路还处在验证阶段。D435 深度、YOLO 识别、融合结果、导航决策都可能受到光线、遮挡、USB 状态、供电影响。安全闸门的作用是先把“能不能进入低速测试”这件事说清楚，而不是直接把建议变成运动命令。

大白话说：导航决策只是“我建议这样走”，安全闸门只是“这个建议目前看起来能不能进入低速测试准备”。真正让机器人动，仍然必须另外接入人工确认过的运动控制链路，本阶段不做。

### 为什么先检查供电

树莓派 5、D435、ONNX 推理一起跑时，耗电会变大。之前已经出现过：

```text
throttled=0x50005
Undervoltage detected!
```

这说明树莓派曾经欠压或限频。供电不稳时，可能出现 SSH 掉线、USB 摄像头断流、推理卡顿，严重时会关机。所以低速执行前必须先跑健康检查：

```bash
cd /home/duck/apps/voice-assistant

./.venv/bin/python -m server.pi_health_check_tool check \
  --output /tmp/openduck_health.json
```

如果 `/tmp/openduck_health.json` 里出现：

```json
"overall_status": "critical"
```

或者 `checks.power.status` 不是 `ok`，安全闸门必须拒绝。也就是说，供电不稳时，不允许给出低速执行建议。

还要单独看一次：

```bash
vcgencmd get_throttled
```

最理想结果是：

```text
throttled=0x0
```

如果不是 `0x0`，就说明存在当前或历史欠压、限频风险。这个时候先处理电源、线材、USB Hub，不要进入低速执行测试。

### 为什么需要手动授权

手动授权是为了防止“程序刚好判断通过，就自动进入下一步”。授权文件只有短时间有效，默认 60 秒。也就是说，操作者必须在现场确认环境安全，再手动敲一次授权命令。

授权命令：

```bash
./.venv/bin/python -m server.navigation_safety_gate_tool authorize \
  --reason "bench dry-run before low speed test" \
  --ttl-sec 60
```

查看授权是否还有效：

```bash
./.venv/bin/python -m server.navigation_safety_gate_tool authorization-status
```

撤销授权：

```bash
./.venv/bin/python -m server.navigation_safety_gate_tool revoke-authorization \
  --reason "test done"
```

如果没有有效授权，安全闸门会拒绝，并给出：

```json
{
  "deny_reasons": [
    "manual_authorization_required"
  ]
}
```

### 急停状态是什么

这里的急停只是安全闸门状态，不会真的给机器人发送停止命令。它的作用是让后续安全闸门评估一律拒绝。

开启急停状态：

```bash
./.venv/bin/python -m server.navigation_safety_gate_tool emergency-stop \
  --reason "user requested stop"
```

开启后再跑 `evaluate`，会得到：

```json
{
  "dry_run_allowed": false,
  "deny_reasons": [
    "emergency_stop_active"
  ],
  "robot_action_executed": false
}
```

解除急停状态必须写清楚原因：

```bash
./.venv/bin/python -m server.navigation_safety_gate_tool clear-emergency-stop \
  --reason "manual reset after checking area"
```

注意：解除急停状态也不会让机器人动，只是允许安全闸门重新评估。

### 完整安全闸门流程

第一步，进入树莓派项目目录：

```bash
cd /home/duck/apps/voice-assistant
```

第二步，先做健康检查，并保存结果：

```bash
./.venv/bin/python -m server.pi_health_check_tool check \
  --output /tmp/openduck_health.json
```

第三步，现场人工确认安全后，给 60 秒手动授权：

```bash
./.venv/bin/python -m server.navigation_safety_gate_tool authorize \
  --reason "bench dry-run before low speed test" \
  --ttl-sec 60
```

第四步，读取健康检查和导航决策，生成安全闸门结果：

```bash
./.venv/bin/python -m server.navigation_safety_gate_tool evaluate \
  --health /tmp/openduck_health.json \
  --decision /tmp/openduck_fusion_loop_20/navigation_decision.json \
  --output /tmp/openduck_safety_gate.json
```

重点看：

```text
dry_run_allowed
would_allow_low_speed_execution
deny_reasons
gate.power_stable
gate.manual_authorization_valid
gate.emergency_stop_active
execution_enabled
robot_action_executed
```

只有在健康检查不是 `critical`、供电 `ok`、急停没开、授权有效、深度有效、前方距离大于 0.45 米，并且决策是 `go_slow_forward`、`turn_left` 或 `turn_right` 时，才可能出现：

```json
{
  "dry_run_allowed": true,
  "would_allow_low_speed_execution": true,
  "execution_enabled": false,
  "robot_action_executed": false
}
```

这仍然只是 dry-run 结果，不会控制机器人。

安全闸门还会检查导航决策本身是不是明确标成 dry-run：`execution_enabled` 必须是 `false`，`requires_manual_enable` 必须是 `true`，并且决策或动作配置里必须有 `not_for_direct_execution: true`。如果这些标记不完整，安全闸门会拒绝，避免把来路不明的动作建议误当成可测试建议。

### 复制结果回 Windows

在 Windows PowerShell 里运行：

```powershell
scp duck@10.64.16.56:/tmp/openduck_safety_gate.json C:\Users\wx151\Downloads\openduck_safety_gate.json
scp duck@10.64.16.56:/tmp/openduck_health.json C:\Users\wx151\Downloads\openduck_health.json
```

如果提示输入密码，只在 SSH 或 scp 的交互提示里输入，不要把密码写进脚本、文档、日志、配置文件或命令参数。

### 树莓派验证命令

把 `server/navigation_safety_gate_tool.py` 复制到树莓派后，在树莓派 SSH 终端里运行下面这些命令：

```bash
cd /home/duck/apps/voice-assistant

./.venv/bin/python -m server.navigation_safety_gate_tool status

./.venv/bin/python -m server.navigation_safety_gate_tool authorization-status

./.venv/bin/python -m server.navigation_safety_gate_tool evaluate \
  --health /tmp/openduck_health.json \
  --decision /tmp/openduck_fusion_loop_20/navigation_decision.json \
  --output /tmp/openduck_safety_gate.json

./.venv/bin/python -m server.navigation_safety_gate_tool authorize \
  --reason "test authorization" \
  --ttl-sec 60

./.venv/bin/python -m server.navigation_safety_gate_tool evaluate \
  --health /tmp/openduck_health.json \
  --decision /tmp/openduck_fusion_loop_20/navigation_decision.json \
  --output /tmp/openduck_safety_gate.json

./.venv/bin/python -m server.navigation_safety_gate_tool emergency-stop \
  --reason "test emergency stop"

./.venv/bin/python -m server.navigation_safety_gate_tool evaluate \
  --health /tmp/openduck_health.json \
  --decision /tmp/openduck_fusion_loop_20/navigation_decision.json \
  --output /tmp/openduck_safety_gate.json

./.venv/bin/python -m server.navigation_safety_gate_tool clear-emergency-stop \
  --reason "test reset"

./.venv/bin/python -m compileall server
```

预期结果：

- 没有授权时，`evaluate` 应该拒绝，`deny_reasons` 里会有 `manual_authorization_required`。
- 授权有效、健康检查 ok、供电 ok、深度有效、前方距离安全时，`evaluate` 可以输出 `would_allow_low_speed_execution: true`。
- 急停开启时，`evaluate` 必须拒绝，`deny_reasons` 里会有 `emergency_stop_active`。
- 所有输出里的 `robot_action_executed` 都应该是 `false`。
- 所有输出里的 `execution_enabled` 都应该是 `false`。
