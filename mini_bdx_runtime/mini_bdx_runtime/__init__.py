__all__ = ["OnnxInfer"]


def __getattr__(name):
    if name == "OnnxInfer":
        from .onnx_infer import OnnxInfer

        return OnnxInfer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
