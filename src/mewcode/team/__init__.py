"""Agent Team: multi-agent collaboration with shared tasks and mailboxes."""

from mewcode.team.backend import Backend, SpawnRequest, build_member_command, new_backend
from mewcode.team.backend.detect import detect
from mewcode.team.mailbox import Box, Message, MessageType
from mewcode.team.manager import LeadMessage, Manager
from mewcode.team.registry import AgentNameRegistry
from mewcode.team.tasks import Filter, Patch, Status, Store, Task
from mewcode.team.types import (
    BackendType,
    InProcessTeammateNoSpawnError,
    MemberExistsError,
    MemberNotFoundError,
    Team,
    TeamError,
    TeamHasActiveMembersError,
    TeammateInfo,
    TeamNotFoundError,
)

__all__ = [
    "AgentNameRegistry",
    "Backend",
    "BackendType",
    "Box",
    "Filter",
    "InProcessTeammateNoSpawnError",
    "LeadMessage",
    "Manager",
    "MemberExistsError",
    "MemberNotFoundError",
    "Message",
    "MessageType",
    "Patch",
    "SpawnRequest",
    "Status",
    "Store",
    "Task",
    "Team",
    "TeamError",
    "TeamHasActiveMembersError",
    "TeamNotFoundError",
    "TeammateInfo",
    "build_member_command",
    "detect",
    "new_backend",
]
