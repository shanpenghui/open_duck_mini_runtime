# 5090_v0.1 真机固定指令数据采集

本流程用于采集真机数据，目标是对比仿真和真机在观测、动作、目标关节、电机执行和供电上的差异，避免只凭经验修改训练参数。

## 启动方式

默认模型是 `5090_v0.1` 最后一个 321M ONNX：

```bash
cd ~/Documents/0-duck/open_duck_mini_runtime

python scripts/record_5090_v01_robot_trial.py \
  --duck_config_path duck_config.json \
  --trials 0 \
  --notes "5090_v0.1 fixed command data"
```

如需显式指定模型：

```bash
python scripts/record_5090_v01_robot_trial.py \
  --onnx_model_path /path/to/model.onnx \
  --duck_config_path duck_config.json
```

## 控制时序

脚本启动后会初始化电机并进入暂停保持状态。此时机器人保持默认站立姿态，不执行行走命令。

按 Xbox `A` 后开始一次完整 trial。默认控制序列：

| phase | duration | cmd_x | cmd_y | cmd_yaw |
| --- | ---: | ---: | ---: | ---: |
| stand_pre | 2s | 0.00 | 0.00 | 0.00 |
| forward_005 | 6s | 0.05 | 0.00 | 0.00 |
| stop_after_005 | 2s | 0.00 | 0.00 | 0.00 |
| forward_010 | 8s | 0.10 | 0.00 | 0.00 |
| stop_after_010 | 2s | 0.00 | 0.00 | 0.00 |
| forward_015 | 10s | 0.15 | 0.00 | 0.00 |
| stand_post | 3s | 0.00 | 0.00 | 0.00 |

一次 trial 结束后，脚本自动把命令归零、回到默认保持状态，并保存数据。再次按 `A` 会记录下一次 trial。

运行中按 Xbox `B` 会中止当前 trial，保存已采到的数据，然后回到保持状态。`Ctrl+C` 退出并关闭电机力矩。

## 输出

默认输出目录：

```text
robot_logs/5090_v0.1/<YYYYMMDD_HHMMSS>/
```

每次 trial 生成：

```text
trial_001.csv
trial_001.npz
trial_001.json
```

CSV 用于快速查看关键指标；NPZ 保存完整数组，后续用于和仿真日志逐字段对比；JSON 保存模型、PID、初始姿态、关节名、summary 和文件路径。

## 记录字段

每个控制周期记录：

- `cmd[7]`
- `obs[101]`
- `action[14]`
- `target[14]`
- `target_delta[14]`
- `joint_pos[14]`
- `joint_vel[14]`
- `contacts[2]`
- `voltages[14]`
- `currents[14]`
- `loop_dt_s`
- `missed_deadline`
- `obs_finite`
- `action_finite`

CSV 额外展开常用诊断字段：

- `gyro_x/y/z`
- `accel_x/y/z`
- `joint_vel_abs_max`
- `action_abs_max`
- `action_saturation_frac`
- `target_delta_abs_max`
- `left_contact/right_contact`
- `min_voltage`
- `sum_abs_current`

## 自定义控制序列

可用 JSON 覆盖默认时序：

```json
[
  {"name": "stand", "duration": 2.0, "cmd_x": 0.0, "cmd_y": 0.0, "cmd_yaw": 0.0},
  {"name": "forward_010", "duration": 8.0, "cmd_x": 0.1, "cmd_y": 0.0, "cmd_yaw": 0.0},
  {"name": "stop", "duration": 3.0, "cmd_x": 0.0, "cmd_y": 0.0, "cmd_yaw": 0.0}
]
```

启动时传入：

```bash
python scripts/record_5090_v01_robot_trial.py --sequence_json my_sequence.json
```
