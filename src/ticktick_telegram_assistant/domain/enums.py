from enum import Enum


class MemoryType(str, Enum):
    PREFERENCE = "preference"
    ALIAS_MAPPING = "alias_mapping"
    TIME_EXPRESSION = "time_expression"
    DISAMBIGUATION_PATTERN = "disambiguation_pattern"
    STYLE_PREFERENCE = "style_preference"
    TICKTICK_SNAPSHOT = "ticktick_snapshot"


class PlannedActionType(str, Enum):
    QUERY = "query"
    CREATE = "create"
    UPDATE = "update"
    COMPLETE = "complete"
    REMINDER_CONTROL = "reminder_control"
