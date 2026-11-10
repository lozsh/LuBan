"""访问控制仓储：只存密码哈希与会话哈希，返回 dict 视图。"""

import asyncio
import hashlib
import hmac
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from lubanagent.core import ErrorCode, LubanError
from lubanagent.persistence.models.security import (
    AuthSessionRow,
    PermissionRow,
    RolePermissionRow,
    RoleRow,
    UserRoleRow,
    UserRow,
)

_USERNAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")
_ROLE_KEY = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_PERMISSION_KEY = re.compile(r"^[a-z]+:[a-z_]+$")
_MIN_PASSWORD = 12
_MAX_PASSWORD = 256
_MAX_ROLE_KEYS = 20
_MAX_PERMISSIONS = 20

ALL_PERMISSIONS: tuple[tuple[str, str], ...] = (
    ("config:read", "读取配置"),
    ("config:write", "修改配置"),
    ("run:read", "查看运行"),
    ("run:execute", "执行 Agent"),
    ("run:cancel", "取消运行"),
    ("run:approve", "审批工具"),
    ("diagnostics:read", "查看排查数据"),
    ("eval:read", "查看评测"),
    ("eval:execute", "执行评测"),
    ("security:manage", "管理账号与角色"),
)
DEFAULT_ROLES: dict[str, tuple[str, tuple[str, ...], bool]] = {
    "admin": ("管理员", tuple(key for key, _ in ALL_PERMISSIONS), True),
    "operator": (
        "操作员",
        (
            "config:read",
            "config:write",
            "run:read",
            "run:execute",
            "run:cancel",
            "run:approve",
            "diagnostics:read",
            "eval:read",
            "eval:execute",
        ),
        False,
    ),
    "auditor": ("审计员", ("config:read", "run:read", "diagnostics:read", "eval:read"), False),
    "viewer": ("观察者", ("config:read", "run:read"), False),
}


def hash_password(password: str) -> str:
    """stdlib scrypt；随机盐与参数随哈希一起存储。"""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt$16384$8$1${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, rounds, block_size, parallel, salt_hex, digest_hex = stored.split("$")
        if algorithm != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode(),
            salt=bytes.fromhex(salt_hex),
            n=int(rounds),
            r=int(block_size),
            p=int(parallel),
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


_DUMMY_HASH: str | None = None


def _timing_safe_dummy_hash() -> str:
    """未知用户名也执行同代价 scrypt 校验，避免登录耗时泄露账号存在性。"""
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password("luban-timing-safe-dummy-password")
    return _DUMMY_HASH


class SecurityRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def seed(self) -> None:
        for key, name in ALL_PERMISSIONS:
            if await self._session.get(PermissionRow, key) is None:
                self._session.add(PermissionRow(key=key, name=name))
        for key, (name, permissions, protected) in DEFAULT_ROLES.items():
            role = await self._session.get(RoleRow, key)
            is_new = role is None
            if role is None:
                self._session.add(RoleRow(key=key, name=name, protected=protected))
            # 幂等种子只补缺失；不改已有角色名，也不把管理员裁掉的
            # operator 权限在下一次登录时自动加回。唯一例外是受保护的
            # admin：作为 bootstrap 破窗角色，始终补齐全量权限。
            if is_new or protected:
                for permission in permissions:
                    await self._session.merge(
                        RolePermissionRow(role_key=key, permission_key=permission)
                    )
        await self._session.flush()

    async def seed_admin(self, *, username: str, password: str) -> None:
        await self.seed()
        if await self.get_user_by_username(username) is not None:
            return
        user = await self.create_user(
            username=username,
            display_name="管理员",
            password=password,
            role_keys=["admin"],
            bootstrap=True,
        )
        await self._session.merge(UserRoleRow(user_id=user["id"], role_key="admin"))

    async def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        row = await self._session.scalar(sa.select(UserRow).where(UserRow.username == username))
        return await self._user_dict(row) if row else None

    async def get_user(self, user_id: str) -> dict[str, Any] | None:
        row = await self._session.get(UserRow, user_id)
        return await self._user_dict(row) if row else None

    async def list_users(self) -> list[dict[str, Any]]:
        rows = (
            await self._session.scalars(sa.select(UserRow).order_by(UserRow.created_at))
        ).all()
        return [await self._user_dict(row) for row in rows]

    async def create_user(
        self,
        *,
        username: str,
        display_name: str,
        password: str,
        role_keys: list[str],
        bootstrap: bool = False,
        enabled: bool = True,
    ) -> dict[str, Any]:
        self._validate_username(username)
        self._validate_password(password)
        await self._validate_role_keys(role_keys)
        if await self.get_user_by_username(username) is not None:
            raise LubanError(ErrorCode.VALIDATION_ERROR, "用户名已存在")
        await self._assert_roles(role_keys)
        password_hash = await self._hash_password(password)
        user_id = uuid.uuid4().hex
        self._session.add(
            UserRow(
                id=user_id,
                username=username,
                display_name=display_name,
                password_hash=password_hash,
                enabled=enabled,
                bootstrap=bootstrap,
            )
        )
        await self._replace_roles(user_id, role_keys)
        await self._session.flush()
        return await self.get_user(user_id)  # type: ignore[return-value]

    async def update_user(
        self,
        user_id: str,
        *,
        display_name: str | None = None,
        enabled: bool | None = None,
        role_keys: list[str] | None = None,
        password: str | None = None,
    ) -> dict[str, Any]:
        row = await self._session.get(UserRow, user_id)
        if row is None:
            raise LubanError(ErrorCode.VALIDATION_ERROR, "用户不存在")
        # 只要传了角色数组就全量校验格式与存在性，防止借 admin 携带
        # 非法角色绕过校验写入坏数据。
        if role_keys is not None:
            await self._validate_role_keys(role_keys)
            await self._assert_roles(role_keys)
        demotes_admin = role_keys is not None and "admin" not in role_keys
        needs_admin_lock = demotes_admin or enabled is False
        if needs_admin_lock:
            # 统一锁序 admin → user：先公共 admin 哨兵，再 locking-read 全部
            # 活跃管理员行（FOR UPDATE 已含目标管理员行）。若先锁 user 再拿
            # admin，两个管理员并发操作不同账号会形成 userA→admin /
            # admin→userB 环；统一顺序后互不交叉。MySQL 同样避开目标表
            # 自读子查询（1093）；SQLite 从哨兵 UPDATE 起持有写锁。
            await self._lock_admin_role()
            active_admins = await self._active_admin_ids()
            if row.id in active_admins and len(active_admins) <= 1:
                raise LubanError(ErrorCode.VALIDATION_ERROR, "不能停用或移除最后一个管理员")
        elif password is not None:
            # 纯改密路径只锁本行、不触碰 admin 锁，与 login（只锁本行）安全
            # 串行：改密先提交则 login 的 post-verify recheck 看到新 hash 拒绝；
            # login 先提交则改密的 revoke_user_sessions 撤销其新会话。
            row = await self._lock_user_row(user_id) or row
        if enabled is False:
            row.enabled = False
            await self.revoke_user_sessions(user_id)
        if display_name is not None:
            row.display_name = display_name
        if enabled is not None:
            row.enabled = enabled
        if password is not None:
            self._validate_password(password)
            row.password_hash = await self._hash_password(password)
            await self.revoke_user_sessions(user_id)
        if role_keys is not None:
            await self._replace_roles(user_id, role_keys)
        await self._session.flush()
        await self._session.refresh(row)
        return await self.get_user(user_id)  # type: ignore[return-value]

    async def list_roles(self) -> list[dict[str, Any]]:
        rows = (await self._session.scalars(sa.select(RoleRow).order_by(RoleRow.key))).all()
        return [await self._role_dict(row) for row in rows]

    async def create_role(
        self, *, key: str, name: str, permissions: list[str]
    ) -> dict[str, Any]:
        await self._assert_permissions(permissions)
        if not _ROLE_KEY.fullmatch(key):
            raise LubanError(ErrorCode.VALIDATION_ERROR, f"角色 key 不合法: {key}")
        if len(permissions) > _MAX_PERMISSIONS:
            raise LubanError(ErrorCode.VALIDATION_ERROR, "权限数量超过上限")
        await self._validate_permission_keys(permissions)
        if await self._session.get(RoleRow, key) is not None:
            raise LubanError(ErrorCode.VALIDATION_ERROR, "角色已存在")
        self._session.add(RoleRow(key=key, name=name))
        await self._replace_permissions(key, permissions)
        await self._session.flush()
        row = await self._session.get(RoleRow, key)
        return await self._role_dict(row)  # type: ignore[arg-type]

    async def update_role(
        self, key: str, *, name: str, permissions: list[str]
    ) -> dict[str, Any]:
        row = await self._session.get(RoleRow, key)
        if row is None:
            raise LubanError(ErrorCode.VALIDATION_ERROR, "角色不存在")
        await self._assert_permissions(permissions)
        if not _ROLE_KEY.fullmatch(key):
            raise LubanError(ErrorCode.VALIDATION_ERROR, f"角色 key 不合法: {key}")
        if key == "admin" and set(permissions) != {key for key, _ in ALL_PERMISSIONS}:
            raise LubanError(ErrorCode.VALIDATION_ERROR, "admin 必须拥有全部权限")
        row.name = name
        await self._replace_permissions(key, permissions)
        await self._session.flush()
        return await self._role_dict(row)

    async def delete_role(self, key: str) -> None:
        row = await self._session.get(RoleRow, key)
        if row is None:
            raise LubanError(ErrorCode.VALIDATION_ERROR, "角色不存在")
        if row.protected:
            raise LubanError(ErrorCode.VALIDATION_ERROR, "内置角色不可删除")
        await self._session.execute(
            sa.delete(RolePermissionRow).where(RolePermissionRow.role_key == key)
        )
        await self._session.execute(sa.delete(UserRoleRow).where(UserRoleRow.role_key == key))
        await self._session.delete(row)

    async def list_permissions(self) -> list[dict[str, str]]:
        rows = (
            await self._session.scalars(sa.select(PermissionRow).order_by(PermissionRow.key))
        ).all()
        return [{"key": row.key, "name": row.name} for row in rows]

    async def login(
        self, *, username: str, password: str, session_hours: int
    ) -> tuple[str, datetime, dict[str, Any]]:
        user = await self.get_user_by_username(username)
        # 不存在的用户名用固定 dummy 哈希跑同一遍 scrypt，与真实账号耗时一致。
        stored_hash = user["password_hash"] if user else _timing_safe_dummy_hash()
        valid = await self._verify(password, stored_hash)
        if not user or not valid:
            raise LubanError(ErrorCode.AUTH_FAILED, "用户名或密码错误")
        # TOCTOU 收口：scrypt 在线程里跑的间隙，管理员可能已改密/禁用并撤销
        # 全部会话。凭旧 hash 继续插新 session 会让改密后的新 token 逃过撤销。
        # 因此在同一事务内锁用户行（哨兵写 + locking read 取最新提交值），
        # enabled 与 password_hash 都和刚验证的值恒等才允许发新 token。
        current = await self._lock_user_row(user["id"])
        if current is None or not current.enabled or current.password_hash != stored_hash:
            raise LubanError(ErrorCode.AUTH_FAILED, "用户名或密码错误")
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(UTC) + timedelta(hours=session_hours)
        self._session.add(
            AuthSessionRow(
                token_hash=token_hash(token), user_id=user["id"], expires_at=expires_at
            )
        )
        await self._session.flush()
        return token, expires_at, {
            key: value for key, value in user.items() if key != "password_hash"
        }

    async def user_by_token(self, token: str) -> dict[str, Any] | None:
        now = datetime.now(UTC)
        row = await self._session.scalar(
            sa.select(AuthSessionRow).where(
                *self._active_session_filters(token, now)
            )
        )
        if row is None:
            return None
        user = await self.get_user(row.user_id)
        if not user or not user["enabled"]:
            return None
        return user

    async def revoke_token(self, token: str) -> bool:
        row = await self._session.get(AuthSessionRow, token_hash(token))
        if row is None or row.revoked_at is not None:
            return False
        row.revoked_at = datetime.now(UTC)
        await self._session.flush()
        return True

    async def revoke_user_sessions(self, user_id: str) -> None:
        await self._session.execute(
            sa.update(AuthSessionRow)
            .where(
                AuthSessionRow.user_id == user_id,
                AuthSessionRow.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(UTC))
        )

    async def _verify(self, password: str, stored_hash: str) -> bool:
        return await asyncio.to_thread(verify_password, password, stored_hash)

    async def _hash_password(self, password: str) -> str:
        return await asyncio.to_thread(hash_password, password)

    async def _lock_admin_role(self) -> None:
        """跨库公共写哨兵：MySQL 锁 admin 行；SQLite 获得事务写锁。

        所有移除/停用管理员路径都必须先经过这里，再 locking-read 最新状态。
        """
        await self._session.execute(
            sa.update(RoleRow)
            .where(RoleRow.key == "admin")
            .values(protected=RoleRow.protected)
        )
        await self._session.flush()

    async def _lock_user_row(self, user_id: str) -> UserRow | None:
        """跨库用户行写哨兵 + locking current-read，返回最新提交的行。

        enabled/updated_at 均显式自赋值：阻止 ORM onupdate 把每次 login
        的取锁误当作真实变更刷新 updated_at。
        """
        await self._session.execute(
            sa.update(UserRow)
            .where(UserRow.id == user_id)
            .values(enabled=UserRow.enabled, updated_at=UserRow.updated_at)
        )
        await self._session.flush()
        return await self._session.scalar(
            sa.select(UserRow)
            .where(UserRow.id == user_id)
            .execution_options(populate_existing=True)
            .with_for_update()
        )

    async def _active_admin_ids(self) -> set[str]:
        rows = await self._session.scalars(
            sa.select(UserRoleRow.user_id)
            .join(UserRow, UserRow.id == UserRoleRow.user_id)
            .where(UserRoleRow.role_key == "admin", UserRow.enabled.is_(True))
            .with_for_update()
        )
        return set(rows)

    async def _assert_roles(self, keys: list[str]) -> None:
        rows = await self._session.scalars(
            sa.select(RoleRow.key).where(RoleRow.key.in_(keys))
        )
        missing = set(keys) - set(rows)
        if missing:
            raise LubanError(ErrorCode.VALIDATION_ERROR, f"角色不存在: {sorted(missing)}")

    async def _assert_permissions(self, keys: list[str]) -> None:
        await self._validate_permission_keys(keys)
        rows = await self._session.scalars(
            sa.select(PermissionRow.key).where(PermissionRow.key.in_(keys))
        )
        missing = set(keys) - set(rows)
        if missing:
            raise LubanError(ErrorCode.VALIDATION_ERROR, f"权限不存在: {sorted(missing)}")

    def _validate_username(self, username: str) -> None:
        if not _USERNAME.fullmatch(username):
            raise LubanError(ErrorCode.VALIDATION_ERROR, "用户名格式不合法")

    def _validate_password(self, password: str) -> None:
        if not _MIN_PASSWORD <= len(password) <= _MAX_PASSWORD:
            raise LubanError(
                ErrorCode.VALIDATION_ERROR,
                f"密码长度必须在 {_MIN_PASSWORD} 到 {_MAX_PASSWORD} 位之间",
            )

    async def _validate_role_keys(self, keys: list[str]) -> None:
        if len(keys) > _MAX_ROLE_KEYS:
            raise LubanError(ErrorCode.VALIDATION_ERROR, "角色数量超过上限")
        for key in keys:
            if not _ROLE_KEY.fullmatch(key):
                raise LubanError(ErrorCode.VALIDATION_ERROR, f"角色 key 不合法: {key}")

    async def _validate_permission_keys(self, keys: list[str]) -> None:
        if len(keys) > _MAX_PERMISSIONS:
            raise LubanError(ErrorCode.VALIDATION_ERROR, "权限数量超过上限")
        for key in keys:
            if not _PERMISSION_KEY.fullmatch(key):
                raise LubanError(ErrorCode.VALIDATION_ERROR, f"权限 key 不合法: {key}")

    @staticmethod
    def _active_session_filters(token: str, now: datetime) -> list[Any]:
        return [
            AuthSessionRow.token_hash == token_hash(token),
            AuthSessionRow.revoked_at.is_(None),
            AuthSessionRow.expires_at > now,
        ]

    async def _replace_roles(self, user_id: str, keys: list[str]) -> None:
        await self._session.execute(
            sa.delete(UserRoleRow).where(UserRoleRow.user_id == user_id)
        )
        for key in dict.fromkeys(keys):
            self._session.add(UserRoleRow(user_id=user_id, role_key=key))

    async def _replace_permissions(self, role_key: str, keys: list[str]) -> None:
        await self._session.execute(
            sa.delete(RolePermissionRow).where(RolePermissionRow.role_key == role_key)
        )
        for key in dict.fromkeys(keys):
            self._session.add(RolePermissionRow(role_key=role_key, permission_key=key))

    async def _user_dict(self, row: UserRow) -> dict[str, Any]:
        roles = (
            await self._session.scalars(
                sa.select(UserRoleRow.role_key).where(UserRoleRow.user_id == row.id)
            )
        ).all()
        permissions = (
            await self._session.scalars(
                sa.select(RolePermissionRow.permission_key).where(
                    RolePermissionRow.role_key.in_(roles)
                )
            )
        ).all()
        return {
            "id": row.id,
            "username": row.username,
            "display_name": row.display_name,
            "enabled": row.enabled,
            "bootstrap": row.bootstrap,
            "role_keys": list(roles),
            "permissions": list(dict.fromkeys(permissions)),
            "password_hash": row.password_hash,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    async def _role_dict(self, row: RoleRow) -> dict[str, Any]:
        permissions = (
            await self._session.scalars(
                sa.select(RolePermissionRow.permission_key).where(
                    RolePermissionRow.role_key == row.key
                )
            )
        ).all()
        return {
            "key": row.key,
            "name": row.name,
            "protected": row.protected,
            "permissions": list(permissions),
        }
