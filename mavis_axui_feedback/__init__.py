"""mavis-axui-feedback — bidirectional projection.

A workbook cell that projects to a runtime AND the runtime projects back.
Implements the ring: cell → projection → sub-workbook → flow → cell.
"""
from .feedback import (
    FeedbackRing, FeedbackCell, SubWorkbook,
    FeedbackEvent, FeedbackType, project_lossy,
    sign_command, verify_command_signature, polyformality_check,
    project_to_runtime, project_back_to_workbook,
)


__version__ = "0.2.0"


__all__ = [
    "__version__", "FeedbackRing", "FeedbackCell", "SubWorkbook",
    "FeedbackEvent", "FeedbackType", "project_lossy",
    "sign_command", "verify_command_signature", "polyformality_check",
    "project_to_runtime", "project_back_to_workbook",
]
