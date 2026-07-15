# Qwen-VL 同步部署检查清单

## 它在 OpenDuck 里做什么

Qwen-VL 在这个项目里先只做“看图解释”。

简单说，它负责看一张 RGB 图片，然后返回：

```text
scene：画面里大概是什么场景
objects：看到了哪些物体
risks：有哪些风险
safe_to_move：前面是否看起来安全
suggested_action：非常保守的建议
```

它是场景解释器，不是机器人驾驶员。

第一版不接机器人运动，不访问 runtime，不发送前进、转向、横移、速度控制命令。

## 当前边界确认

- Android APP 手柄控制仍然只走 `/ws type=robot_command`。
- 语音对话仍然走 `/ws type=chat -> generate_reply -> Duck Agent -> 本地 gemma -> TTS`。
- Qwen-VL 工具只通过命令行独立测试。
- Qwen-VL 不直接控制机器人。
- Qwen-VL 返回了危险动作建议时，本地工具会二次过滤。

允许的建议只有：

```text
pause
stop
head_center
none
```

不允许的建议包括：

```text
joystick_velocity
前进
转向
横移
持续运动
速度控制
```

## 配置 .env

在树莓派上运行：

```bash
mkdir -p ~/.openduck
nano ~/.openduck/.env
```

把下面模板填进去：

```text
QWEN_VISION_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_VISION_MODEL=qwen-vl-plus
QWEN_VISION_API_KEY=这里手动填你的真实 key
```

注意：

- 真实 key 只写在树莓派本机。
- 不要写进项目代码目录。
- 不要写进文档。
- 不要截图发给别人。
- 不要提交 Git。

## 查看工具状态

进入项目目录：

```bash
cd /home/duck/apps/voice-assistant
```

查看 Qwen-VL 工具状态：

```bash
.venv/bin/python -m server.qwen_vision_tool status
```

重点看这几项：

```text
api_key_configured: true 表示已经检测到 key
secret_values_printed: false 表示没有打印真实 key
executes_robot_actions: false 表示不会执行机器人动作
```

如果 `api_key_configured` 是 `false`，说明还没配 key，或者 `.env` 路径不对。

## 用一张图片测试

如果你已经有图片，比如 `/tmp/openduck_camera.jpg`：

```bash
.venv/bin/python -m server.qwen_vision_tool observe --image /tmp/openduck_camera.jpg
```

也可以用测试脚本同时跑 status 和 observe：

```bash
.venv/bin/python tools/qwen_vision_smoke_test.py --image /tmp/openduck_camera.jpg
```

如果想问得更明确：

```bash
.venv/bin/python tools/qwen_vision_smoke_test.py --image /tmp/openduck_camera.jpg --prompt "前面安全吗？"
```

## 没有图片时先拍一张

如果第一阶段本地视觉已经准备好，可以先让 Orbbec 工具拍图：

```bash
.venv/bin/python -m server.orbbec_camera_tool capture --output /tmp/openduck_camera.jpg
```

然后再跑：

```bash
.venv/bin/python -m server.qwen_vision_tool observe --image /tmp/openduck_camera.jpg
```

## 如何看返回 JSON

成功时会看到类似：

```json
{
  "ok": true,
  "source": "qwen-vision",
  "model": "qwen-vl-plus",
  "scene": "前方是室内桌面场景。",
  "objects": ["桌子", "椅子"],
  "risks": ["前方可能有障碍物"],
  "safe_to_move": false,
  "suggested_action": "pause",
  "robot_action_executed": false
}
```

没有 API key 时会看到：

```json
{
  "ok": false,
  "error": "missing_api_key",
  "message": "Qwen vision API key is not configured.",
  "robot_action_executed": false
}
```

这说明工具安全停下了，不是 Python 程序崩溃。

图片路径写错时会看到：

```json
{
  "ok": false,
  "error": "image_not_found",
  "robot_action_executed": false
}
```

## 如何确认它没有执行机器人动作

看返回 JSON 里的：

```text
robot_action_executed: false
```

再看 `status` 里的：

```text
executes_robot_actions: false
```

这两项说明：Qwen-VL 只是看图和解释，没有控制机器人。

本阶段也不需要访问：

```text
127.0.0.1:18765
```

也不需要发送：

```text
joystick_velocity
前进
转向
横移
速度控制
```

## 本地开发测试命令

在 Windows 工作区里可以运行：

```bash
python -m server.qwen_vision_tool status
python -m server.qwen_vision_tool observe
python -m server.qwen_vision_tool observe --image 不存在的图片.jpg
python tools/qwen_vision_smoke_test.py --image 不存在的图片.jpg
python -m compileall server tools
```

这些命令的意义：

- `status`：看配置和安全边界。
- `observe`：不传图片时确认摄像头占位返回 JSON。
- `observe --image 不存在的图片.jpg`：确认图片路径错了也返回 JSON。
- `qwen_vision_smoke_test.py`：一次性跑状态检查和单图观察。
- `compileall`：检查 Python 文件有没有语法错误。

## 后续接入路线

第三阶段可以把这些信息一起发给 Qwen-VL：

```text
RGB 图片
YOLO objects JSON
depth summary JSON
```

Qwen-VL 输出：

```text
scene
risks
safe_to_move
suggested_action
```

然后交给 Duck Agent 整理成人能听懂的话。

第四阶段再做 `scene_observer`，把本地 YOLO、深度距离、Qwen-VL 场景解释合成稳定的 VLA 前置感知输入。

即使到了后续阶段，也建议保持这个原则：

```text
Qwen-VL 负责解释场景，不直接驾驶机器人。
机器人动作必须经过本地安全规则和用户确认。
```
