# OpenDuck 本地视觉流水线第一版说明

## 这是什么

这是给鸭子加“本地眼睛”的第一版旁路工具。

它只做三件事：

1. Gemini 2 摄像头拍一张 RGB 图片。
2. OpenCV 把图片保存成 jpg。
3. YOLO11n 在本机识别图片里的物体，并输出 `objects` JSON。

它不会控制机器人，不会访问机器人 runtime，也不会往 `/ws` 主链路里塞新逻辑。

## 当前安全边界

现有语音链路不变：

```text
Android APP 语音
-> /ws type=chat
-> server/app.py
-> generate_reply
-> Duck Agent
-> 本地 gemma
-> Android TTS
```

现有手柄链路不变：

```text
Android APP 手柄
-> /ws type=robot_command
-> robot_executor
-> runtime voice_overlay.py
```

本地视觉第一版只是独立命令行工具：

```text
Gemini 2
-> Orbbec SDK v2 / pyorbbecsdk
-> OpenCV
-> YOLO11n
-> objects JSON
```

第一版明确不做：

- 不读深度图。
- 不做实时视频。
- 不接 Qwen-VL。
- 不接 VLA。
- 不控制机器人运动。
- 不发送 `joystick_velocity`、前进、转向、横移、速度控制等命令。

## 官方资料确认

- Orbbec 官方 `pyorbbecsdk` 仓库说明：它是 Orbbec SDK v2.x 的官方 Python binding，模块用 `pyorbbecsdk`，官方推荐安装 PyPI 包 `pyorbbecsdk2`。
- Orbbec 官方文档/发布说明里列出了 Gemini 2，并给出了推荐固件版本；如果设备在支持列表里，优先使用 v2-main / SDK v2 路线。
- Ultralytics 官方 Raspberry Pi 文档建议在树莓派这类 ARM 设备上优先考虑 NCNN，因为它更适合移动端和嵌入式设备。
- Ultralytics 官方 NCNN 文档说明，YOLO 模型可以导出成 NCNN，用于更轻量的本地推理。

参考链接：

- https://github.com/orbbec/pyorbbecsdk
- https://orbbec.github.io/pyorbbecsdk/
- https://github.com/orbbec/OrbbecSDK_v2
- https://docs.ultralytics.com/guides/raspberry-pi
- https://docs.ultralytics.com/integrations/ncnn

## 新增工具

### 1. Orbbec 摄像头工具

文件：

```text
server/orbbec_camera_tool.py
```

查看状态：

```bash
python -m server.orbbec_camera_tool status
```

拍一张图，默认保存到 `data/vision_snapshots/`：

```bash
python -m server.orbbec_camera_tool capture
```

拍一张图，并指定输出路径：

```bash
python -m server.orbbec_camera_tool capture --output /tmp/openduck_camera.jpg
```

如果没装 `pyorbbecsdk`，会返回：

```json
{
  "ok": false,
  "error": "pyorbbecsdk_not_installed"
}
```

如果没接相机，会返回：

```json
{
  "ok": false,
  "error": "camera_not_found"
}
```

### 2. YOLO 本地检测工具

文件：

```text
server/yolo_vision_tool.py
```

查看状态：

```bash
python -m server.yolo_vision_tool status
```

检测一张图片：

```bash
python -m server.yolo_vision_tool detect --image path/to/image.jpg
```

指定模型文件：

```bash
python -m server.yolo_vision_tool detect --image path/to/image.jpg --model models/yolo11n.pt
```

输出大概长这样：

```json
{
  "ok": true,
  "source": "yolo",
  "model": "models/yolo11n.pt",
  "image_path": "path/to/image.jpg",
  "objects": [
    {
      "label": "person",
      "confidence": 0.91,
      "box_xyxy": [120, 80, 300, 420]
    }
  ],
  "count": 1,
  "robot_action_executed": false
}
```

### 3. 本地视觉流水线

文件：

```text
server/local_vision_pipeline.py
```

查看整体状态：

```bash
python -m server.local_vision_pipeline status
```

直接检测已有图片：

```bash
python -m server.local_vision_pipeline observe --image path/to/image.jpg
```

不传图片时，先用 Gemini 2 拍一张，再跑 YOLO：

```bash
python -m server.local_vision_pipeline observe
```

统一返回里会一直带着：

```json
{
  "robot_action_executed": false
}
```

意思是：这个视觉工具只负责看，不负责动。

## 模型文件怎么放

第一版不把大模型文件放进 Git。

默认会按顺序找：

```text
models/yolo11n.pt
model/yolo11n.pt
~/.openduck/models/yolo11n.pt
```

如果模型不存在，`status` 会说 `model_found: false`，检测命令会返回 `model_not_found`。工具不会偷偷自动下载。

手动准备模型可以这样做：

```bash
mkdir -p ~/.openduck/models
# 然后你自己从 Ultralytics 官方渠道下载 yolo11n.pt
# 放到 ~/.openduck/models/yolo11n.pt
```

后续要提速，可以在 PC 上导出 NCNN：

```bash
yolo export model=yolo11n.pt format=ncnn imgsz=640
```

第一版不强制 NCNN，因为先把“能拍图、能识别、能输出 JSON”跑通更重要。

## 树莓派 5 安装依赖

先进入 OpenDuck 服务目录：

```bash
cd /home/duck/apps/voice-assistant
```

升级 pip：

```bash
.venv/bin/python -m pip install --upgrade pip
```

安装基础依赖：

```bash
.venv/bin/python -m pip install numpy opencv-python-headless ultralytics
```

安装 Orbbec SDK Python 包，优先按官方方式：

```bash
.venv/bin/python -m pip install --upgrade pyorbbecsdk2
```

注意：安装包叫 `pyorbbecsdk2`，代码里导入的模块名通常是 `pyorbbecsdk`。这就像 C++ 里库文件名和 `#include` 名字不一定完全一样。

如果树莓派 ARM64 上 pip 找不到合适 wheel，不要硬装来路不明的包。下一步按 Orbbec 官方 GitHub Release 或官方文档走源码/安装包路线，并把失败原因记下来。

## 安装前检查命令

如果要远程检查树莓派，SSH 命令里不要写密码。登录后执行：

```bash
uname -a
cat /etc/os-release
python3 --version
cd /home/duck/apps/voice-assistant && .venv/bin/python --version
free -h
df -h
vcgencmd measure_temp
lsusb
```

检查 OpenDuck 服务：

```bash
systemctl status voice-assistant-websocket.service --no-pager
systemctl status voice-assistant-llama.service --no-pager
```

这些命令只是在看系统信息、Python 版本、内存、磁盘、温度、USB 设备和服务状态，不会控制机器人。

## 本地测试命令

在项目根目录运行：

```bash
python -m server.orbbec_camera_tool status
python -m server.yolo_vision_tool status
python -m server.yolo_vision_tool detect --image 不存在的图片.jpg
python -m server.local_vision_pipeline status
python -m server.local_vision_pipeline observe --image 不存在的图片.jpg
python -m compileall server
```

如果没装依赖、没接相机、没放模型，工具应该返回 JSON 说明原因，而不是崩溃。

如果树莓派已经装好依赖，并且接了 Gemini 2，再跑：

```bash
python -m server.orbbec_camera_tool status
python -m server.orbbec_camera_tool capture --output /tmp/openduck_camera.jpg
python -m server.yolo_vision_tool detect --image /tmp/openduck_camera.jpg
python -m server.local_vision_pipeline observe --image /tmp/openduck_camera.jpg
```

## 查看温度和 CPU

看温度：

```bash
vcgencmd measure_temp
```

看有没有降频：

```bash
vcgencmd get_throttled
```

看 CPU 和内存压力：

```bash
top
```

大白话解释：YOLO 会吃 CPU 和内存。树莓派温度太高会自动降速，识别会变慢；温度继续高就不适合继续压测。

## 如果 Ultralytics 太重怎么办

如果 `ultralytics` 或 PyTorch 在树莓派上安装困难，不要硬怼环境。

可以选这些替代路线：

1. 先只跑 Gemini 2 拍照，把图片保存下来。
2. 在 PC 上把 `yolo11n.pt` 导出成 ONNX 或 NCNN。
3. 树莓派上用更轻的 ONNXRuntime 或 NCNN 跑模型。
4. 或者先把图片发到更强的电脑上识别，树莓派只负责采集。

## 后续怎么接 Qwen-VL 和 Duck Agent

建议后续分两步：

1. 本地 YOLO 先输出 `objects`，例如“人、椅子、桌子、障碍物”。
2. 再把图片和 `objects` 一起交给 Qwen-VL 或 Duck Agent，让它用大白话解释画面。

就算后续接入 Duck Agent，也建议继续保持：

```json
{
  "robot_action_executed": false
}
```

也就是说，视觉链路可以“观察”和“建议”，但不直接驾驶机器人。真正动作仍然走现有受控链路，并且需要明确确认。
