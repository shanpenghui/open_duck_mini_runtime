
import os

__path__ = [os.path.join(os.path.dirname(__file__), "mini_bdx_runtime")]
__all__ = ["OnnxInfer"]


def __getattr__(name):
    if name == "OnnxInfer":
        from .onnx_infer import OnnxInfer

        return OnnxInfer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
