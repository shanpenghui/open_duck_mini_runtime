# OpenClaw Bridge 第一版接入说明

这份文档说明 OpenClaw 怎么安全接入 OpenDuck。第一版只做一个很小的桥，不改 Android APP 原来的 WebSocket 主链路。

## 一句话架构

```text
OpenClaw WebChat / dashboard
  -> python -m server.agent_bridge ...
  -> 只读查询，或者少量安全动作
  -> 复用 OpenDuck 现有 robot_executor
  -> 127.0.0.1:18765 voice_overlay.py
```

Android APP 还是走原来的：

```text
Android APP
  -> ws://<pi>:8765/ws
  -> type=chat 或 type=robot_command
```

`server/agent_bridge.py` 不接管 `/ws`，也不改 `server/app.py`。手机手柄持续直控还是只能走 `type=robot_command`，不会进入 `chat`，也不会进入大模型。

## Bridge 能做什么

只读命令：

```bash
python -m server.agent_bridge status
python -m server.agent_bridge health
python -m server.agent_bridge robot-state
```

动作命令默认只是预览，不会真的动机器人：

```bash
python -m server.agent_bridge action stop --dry-run
```

真的要执行，必须显式加 `--execute`。这一步应该由人确认后再让 OpenClaw 调：

```bash
python -m server.agent_bridge action stop --execute
python -m server.agent_bridge action pause --execute
python -m server.agent_bridge action resume --execute
python -m server.agent_bridge action head_center --execute
```

第一版允许动作：

- `stop`
- `pause`
- `resume`
- `head_center`

第一版禁止动作：

- `joystick_velocity`
- `walk_forward_step`
- `walk_forward_steps`
- `turn_left_small`
- `turn_right_small`
- `turn_left_3s`
- `turn_right_3s`
- `strafe_left_step`
- `strafe_right_step`
- 所有带速度、方向、持续时间参数的身体运动

禁止这些动作的原因很简单：OpenClaw 是 agent，它适合建议和辅助，不适合第一版就直接持续控制机器人运动。手机 APP 的手柄链路已经有 TTL 和限幅，继续让 APP 负责直控。

## 输出格式

bridge 所有命令都输出 JSON，方便 OpenClaw 读取。动作输出会包含：

- `ok`：这次调用是否成功。
- `action`：动作名。
- `executed`：有没有真的执行。
- `blocked`：有没有被白名单挡住。
- `message`：给人看的说明。
- `detail`：细节。

例子：

```bash
python -m server.agent_bridge action joystick_velocity --dry-run
```

会被拒绝，因为 `joystick_velocity` 只能由 Android APP 手柄链路走 `/ws type=robot_command`。

## WebChat / dashboard 访问方式

第一阶段先用 OpenClaw 的 WebChat / dashboard，不配置 Telegram。

在树莓派上安装并启动 OpenClaw Gateway 后，电脑上开 SSH tunnel：

```bash
ssh -N -L 18789:127.0.0.1:18789 duck@10.64.16.56
```

然后在电脑浏览器打开：

```text
http://127.0.0.1:18789/
```

这只是访问 OpenClaw Gateway 的 dashboard。OpenDuck 的 Android APP 仍然连：

```text
ws://<pi-ip>:8765/ws
```

## 模型接口配置

OpenClaw 的模型接口第一版建议复用 OpenAI-compatible 方式。

本地 llama-server 默认类似：

```text
http://127.0.0.1:18080/v1/chat/completions
```

也可以换成外部 OpenAI-compatible base URL。建议用环境变量，不要写死在代码里：

```bash
export OPENDUCK_OPENAI_BASE_URL="http://127.0.0.1:18080/v1/chat/completions"
```

API key 是可选的。如果外部模型需要 key，可以放环境变量：

```bash
export OPENAI_API_KEY="不要把真实 key 写进文档或代码"
```

或者放在树莓派当前用户的：

```text
~/.openclaw/.env
```

bridge 的 `status` 只会显示有没有配置 key，不会打印 key 的真实内容。

## 树莓派部署草案

这些命令只是草案，真正执行前先备份，不在本地自动远程部署。

检查 OpenDuck：

```bash
ssh duck@10.64.16.56
cd /home/duck/apps/voice-assistant
curl http://127.0.0.1:8765/healthz
python -m server.agent_bridge status
python -m server.agent_bridge health
python -m server.agent_bridge action stop --dry-run
python -m server.agent_bridge action joystick_velocity --dry-run
```

安装 OpenClaw：

```bash
node --version
curl -fsSL https://deb.nodesource.com/setup_24.x | sudo -E bash -
sudo apt install -y nodejs
npm install -g openclaw@latest
openclaw onboard --install-daemon
sudo loginctl enable-linger duck
openclaw gateway status
openclaw doctor
openclaw security audit
```

访问 dashboard：

```bash
ssh -N -L 18789:127.0.0.1:18789 duck@10.64.16.56
```

## 回滚草案

如果 OpenClaw 接入有问题，先停 OpenClaw，不动 OpenDuck：

```bash
openclaw gateway stop
systemctl --user disable --now openclaw-gateway.service
curl http://127.0.0.1:8765/healthz
```

如果需要覆盖树莓派上的 bridge 文件，先备份，再复制：

```bash
cd /home/duck/apps/voice-assistant
cp server/agent_bridge.py server/agent_bridge.py.bak.$(date +%Y%m%d_%H%M%S)
cp docs/openclaw_bridge_plan.md docs/openclaw_bridge_plan.md.bak.$(date +%Y%m%d_%H%M%S)
```

如果 bridge 文件需要回退，只恢复本次新增的 `server/agent_bridge.py` 和这份文档即可。不要清理生成产物，不要删除其它资料文件。
