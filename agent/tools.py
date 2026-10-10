"""Platform tool discovery entry point."""

from support_agent.adapters.change_tools import ChangeTools


class Tools(ChangeTools):
    """Expose session-authorized customer, owned-order and catalog read tools."""
