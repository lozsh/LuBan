"""app：应用装配层（create_app 工厂 + 种子与 lifespan）。"""

from lubanagent.app.factory import create_app
from lubanagent.app.lifespan import seed_defaults

__all__ = ["create_app", "seed_defaults"]
