# Open Duck Mini Runtime

## Robot Runtime Helper

On the robot, use the local helper script instead of typing the full runtime
command each time:

```bash
cd ~
./run_duck.sh check
./run_duck.sh start
./run_duck.sh status
./run_duck.sh log
./run_duck.sh stop
```

The same script is also installed at:

```bash
~/open_duck_mini_runtime/run_duck.sh
```

Commands:

```text
start             Start with Xbox controller commands enabled.
start-headless    Start without Xbox controller commands.
start-foreground  Start in the foreground for systemd.
stop              Stop the runtime and turn motor torque off.
restart           Stop, then start.
status            Show process status and recent logs.
log               Follow /tmp/duck.log.
check             Check files, hardware nodes, and Xbox pairing status.
voltage           Read servo bus voltage/current.
```

The helper defaults to:

```text
control_freq=50
kp=22
kd=0
action_scale=0.2
min_motor_voltage=6.3
power_log_interval=1.0
```

When `start` or `start-foreground` uses Xbox commands, the helper waits for
`/dev/input/js0` instead of exiting immediately. By default it waits forever,
which is useful for boot-time startup. The wait behavior can be adjusted with:

```bash
DUCK_WAIT_CONTROLLER=1 DUCK_WAIT_CONTROLLER_TIMEOUT=0 ./run_duck.sh start
```

Set `DUCK_WAIT_CONTROLLER_TIMEOUT` to a positive number of seconds if startup
should fail after a fixed wait.

Before starting, `check` should show the Xbox controller as paired, bonded,
trusted, connected, and available as `/dev/input/js0`:

```text
[OK] joystick: /dev/input/js0
Paired: yes
Bonded: yes
Trusted: yes
Connected: yes
```

If Bluetooth says `Connected: yes` but `/dev/input/js0` is missing, remove and
pair the controller again. `Connected: yes` alone is not enough; the controller
must also be paired and bonded before Linux registers it as an input device.

The `stop` command disables torque directly through `rustypot`, without loading
the walking policy or ONNX runtime.

### Autostart with systemd

The repository includes a systemd service template:

```bash
deploy/open-duck-runtime.service
```

When enabled, `open-duck-runtime.service` starts the Open Duck Mini walking
runtime automatically after boot. The service runs as user `duck`, uses
`/home/duck/open_duck_mini_runtime` as its working directory, and launches the
runtime through:

```bash
/home/duck/open_duck_mini_runtime/run_duck.sh start-foreground
```

The foreground mode lets systemd track the Python process directly. The walking
command is equivalent to:

```bash
cd ~/open_duck_mini_runtime
source ~/.venv/bin/activate
python -u scripts/v2_rl_walk_mujoco.py \
  --onnx_model_path BEST_WALK_ONNX_2.onnx \
  --duck_config_path ~/open_duck_mini_runtime/duck_config.json \
  --commands -c 50 -p 22 -d 0 \
  --action_scale 0.2 \
  --min_motor_voltage 6.3
```

Before the walking policy starts, `run_duck.sh` performs preflight checks. If a
required item is missing, the runtime is not started and a clear error is
printed to the service log.

Preflight checks:

- Required files:
  - `BEST_WALK_ONNX_2.onnx`
  - `duck_config.json`
  - `~/.venv/bin/python`
  - `scripts/v2_rl_walk_mujoco.py`
- BNO055 IMU:
  - `/dev/i2c-1` must exist.
  - I2C address `0x28` must be visible with `i2cdetect -y 1`.
- Xbox controller:
  - Default Bluetooth address: `91:B4:9E:A2:3C:ED`.
  - `bluetoothctl info` must report `Connected: yes`.
  - `pygame` must detect at least one joystick.
  - `/dev/input/js0` must exist.
- Servo bus:
  - `/dev/ttyACM0` should exist.

Install and enable autostart on the robot:

```bash
cd ~/open_duck_mini_runtime
sudo cp deploy/open-duck-runtime.service /etc/systemd/system/open-duck-runtime.service
sudo systemctl daemon-reload
sudo systemctl enable open-duck-runtime.service
```

Run the preflight check before enabling or rebooting:

```bash
./run_duck.sh check
```

Check service status:

```bash
sudo systemctl status open-duck-runtime.service --no-pager
```

Follow runtime logs:

```bash
journalctl -u open-duck-runtime.service -f
```

Start or stop the service manually:

```bash
sudo systemctl start open-duck-runtime.service
sudo systemctl stop open-duck-runtime.service
```

Disable boot autostart:

```bash
sudo systemctl disable open-duck-runtime.service
```


### Xbox button 15 safe poweroff

During runtime, holding Xbox controller button 15 for 7 seconds requests a safe
Raspberry Pi shutdown. This is handled from the main control loop instead of the
joystick worker thread.

Shutdown sequence:

1. The runtime prints a `[SHUTDOWN]` log message.
2. The Xbox Bluetooth controller is disconnected with `bluetoothctl disconnect`.
3. The control loop exits normally.
4. The `finally` block calls `hwi.turn_off()` to disable motor torque.
5. The runtime runs `sudo -n systemctl poweroff`.

The poweroff command requires a minimal sudoers rule:

```text
duck ALL=(root) NOPASSWD: /usr/bin/systemctl poweroff
```

Install it as `/etc/sudoers.d/duck-poweroff` and validate it with:

```bash
sudo visudo -cf /etc/sudoers.d/duck-poweroff
```

Useful environment overrides supported by `run_duck.sh` and the service:

- `DUCK_XBOX_ADDRESS`
- `DUCK_MIN_MOTOR_VOLTAGE`
- `DUCK_WAIT_CONTROLLER`
- `DUCK_WAIT_CONTROLLER_TIMEOUT`
- `DUCK_CONTROL_FREQ`
- `DUCK_KP`
- `DUCK_KD`
- `DUCK_ACTION_SCALE`


## Camera V2 Arrow Vision Control

This branch adds Raspberry Pi Camera V2 arrow-recognition helpers for the
Open Duck Mini walking runtime. The scripts are designed for the Pi-side virtual
environment at `~/.venv`.

Vision-only test, without moving the robot:

```bash
cd ~/open_duck_mini_runtime
source ~/.venv/bin/activate
./scripts/run_arrow_open_duck_runtime.sh --vision-only --threshold-mode dark --roi none --min-area 80 --sample-rotation-deg 35 --confidence 0.55
```

The detector uses real sample arrows from `assets/arrow_samples/` and prints a
JSON line for each stable result, for example:

```json
{"arrow":"left","confidence":0.9751,"raw_arrow":"left","raw_confidence":0.9606,"state":"strafe_left","key":"a"}
```

Full arrow-controlled walking:

```bash
cd ~/open_duck_mini_runtime
source ~/.venv/bin/activate
./scripts/run_arrow_open_duck_runtime.sh --threshold-mode dark --roi none --min-area 80 --sample-rotation-deg 35 --confidence 0.55
```

Arrow mapping:

```text
forward -> W, walk forward
back    -> E, turn right
left    -> A, strafe left
right   -> D, strafe right
none    -> SPACE, hold still
```

Xbox manual control with arrow turn assist:

```bash
cd ~/open_duck_mini_runtime
source ~/.venv/bin/activate
./scripts/run_arrow_xbox_assist_runtime.sh --threshold-mode dark --roi none --min-area 80 --sample-rotation-deg 35 --confidence 0.55
```

In this mode, Xbox remains the main controller. Press Xbox `A` to toggle
pause/running. When the camera sees a stable side arrow while running, the
wrapper turns once and pauses:

```text
left arrow  -> Q for about 1.55 seconds -> pause
right arrow -> E for about 1.55 seconds -> pause
```

Tune the physical 90-degree turn with `--turn-duration-s`.

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
  --commands -c 50 -p 22 -d 0 --action_scale 0.2 --min_motor_voltage 6.3
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
  --min_motor_voltage 6.3 --power_log_interval 1.0
```

The current values are converted from STS3215 feedback raw units using 6.5mA per unit.

### Directly on the duck

```bash
cd ~/open_duck_mini_runtime
source ~/.venv/bin/activate
python -u scripts/v2_rl_walk_mujoco.py \
  --onnx_model_path BEST_WALK_ONNX_2.onnx \
  --duck_config_path ~/open_duck_mini_runtime/duck_config.json \
  --commands -c 50 -p 22 -d 0 --action_scale 0.2 --min_motor_voltage 6.3
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
  --min_motor_voltage 6.3 --power_log_interval 1.0
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
