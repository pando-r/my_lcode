"""Team management and collaboration tools."""

from lcode.team.manager import Manager
from lcode.team.tools.send_message import SendMessageTool, TeamAwareSendMessageTool
from lcode.team.tools.task_create import TaskCreateTool
from lcode.team.tools.task_get import TaskGetTool, TeamAwareTaskGetTool
from lcode.team.tools.task_list import TaskListTool, TeamAwareTaskListTool
from lcode.team.tools.task_update import TaskUpdateTool
from lcode.team.tools.team_create import TeamCreateTool
from lcode.team.tools.team_delete import TeamDeleteTool


def team_tools(manager: Manager, fallback_list, fallback_get, fallback_send) -> list:
    # 统一注册：TeamCreate/Delete + TaskCreate/Update 为新增；
    # TaskList/TaskGet/SendMessage 在 Team 上下文分派到团队实现，否则回落旧行为。
    return [
        TeamCreateTool(manager),
        TeamDeleteTool(manager),
        TaskCreateTool(manager),
        TaskUpdateTool(manager),
        TeamAwareTaskListTool(manager, fallback_list),
        TeamAwareTaskGetTool(manager, fallback_get),
        TeamAwareSendMessageTool(manager, fallback_send),
    ]


__all__ = [
    "SendMessageTool",
    "TaskCreateTool",
    "TaskGetTool",
    "TaskListTool",
    "TaskUpdateTool",
    "TeamAwareSendMessageTool",
    "TeamAwareTaskGetTool",
    "TeamAwareTaskListTool",
    "TeamCreateTool",
    "TeamDeleteTool",
    "team_tools",
]
