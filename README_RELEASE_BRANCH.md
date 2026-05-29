# Open Duck Mini Encrypted Runtime Release

This branch contains the cleaned runtime handoff package generated from `/home/duck/open_duck_mini_runtime_release_entry_clean`.

Included:
- Compiled walking entry: `scripts/v2_rl_walk_mujoco.bin`
- Encrypted policy model: `BEST_WALK_ONNX_2.onnx.duckenc`
- Machine-bound license: `license.duck`
- Core authorization / encrypted asset / ONNX loading modules as Nuitka `.so` files

Not included:
- Plaintext `BEST_WALK_ONNX_2.onnx`
- Plaintext `scripts/v2_rl_walk_mujoco.py`
- Build tools and Nuitka intermediate build directories
