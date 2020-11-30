from __future__ import annotations

from typing import Any, Dict, Tuple, Callable, Union, Optional

from arrowhead_client.system import ArrowheadSystem
from arrowhead_client.abc import BaseConsumer, BaseProvider
from arrowhead_client.service import Service
from arrowhead_client.core_services.core_services import core_service
from arrowhead_client.core_services import core_service_responses as responses, core_service_forms as forms
from arrowhead_client.configuration import config as ar_config
from arrowhead_client.security.utils import extract_publickey, extract_privatekey
from arrowhead_client.security import access_policy
import arrowhead_client.errors as errors


StoredConsumedService = Dict[str, Tuple[Service, ArrowheadSystem, str, Optional[str]]]
StoredProvidedService = Dict[str, Tuple[Service, Callable, str]]


class ArrowheadClient:
    """
    Application class for Arrowhead Systems.

    This class serves as a bridge that connects systems, consumers, and providers to the user.

    Attributes:
        system: ArrowheadSystem
        consumer: Consumer
        provider: Provider
        logger: Logger, will default to the logger found in logs.get_logger()
        keyfile: PEM keyfile
        certfile: PEM certfile
    """

    def __init__(self,
                 system: ArrowheadSystem,
                 consumer: BaseConsumer,
                 provider: BaseProvider,
                 logger: Any,
                 keyfile: str = '',
                 certfile: str = '', ):
        self.system = system
        self.consumer = consumer
        self.provider = provider
        self.keyfile = keyfile
        self.certfile = certfile
        self.secure = True if (self.keyfile and self.certfile) else False
        self._logger = logger
        # TODO: Should these two be set in a different place?
        self.system._publickey = extract_publickey(certfile)
        self.system._privatekey = extract_privatekey(keyfile)
        # TODO: Should this belong to a cloud class?
        self._authorization_publickey = None
        self._consumed_services: StoredConsumedService = {}
        self._provided_services: StoredProvidedService = {}

        # Setup methods
        # TODO: Move these to a dedicated initiation method
        self._core_system_setup()
        self.add_provided_service = self.provider.add_provided_service

    def consume_service(self, service_definition: str, **kwargs):
        """
        Consumes the given provided_service definition

        Args:
            service_definition: The provided_service definition of a consumable provided_service
            **kwargs: Collection of keyword arguments passed to the consumer.
        """

        consumed_service, provider_system, method, auth_token = self._consumed_services[service_definition]

        # TODO: these should be normal arguments, not live in kwargs
        if consumed_service.interface.secure == 'SECURE':
            # Add certificate files if provided_service is secure
            kwargs['cert'] = self.cert

        return self.consumer.consume_service(consumed_service,
                                             provider_system,
                                             method,
                                             auth_token,
                                             **kwargs, )

    def extract_payload(self, service_response: Any, payload_type: str) -> Union[Dict, str]:
        return self.consumer.extract_payload(service_response, payload_type)

    def add_consumed_service(self,
                             service_definition: str,
                             method: str,
                             access_policy: str = '',
                             **kwargs, ) -> None:
        """
        Add orchestration rule for provided_service definition

        Args:
            service_definition: Service definition that is looked up from the orchestrator.
            method: The HTTP method given in uppercase that is used to consume the provided_service.
        """

        requested_service = Service(service_definition, access_policy=access_policy)

        orchestration_form = forms.OrchestrationForm(
                self.system,
                requested_service,
                **kwargs
        )

        orchestration_response = self.consume_service(
                'orchestration-provided_service',
                json=orchestration_form.dto(),
                cert=self.cert,
        )

        # TODO: Handle orchestrator error codes
        if orchestration_response.status_code == 401:
            raise errors.NotAuthorizedError(orchestration_response.payload['errorMessage'])
        elif orchestration_response.status_code == 500:
            raise errors.CoreServiceNotAvailableError('orchestration')

        try:
            (orchestrated_service, provider_system, token), *_ = responses.process_orchestration_response(orchestration_response)
        except errors.NoAvailableServicesError as e:
            print(e)
        else:
            # TODO: Handle response with more than 1 provided_service
            # Perhaps a list of consumed services for each provided_service definition should be stored
            self._store_consumed_service(orchestrated_service, provider_system, method, token)

    def provided_service(
            self,
            service_definition: str,
            service_uri: str,
            interface: str,
            access_policy: str,
            method: str,
            *func_args,
            **func_kwargs, ):
        """
        Decorator to add a provided provided_service to the provider.

        Args:
            service_definition: Service definition to be stored in the provided_service registry
            service_uri: The path to the provided_service
            interface: Arrowhead interface string(s)
            method: HTTP method required to access the provided_service
        """
        provided_service = Service(
                service_definition,
                service_uri,
                interface,
                access_policy,
        )

        def wrapped_func(func):
            self._provided_services[service_definition] = (
                provided_service,
                func,
                method,
                access_policy,
                func_args,
                func_kwargs)
            return func

        return wrapped_func

    def run_forever(self) -> None:
        """ Start the server, publish all provided_service, and run until interrupted. Then, unregister all services"""

        # TODO: This filter should be removed
        import warnings
        warnings.simplefilter('ignore')

        try:
            self._authorization_publickey = responses.process_publickey(self.consume_service('publickey'))
            self._initialize_provided_services()
            self._register_all_services()
            self._logger.info('Starting server')
            print('Started Arrowhead ArrowheadSystem')
            self.provider.run_forever()
        except KeyboardInterrupt:
            self._logger.info('Shutting down server')
        finally:
            print('Shutting down Arrowhead system')
            self._unregister_all_services()
            self._logger.info('Server shut down')

    @property
    def cert(self) -> Tuple[str, str]:
        """
        Tuple of the keyfile and certfile
        """
        return self.certfile, self.keyfile

    def _initialize_provided_services(self) -> None:
        for provided_service, func, method, access_policy, func_args, func_kwargs in self._provided_services.values():
            self.provider.add_provided_service(
                    provided_service,
                    method=method,
                    func=func,
                    # TODO: Here I change the type of access_policy from str to AccessPolicy, this is not good and should be changed
                    access_policy=access_policy.get_access_policy(
                            access_policy,
                            authorization_key=self._authorization_publickey
                    ),
                    *func_args,
                    **func_kwargs)
            #self._provided_services[provided_service.service_definition][3] = 'suspended'

    def _core_system_setup(self) -> None:
        """
        Method that sets up the core services.

        Runs when the client is created and should not be run manually.
        """

        self._store_consumed_service(
                core_service('register'),
                ar_config['core_service']['service_registry'],
                'POST')
        self._store_consumed_service(
                core_service('unregister'),
                ar_config['core_service']['service_registry'],
                'DELETE')
        self._store_consumed_service(
                core_service('orchestration-provided_service'),
                ar_config['core_service']['orchestrator'],
                'POST')
        self._store_consumed_service(
                core_service('publickey'),
                ar_config['core_service']['authorization'],
                'GET'
        )

    def _store_consumed_service(
            self,
            service: Service,
            system: ArrowheadSystem,
            http_method: str,
            authorization_token: Optional[str] = None) -> None:
        """
        Register consumed services with the consumer

        Args:
            service: Service to be stored
            system: System containing the provided_service
            http_method: HTTP method used to consume the provided_service
        """

        self._consumed_services[service.service_definition] = (
            service,
            system,
            http_method,
            authorization_token,
        )

    def _register_service(self, service: Service):
        """
        Registers the given provided_service with provided_service registry

        Args:
            service: Service to register with the Service registry.
        """

        # Decide security level:
        if service.interface.secure == 'INSECURE':
            secure = 'NOT_SECURE'
        elif service.interface.secure == 'SECURE':
            secure = 'CERTIFICATE'
        else:
            secure = 'CERTIFICATE'
        # TODO: Add 'TOKEN' security level

        # TODO: Should accept a system and a provided_service
        service_registration_form = forms.ServiceRegistrationForm(
                provided_service=service,
                provider_system=self.system,
        )

        service_registration_response = self.consume_service(
                'register',
                json=service_registration_form.dto(),
                cert=self.cert
        )
        print(service_registration_response.payload)

        # TODO: Error handling - Done
        # TODO: Do logging
        if service_registration_response.status_code == 400:
            raise errors.CouldNotRegisterServiceError(
                    service.service_definition,
                    service_registration_response.payload['errorMessage'],
            )
        elif service_registration_response.status_code == 401:
            raise errors.NotAuthorizedError
        elif service_registration_response.status_code == 500:
            raise errors.CoreServiceNotAvailableError('Service Registry')

    def _register_all_services(self) -> None:
        """
        Registers all provided services of the system with the system registry.
        """
        for service, *_ in self._provided_services.values():
            try:
                self._register_service(service)
            except errors.CouldNotRegisterServiceError as e:
                print(e)

    def _unregister_service(self, service: Service) -> None:
        """
        Unregisters the given provided_service with provided_service registry

        Args:
            service: Service to unregister with the Service registry.
        """

        service_definition = service.service_definition

        if service_definition not in self._provided_services.keys():
            raise ValueError(f'{service_definition} not provided by {self}')

        # TODO: Should be a "form"?
        unregistration_payload = {
            'service_definition': service_definition,
            'system_name': self.system.system_name,
            'address': self.system.address,
            'port': self.system.port
        }

        service_unregistration_response = self.consume_service(
                'unregister',
                params=unregistration_payload,
                cert=self.cert
        )

        if service_unregistration_response.status_code == 400:
            raise errors.CouldNotUnregisterServiceError(
                    service.service_definition,
                    service_unregistration_response.payload['errorMessage']
            )
        if service_unregistration_response.status_code == 401:
            raise errors.NotAuthorizedError
        if service_unregistration_response.status_code == 500:
            raise errors.CoreServiceNotAvailableError

    def _unregister_all_services(self) -> None:
        """
        Unregisters all provided services of the system with the system registry.
        """

        for service, *_ in self._provided_services.values():
            try:
                self._unregister_service(service)
            except errors.CouldNotUnregisterServiceError as e:
                print(e)

    """
    def __enter__(self):
        '''Start server and register all services'''
        import warnings
        warnings.simplefilter('ignore')

        print('Starting server')
        self.server.start()
        print('Registering services')
        self.register_all_services()

    def __exit__(self, exc_type, exc_value, tb):
        '''Unregister all services and stop the server'''
        if exc_type != KeyboardInterrupt:
            print(f'Exception was raised:')
            print(exc_value)

        print('\nArrowheadSystem was stopped, unregistering services')
        self.unregister_all_services()
        print('Stopping server')
        self.server.stop()
        print('Shutdown completed')

        return True
    """


def _service_uri(service: Service, system: ArrowheadSystem) -> str:
    service_uri = f'{system.authority}/{service.service_uri}'

    return service_uri
