"""重依赖隔离: import lubanagent.core 不得加载 DB/HTTP/LangChain。"""

import subprocess
import sys
from pathlib import Path

HEAVY_DEPS = {"sqlalchemy", "fastapi", "langchain", "langchain_core", "httpx"}


def test_core_import_does_not_load_heavy_deps(
    tmp_path: Path, isolated_subprocess_env: dict[str, str]
) -> None:
    script = (
        "import sys, lubanagent.core; "
        f"leaked = [m for m in sys.modules if m.split('.')[0] in {HEAVY_DEPS!r}]; "
        "assert not leaked, f'重依赖泄漏: {leaked}'"
    )
    subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        cwd=tmp_path,
        env=isolated_subprocess_env,
    )


def test_core_modules_have_no_forbidden_imports() -> None:
    import importlib
    import pkgutil

    import lubanagent.core as core_pkg

    for info in pkgutil.iter_modules(core_pkg.__path__):
        module = importlib.import_module(f"{core_pkg.__name__}.{info.name}")
        for name in dir(module):
            value = getattr(module, name)
            top = getattr(value, "__module__", "") or ""
            assert top.split(".")[0] not in HEAVY_DEPS, (
                f"{module.__name__}.{name} 来自重依赖 {top}"
            )
