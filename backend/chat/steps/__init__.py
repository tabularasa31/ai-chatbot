"""Chat pipeline steps.

Each module owns one pipeline step (or a tight group of sub-steps). Every
step function takes a :class:`backend.chat.types.PipelineRun` and returns a
terminal :class:`backend.chat.types.ChatPipelineResult` to short-circuit the
pipeline, or ``None`` to continue. The orchestrator that wires them together
lives in ``backend/chat/pipeline.py``.
"""
