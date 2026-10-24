"""Platform tool discovery entry point."""

from support_agent.adapters.change_tools import ChangeTools


class Tools(ChangeTools):
    """Provide real reads, grounded quotes, one canonical write channel and handoff."""
