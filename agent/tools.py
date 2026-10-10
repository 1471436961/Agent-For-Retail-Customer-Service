"""Platform tool discovery entry point."""

from support_agent.adapters.customer_tools import CustomerTools


class Tools(CustomerTools):
    """Expose session-authorized customer, owned-order and catalog read tools."""
