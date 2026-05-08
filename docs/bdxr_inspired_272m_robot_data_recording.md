# BDXR-inspired 272M 真机数据采集

本分支默认部署模型：

```text
WALK_BDXR_INSPIRED_272M.onnx
```

该模型来自 Open Duck 训练：

```text
/home/nano/Documents/0-duck/open_duck_playground/checkpoints/5090d_ubuntu22/bdxr_inspired_101_300m/bdxr_inspired_300m_direct_20260508_1017/2026_05_08_105009_65536000.onnx
```

离线 MuJoCo 评估显示它没有摔倒，线速度跟踪优于 `BEST_WALK_ONNX_2.onnx`，但动作、力矩和关节速度更激进。真机先采集低速固定序列，不建议直接自由操控。

## 快速检查

```bash
cd ~/open_duck_mini_runtime
./run_duck.sh check
```

确认：

- `/dev/ttyACM0` 存在
- `/dev/input/js0` 存在
- 电池电压高于安全阈值
- 机器人悬空或有人扶持

## 固定序列采集

默认序列只包含站立、0.05、0.10、0.15 m/s 前进和停步段。脚本启动后先进入保持姿态，按 Xbox `A` 开始一次 trial，按 `B` 中止当前 trial。

```bash
cd ~/open_duck_mini_runtime

python scripts/record_5090_v01_robot_trial.py \
  --duck_config_path duck_config.json \
  --onnx_model_path WALK_BDXR_INSPIRED_272M.onnx \
  --model_tag bdxr_inspired_272m \
  --output_dir robot_logs/bdxr_inspired_272m \
  --trials 3 \
  --notes "bdxr inspired 272m first real robot fixed-command trials"
```

输出目录：

```text
robot_logs/bdxr_inspired_272m/<YYYYMMDD_HHMMSS>/
```

每次 trial 会写：

```text
trial_001.csv
trial_001.npz
trial_001.json
```

CSV 用于快速看异常，NPZ 保存完整 `obs/action/target/joint/current/voltage` 数组。

## 常规启动

本分支的 `run_duck.sh` 默认模型已经切到 `WALK_BDXR_INSPIRED_272M.onnx`：

```bash
./run_duck.sh start
./run_duck.sh status
./run_duck.sh log
./run_duck.sh stop
```

实机首测时建议显式降低风险参数：

```bash
DUCK_ACTION_SCALE=0.16 DUCK_MIN_MOTOR_VOLTAGE=6.8 ./run_duck.sh start
```

如果固定序列数据里 `action_saturation_frac`、`target_delta_abs_max`、`sum_abs_current` 明显偏高，先不要用自由操控模式。
