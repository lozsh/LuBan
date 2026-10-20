"""Agent 虚拟形象文件管理：上传校验、落盘、读取、删除。

只做展示用途（SVG/PNG 图片与 GLB/GLTF 3D 模型），不进运行期逻辑。
落盘布局：files_root/{agent_key}/avatar.{ext}，每 Agent 一个目录（同知识源模式）。
"""

import asyncio
import shutil
from pathlib import Path

from lubanagent.core import ErrorCode, LubanError

MAX_AVATAR_BYTES = 20 * 1024 * 1024
SUPPORTED_SUFFIXES = frozenset({".svg", ".png", ".glb", ".gltf"})

# Content-Type 按后缀映射（GET /v1/agents/{key}/avatar 响应用）
AVATAR_MEDIA_TYPES: dict[str, str] = {
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".glb": "model/gltf-binary",
    ".gltf": "model/gltf+json",
}


class AvatarManager:
    """形象文件存取；由工厂挂 app.state.avatar_manager，路由层经 state 取用。"""

    def __init__(self, files_root: Path) -> None:
        self._files_root = files_root

    def validate(self, filename: str, data: bytes) -> str:
        """校验后缀白名单 + 大小上限；返回净化后的落盘文件名（avatar.{ext}）。"""
        suffix = Path(filename).suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            raise LubanError(
                ErrorCode.VALIDATION_ERROR,
                f"不支持的形象类型: {filename}",
                details={"filename": filename, "supported": sorted(SUPPORTED_SUFFIXES)},
            )
        if len(data) > MAX_AVATAR_BYTES:
            raise LubanError(
                ErrorCode.VALIDATION_ERROR,
                f"形象文件超过上限 {MAX_AVATAR_BYTES} 字节",
                details={"filename": filename, "size": len(data), "max": MAX_AVATAR_BYTES},
            )
        # 统一存 avatar.{ext}：每 Agent 单形象，换类型直接覆盖旧扩展名的文件
        return f"avatar{suffix}"

    async def save(self, agent_key: str, filename: str, data: bytes) -> str:
        """校验 + 落盘 files_root/{agent_key}/avatar.{ext}；返回落盘文件名。"""
        stored = self.validate(filename, data)
        agent_dir = self._agent_dir(agent_key)
        await asyncio.to_thread(agent_dir.mkdir, parents=True, exist_ok=True)
        # 换类型时清掉旧扩展名文件（avatar.png → avatar.glb）
        for old in agent_dir.glob("avatar.*"):
            if old.name != stored:
                await asyncio.to_thread(old.unlink)
        await asyncio.to_thread((agent_dir / stored).write_bytes, data)
        return stored

    async def get_path(self, agent_key: str, filename: str) -> Path:
        """形象文件绝对路径；不存在抛 AVATAR 相关 VALIDATION_ERROR（路由层转 404）。"""
        path = (self._agent_dir(agent_key) / Path(filename).name).resolve()
        if not path.is_relative_to(self._files_root.resolve()) or not path.is_file():
            raise LubanError(
                ErrorCode.VALIDATION_ERROR,
                f"形象文件不存在: {agent_key}/{filename}",
                details={"agent_key": agent_key, "filename": filename},
            )
        return path

    async def delete(self, agent_key: str) -> None:
        """删除该 Agent 的整个形象目录（幂等，目录不存在也成功）。"""
        agent_dir = self._agent_dir(agent_key)
        await asyncio.to_thread(shutil.rmtree, agent_dir, True)

    def _agent_dir(self, agent_key: str) -> Path:
        # agent_key 由路由层传入（库内 key，无路径字符）；resolve 后仍校验在 root 内（双保险）
        agent_dir = (self._files_root / agent_key).resolve()
        if not agent_dir.is_relative_to(self._files_root.resolve()):
            raise LubanError(
                ErrorCode.VALIDATION_ERROR,
                f"非法 agent key: {agent_key}",
                details={"agent_key": agent_key},
            )
        return agent_dir
