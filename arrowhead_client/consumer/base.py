from abc import ABC, abstractmethod

from arrowhead_client.abc import ProtocolMixin
from arrowhead_client.response import Response, ConnectionResponse
from arrowhead_client.rules import OrchestrationRule


class BaseConsumer(ProtocolMixin, ABC, protocol='<PROTOCOL>'):
    """Abstract base class for consumers"""
    def __init__(
            self,
            keyfile,
            certfile,
            cafile,
    ):
        self.keyfile = keyfile
        self.certfile = certfile
        self.cafile = cafile

    @abstractmethod
    def consume_service(
            self,
            rule: OrchestrationRule,
            **kwargs
    ) -> Response:
        """
        Consume service according to the consumation rule and return the response.

        Args:
           rule: Orchestration rule.
        Returns:
            A Response object.
        """

    async def connect(
            self,
            rule: OrchestrationRule,
            **kwargs,
    ) -> ConnectionResponse:
        """
        Connect to a service with a persistent connection, for example with WebSockets

        Args:
            rule: Orchestration rule.
        Returns:
            A connection object, currently implementation specific.
        """
        raise NotImplementedError

    async def async_startup(self):
        raise NotImplementedError

    def async_shutdown(self):
        raise NotImplementedError