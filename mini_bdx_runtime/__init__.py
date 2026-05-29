from pathlib import Path


_nested_package_dir = Path(__file__).resolve().parent / "mini_bdx_runtime"
if _nested_package_dir.is_dir():
    __path__.append(str(_nested_package_dir))
