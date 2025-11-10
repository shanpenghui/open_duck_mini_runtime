- [] Better handle xbox controller 
  - It's a little bit of a mess right now, how we handle directions and buttons etc
- [] Make the offsets flashing work. This will be in the motor configuration script



左摇杆：
前进打满[RAW AXIS] axis0=0.004, axis1=-1, axis2=-1.000, axis3=0.001
后退打满[RAW AXIS] axis0=0.004, axis1=1, axis2=-1.000, axis3=0.001

LT：
松开[RAW AXIS] axis0=-0.015, axis1=-0.037, axis2=-1.000, axis3=0.043
按到底[RAW AXIS] axis0=-0.015, axis1=-0.037, axis2=1.000, axis3=0.043

右摇杆：
向右打满[RAW AXIS] axis0=0.004, axis1=-1, axis2=-1.000, axis3=0.043
向左打满[RAW AXIS] axis0=0.004, axis1=1, axis2=-1.000, axis3=0.043

左摇杆：
向右打满[RAW AXIS] axis0=1.000, axis1=-1, axis2=-1.000, axis3=0.001
向左打满[RAW AXIS] axis0=-1.000, axis1=1, axis2=-1.000, axis3=0.001

1. 这个怎么加？是加在启动脚本的参数里面？比如python /home/duck/Open_Duck_Mini_Runtime/scripts/v2_rl_walk_mujoco.py --onnx_model_path /home/duck/Open_Duck_Mini_Runtime/BEST_WALK_ONNX_2.onnx --duck_config_path /home/duck/duck_config.json --action_scale 0.2
--p 30 -i 0 -d 5
2.加入策略输出归零死区，我没看懂。代码if np.abs(action).max() < 0.1:
   action[:] = 0.0
加在哪里？告诉我具体位置。
3.怎么看看到底哪些关节输出幅度大？是不是所有关节都在抖？
   if i % 50 == 0:
   print(f"[Action raw] {np.round(action, 3)}")
告诉我加代码的具体地方

scp .\scripts\v2_rl_walk_mujoco.py duck@192.168.10.14:/home/duck/Open_Duck_Mini_Runtime/scripts/v2_rl_walk_mujoco.py

scp .\mini_bdx_runtime\mini_bdx_runtime\xbox_controller.py duck@192.168.10.14:/home/duck/Open_Duck_Mini_Runtime/mini_bdx_runtime/mini_bdx_runtime






python /home/duck/Open_Duck_Mini_Runtime/scripts/v2_rl_walk_mujoco.py \
--onnx_model_path /home/duck/Open_Duck_Mini_Runtime/BEST_WALK_ONNX_2.onnx \
--duck_config_path /home/duck/duck_config.json \
--pitch_bias 5.0 \
--action_scale 0.2 \
--p 20 -i 5 -d 1 \
--commands

python /home/duck/Open_Duck_Mini_Runtime/scripts/v2_rl_walk_mujoco.py --onnx_model_path /home/duck/Open_Duck_Mini_Runtime/BEST_WALK_ONNX_2.onnx --duck_config_path /home/duck/duck_config.json --pitch_bias 5.0 --action_scale 0.2 

python /home/duck/Open_Duck_Mini_Runtime/scripts/v2_rl_walk_mujoco.py --onnx_model_path /home/duck/Open_Duck_Mini_Runtime/BEST_WALK_ONNX_2.onnx --duck_config_path /home/duck/duck_config.json --action_scale 0.2

✅ 方案 1：加入遥控器控制走路指令（最推荐）
你已经传入了 --commands，但如果你不操作 Xbox，策略就会在原地走。
你应该通过遥控器摇杆指令控制其前进。