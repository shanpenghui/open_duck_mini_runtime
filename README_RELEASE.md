# Open Duck Mini Clean Release

This is the cleaned runtime package with the compiled walking entry.

- Plaintext `BEST_WALK_ONNX_2.onnx` is not included.
- `BEST_WALK_ONNX_2.onnx.duckenc` and `license.duck` are included.
- Core authorization, secure asset, and ONNX loading modules are included as Nuitka `.so` files.
- `scripts/v2_rl_walk_mujoco.py` is not included; `scripts/v2_rl_walk_mujoco.bin` is used.
- Nuitka build directories, tools, tests, and old notes are not included.

Use `./run_duck.sh --help` to inspect commands. Start only when hardware is ready.
