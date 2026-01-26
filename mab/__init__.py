"""MAB - Multi-Agent Beads CLI tool for orchestrating concurrent agent workflows."""

from mab.rpc import DaemonNotRunningError as RPCDaemonNotRunningError
from mab.rpc import RPCClient, RPCError, RPCErrorCode, RPCRequest, RPCResponse, RPCServer
from mab.version import __version__

__all__ = [
    "__version__",
    "RPCClient",
    "RPCServer",
    "RPCRequest",
    "RPCResponse",
    "RPCError",
    "RPCErrorCode",
    "RPCDaemonNotRunningError",
]
