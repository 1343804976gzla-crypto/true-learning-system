from __future__ import annotations

from collections.abc import Sequence

from database.audit import AUDIT_METADATA
from database.domains import AgentBase, ContentBase, CoreBase, LegacyBase, ReviewBase, RuntimeBase


def load_alembic_models() -> None:
    import agent_models  # noqa: F401
    import auth_models  # noqa: F401
    import knowledge_upload_models  # noqa: F401
    import learning_tracking_models  # noqa: F401
    import models  # noqa: F401
    import heartbeat_models  # noqa: F401
    import soul_models  # noqa: F401
    from services.api_hub import models as _api_hub_models  # noqa: F401


def get_target_metadata() -> Sequence[object]:
    load_alembic_models()
    return (
        CoreBase.metadata,
        ContentBase.metadata,
        LegacyBase.metadata,
        AgentBase.metadata,
        RuntimeBase.metadata,
        ReviewBase.metadata,
        AUDIT_METADATA,
    )
