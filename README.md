# Open Duck Mini Runtime

## Raspberry Pi zero 2W setup

### Install Raspberry Pi OS

Download Raspberry Pi OS Lite (64-bit) from here : https://www.raspberrypi.com/software/operating-systems/

Follow the instructions here to install the OS on the SD card : https://www.raspberrypi.com/documentation/computers/getting-started.html

With the Raspberry Pi Imager, you can pre-configure session, wifi and ssh. Do it like below :

![imager_setup](https://github.com/user-attachments/assets/7a4987b2-de83-41dd-ab7f-585259685f16)

> Tip: I configure the rasp to connect to my phone's hotspot, this way I can connect to it from anywhere.

### Setup SSH (If not setup during the installation)

When first booting on the rasp, you will need to connect a screen and a keyboard. The first thing you should do is connect to a wifi network and enable SSH.

To do so, you can follow this guide : https://www.raspberrypi.com/documentation/computers/configuration.html#setting-up-wifi

Then, you can connect to your rasp using SSH without having to plug a screen and a keyboard.

### Update the system and install necessary stuff

```bash
sudo apt update
sudo apt upgrade
sudo apt install git
sudo apt install python3-pip
sudo apt install python3-virtualenvwrapper
(optional) sudo apt install python3-picamzero

```

Add this to the end of the `.bashrc`:

```bash
export WORKON_HOME=$HOME/.virtualenvs
export PROJECT_HOME=$HOME/Devel
source /usr/share/virtualenvwrapper/virtualenvwrapper.sh
```

### Enable I2C

`sudo raspi-config` -> `Interface Options` -> `I2C`

TODO set 400KHz ?

### Set the usbserial latency timer

```bash
cd  /etc/udev/rules.d/
sudo touch 99-usb-serial.rules
sudo nano 99-usb-serial.rules
# copy the following line in the file
SUBSYSTEM=="usb-serial", DRIVER=="ftdi_sio", ATTR{latency_timer}="1"
```

### Set the udev rules for the motor control board

TODO


### Setup xbox one controller over bluetooth

Turn your xbox one controller on and set it in pairing mode by long pressing the sync button on the top of the controller.

Run the following commands on the rasp :

```bash
bluetoothctl
scan on
```

Wait for the controller to appear in the list, then run :

```bash
pair <controller_mac_address>
trust <controller_mac_address>
connect <controller_mac_address>
```

The led on the controller should stop blinking and stay on.

You can test that it's working by running

```bash
python3 mini_bdx_runtime/mini_bdx_runtime/xbox_controller.py
```

## Speaker wiring and configuration
Follow this tutorial

> For now, don't activate `/dev/zero` when they ask

https://learn.adafruit.com/adafruit-max98357-i2s-class-d-mono-amp?view=all


## Install the runtime

### Make a virtual environment and activate it

```bash
mkvirtualenv -p python3 open-duck-mini-runtime
workon open-duck-mini-runtime
```

Clone this repository on your rasp, cd into the repo, then :

```bash
git clone https://github.com/apirrone/Open_Duck_Mini_Runtime
cd Open_Duck_Mini_Runtime
git checkout v2
pip install -e .
```


## Test the IMU

```bash
python3 mini_bdx_runtime/mini_bdx_runtime/raw_imu.py
```

You can also run `python3 scripts/imu_server.py` on the robot and `python3 scripts/imu_client.py --ip <robot_ip>` on your computer to check that the frame is oriented correctly. 

> To find the ip address of the robot, run `ifconfig` on the robot

## Test motors

This will allow you to verify all your motors are connected and configured.

```bash
python3 scripts/check_motors.py
```

## Make your duck_config.json

Copy `example_config.json` in the home directory of your duck and rename it `duck_config.json`.

`cp example_config.json ~/duck_config.json`

In this file, you can configure some stuff, like registering if you installed the expression features, installed the imu upside down or and other stuff. You also write the joints offsets of your duck here

## Find the joints offsets

This script will guide you through finding the joints offsets of your robot that you can then write in your `duck_config.json`

> This procedure won't be necessary in the future as we will be flashing the offsets directly in each motor's eeprom.

```bash
cd scripts/
python find_soft_offsets.py
```

## Run the walk !

Download the [latest policy checkpoint ](https://github.com/apirrone/Open_Duck_Mini/blob/v2/BEST_WALK_ONNX_2.onnx) and copy it to your duck.

### Deployment workflow

The intended workflow is local development on Windows and remote execution on the duck:

1. Edit code locally in `D:\open_duck_mini_runtime`.
2. Sync the local checkout to `duck@192.168.0.34:/home/duck/open_duck_mini_runtime`.
3. Build, run, and inspect logs on the duck.
4. Commit and push the local branch to GitHub when the remote run is verified.

### Local Windows setup

```powershell
cd D:\open_duck_mini_runtime
python -m pip install -r requirements.txt
```

The deployment helper prompts for the SSH password when `DUCK_PASSWORD` is not set. For one shell session only, you can also set it as an environment variable.

```powershell
$env:DUCK_HOST="192.168.0.34"
$env:DUCK_USER="duck"
python scripts\duck_remote.py check
```

### Remote duck setup

Run this on the duck after cloning or syncing the repository:

```bash
cd ~/open_duck_mini_runtime
python3 -m venv ~/.venv
~/.venv/bin/python -m pip install --upgrade pip
~/.venv/bin/python -m pip install -r requirements.txt
~/.venv/bin/python -m pip install -e . --no-deps
```

If the duck cannot reach PyPI reliably, download the matching `rustypot==1.4.2` wheel on a machine with internet access and install it on the duck before installing the project:

```bash
~/.venv/bin/python -m pip install /tmp/rustypot-1.4.2-cp313-cp313-manylinux_2_24_aarch64.whl
~/.venv/bin/python -m pip install -e . --no-deps
```

### Remote commands from Windows

Check hardware and current logs:

```powershell
python scripts\duck_remote.py check
python scripts\duck_remote.py status
```

Sync only:

```powershell
python scripts\duck_remote.py sync
```

Start with the conservative default profile:

```powershell
python scripts\duck_remote.py start
```

This expands to:

```bash
/home/duck/.venv/bin/python -u scripts/v2_rl_walk_mujoco.py \
  --onnx_model_path BEST_WALK_ONNX_2.onnx \
  --duck_config_path /home/duck/open_duck_mini_runtime/duck_config.json \
  --commands -c 50 -p 22 -d 0 --action_scale 0.2 --min_motor_voltage 6.8
```

Use a headless startup check when no Xbox controller is connected:

```powershell
python scripts\duck_remote.py start --no-commands
```

Watch logs and stop safely:

```powershell
python scripts\duck_remote.py log
python scripts\duck_remote.py stop
```

`stop` terminates the walk process and sends a torque-off command to all configured servos.

### Power safety

The default run profile is intentionally conservative:

```powershell
python scripts\duck_remote.py start --kp 22 --action-scale 0.2 --min-motor-voltage 6.8
```

The runtime checks servo bus voltage once per second. If the minimum servo voltage drops below `6.8V`, it turns off torque and exits instead of pulling the battery or power board into a hard reset.

Check the current servo bus voltage with the same `rustypot` backend used by the walk runtime:

```bash
cd ~/open_duck_mini_runtime
source ~/.venv/bin/activate
python3 scripts/check_voltage.py
```

If the static minimum voltage is near `7.1V`, the battery may sag below `6.8V` while walking and the runtime will safely turn torque off.

To log voltage and servo current while walking, add `--power_log_interval`:

```bash
python -u scripts/v2_rl_walk_mujoco.py \
  --onnx_model_path BEST_WALK_ONNX_2.onnx \
  --duck_config_path ~/open_duck_mini_runtime/duck_config.json \
  --commands -c 50 -p 22 -d 0 --action_scale 0.2 \
  --min_motor_voltage 6.8 --power_log_interval 1.0
```

The current values are converted from STS3215 feedback raw units using 6.5mA per unit.

### Directly on the duck

```bash
cd ~/open_duck_mini_runtime
source ~/.venv/bin/activate
python -u scripts/v2_rl_walk_mujoco.py \
  --onnx_model_path BEST_WALK_ONNX_2.onnx \
  --duck_config_path ~/open_duck_mini_runtime/duck_config.json \
  --commands -c 50 -p 22 -d 0 --action_scale 0.2 --min_motor_voltage 6.8
```

### Keyboard control on the duck

Keyboard control must run in the foreground inside an interactive SSH session. Do not start it with `nohup` or `duck_remote.py start`, because background processes cannot receive keyboard input.

```bash
cd ~/open_duck_mini_runtime
source ~/.venv/bin/activate
python -u scripts/v2_rl_walk_mujoco.py \
  --onnx_model_path BEST_WALK_ONNX_2.onnx \
  --duck_config_path ~/open_duck_mini_runtime/duck_config.json \
  --commands --command_source keyboard \
  -c 50 -p 22 -d 0 --action_scale 0.2 \
  --min_motor_voltage 6.5 --power_log_interval 1.0
```

Keyboard mapping:

```text
P       pause/unpause, replaces Xbox A
W/S     forward/backward
A/D     strafe left/right
Q/E     yaw left/right
Space   zero all commands
H       toggle head control mode
U/J     phase frequency +/-
Ctrl+C  stop and turn torque off
```



```
- The commands are : 
- A to pause/unpause
- X to turn on/off the projector
- B to play a random sound
- Y to turn on/off head control (very experimental, I don't recommend trying that, it can break your duck's head)
- left and right triggers to control the left and right antennas
- LB (new!) press and hold to increase the walking frequency, kind of a sprint mode 🙂
```

## Standing Policy

A standing-only policy trained with PPO (MuJoCo MJX) on RTX 5090 D. The model keeps the duck balanced upright with no locomotion commands.

### Training (WSL with GPU)

The training code lives in [open_duck_playground](https://github.com/apirrone/Open_Duck_Playground). Clone and set up the environment:

\\ash
# In WSL (Ubuntu 22.04)
cd ~/work/open_duck_playground
source ~/.venvs/open_duck_playground_py310/bin/activate
\
Train the standing policy (300M steps, ~30 min on RTX 5090 D):

\\ash
TF_CPP_MIN_LOG_LEVEL=3 TF_ENABLE_ONEDNN_OPTS=0 \
python playground/open_duck_mini_v2/runner.py \
  --env standing \
  --num_timesteps 300000000 \
  --output_dir checkpoints_standing_300m
\
Training parameters:

- **PPO config**: BerkeleyHumanoidJoystickFlatTerrain
- **Obs dim**: 85 (gyro 3 + accel 3 + command 7 + joints 14 + vel 14 + 3xhistory 42 + contacts 2)
- **Action dim**: 14 (14 actuator joints)
- **action_scale**: 0.15
- **Network**: 85->512->256->128->28 (SiLU activations, 14 action + 14 value heads)
- **normalize_observations**: True (baked into ONNX)
- **sim_dt / ctrl_dt**: 0.002 / 0.02 (50 Hz control)
- **num_envs**: 8192
- **episode_length**: 1000 steps (20s)

The last ONNX checkpoint in \checkpoints_standing_300m/\ is the trained model.

### Deployment on the duck

The trained model is saved as \BEST_STANDING_ONNX.onnx\ in this repo. A dedicated launch script is provided:

\\ash
cd ~/open_duck_mini_runtime
source ~/.venv/bin/activate

# Default: uses BEST_STANDING_ONNX.onnx, action_scale=0.15, 50Hz
python -u scripts/v2_rl_standing.py
\
Or using the generic walk script with \--policy_mode standing\:

\\ash
python -u scripts/v2_rl_walk_mujoco.py \
  --onnx_model_path BEST_STANDING_ONNX.onnx \
  --policy_mode standing \
  --duck_config_path ~/duck_config.json \
  --action_scale 0.15 \
  -c 50 -p 22 -d 0 --min_motor_voltage 6.8
\
### Standing vs Walking modes

| | Standing | Walking |
|---|---------|--------|
| Obs dim | 85 | 101 |
| ONNX model | BEST_STANDING_ONNX.onnx | BEST_WALK_ONNX_2.onnx |
| action_scale | 0.15 | 0.25 (train) / 0.2 (deploy) |
| Command input | All zeros (no locomotion) | lin_x, lin_y, yaw + head angles |
| PRM / imitation | Disabled | Enabled |

### Evaluation results (MuJoCo simulation)

- **Training steps**: 321M
- **Survival rate (40s episodes)**: 100% (20/20)
- **Mean standing height**: 0.1585m
- **Height stability (std)**: +/-0.0005m
- **Max push tolerance (100% survival)**: 0.3 m/s (0.63 Ns impulse)
- **Robot mass**: 2.107 kg
