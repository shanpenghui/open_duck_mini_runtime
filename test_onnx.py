import onnxruntime as ort

# 替换为你的 ONNX 文件路径
onnx_path = "BEST_WALK_ONNX_2.onnx"
session = ort.InferenceSession(onnx_path)

# 打印模型输入输出名和形状
print("Inputs:")
for inp in session.get_inputs():
    print(f"{inp.name}: shape={inp.shape}, dtype={inp.type}")

print("\nOutputs:")
for out in session.get_outputs():
    print(f"{out.name}: shape={out.shape}, dtype={out.type}")
