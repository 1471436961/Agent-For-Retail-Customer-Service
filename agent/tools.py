"""Platform tool discovery entry point."""

from support_agent.adapters.change_tools import ChangeTools


class Tools(ChangeTools):
    """Expose verified reads, reviewed changes and terminal human handoff."""
