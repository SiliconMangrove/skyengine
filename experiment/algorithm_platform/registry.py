"""Explicit algorithm and domain registrations.

The registry stores concrete factory callables next to immutable manifests.
It never imports an object from a user supplied string.  The entrypoint names
in manifests are provenance and UI metadata only; executable code enters the
platform exclusively through an explicit registration call.
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from types import MappingProxyType
from typing import Mapping, Protocol

from .events import EventRecorder

from .models import (
    AlgorithmInterface,
    AlgorithmManifest,
    AlgorithmRef,
    DomainManifest,
    DomainRef,
    PLATFORM_PROTOCOL_VERSION,
    RuntimeManifest,
    RuntimeRef,
)
from .protocols import RuntimeDriver, RuntimePlugin


RegistryKey = tuple[str, str]


class AlgorithmFactory(Protocol):
    """Construct one algorithm runtime from an explicit reference."""

    def __call__(self, reference: AlgorithmRef) -> object:
        ...


class DomainFactory(Protocol):
    """Construct one domain adapter from an explicit reference."""

    def __call__(self, reference: DomainRef) -> object:
        ...


class RuntimePluginFactory(Protocol):
    """Construct one runtime driver bound to an execution event recorder."""

    def __call__(
        self,
        recorder: EventRecorder,
    ) -> RuntimeDriver[object, object]:
        ...


@dataclass(frozen=True, slots=True)
class AlgorithmRegistration:
    """Manifest and executable factories for one algorithm version."""

    manifest: AlgorithmManifest
    factories: Mapping[AlgorithmInterface, AlgorithmFactory]


@dataclass(frozen=True, slots=True)
class DomainRegistration:
    """Manifest and executable factory for one domain version."""

    manifest: DomainManifest
    factory: DomainFactory


@dataclass(frozen=True, slots=True)
class RuntimeRegistration:
    """Manifest and factory for one runtime plugin version."""

    manifest: RuntimeManifest
    factory: RuntimePluginFactory


class PlatformRegistry:
    """In-process source of trusted algorithm and domain implementations."""

    def __init__(self) -> None:
        self._algorithms: dict[RegistryKey, AlgorithmRegistration] = {}
        self._domains: dict[RegistryKey, DomainRegistration] = {}
        self._runtimes: dict[RegistryKey, RuntimeRegistration] = {}
        self._lock = RLock()

    def register_algorithm(
        self,
        manifest: AlgorithmManifest,
        factories: Mapping[AlgorithmInterface, AlgorithmFactory],
    ) -> None:
        """Register one version and reject incomplete or duplicate entries."""

        key = _manifest_key(manifest.algorithm_id, manifest.version, "algorithm")
        if manifest.protocol_version != PLATFORM_PROTOCOL_VERSION:
            raise ValueError(
                f"algorithm protocol version must be {PLATFORM_PROTOCOL_VERSION!r}"
            )
        declared_interfaces = set(manifest.interfaces)
        entrypoint_interfaces = set(manifest.entrypoints)
        factory_interfaces = set(factories)
        if not declared_interfaces:
            raise ValueError("algorithm manifest must declare at least one interface")
        if entrypoint_interfaces != declared_interfaces:
            raise ValueError(
                "algorithm manifest entrypoints must exactly match its interfaces"
            )
        if factory_interfaces != declared_interfaces:
            raise ValueError(
                "algorithm factories must exactly match the manifest interfaces"
            )
        if any(not callable(factory) for factory in factories.values()):
            raise TypeError("every algorithm factory must be callable")
        if any(
            interface not in declared_interfaces
            for interface in manifest.minimum_input_artifacts
        ):
            raise ValueError(
                "minimum input artifact requirements must target declared interfaces"
            )
        if any(
            type(count) is not int or count < 0
            for count in manifest.minimum_input_artifacts.values()
        ):
            raise ValueError(
                "minimum input artifact counts must be non-negative integers"
            )
        if (
            any(count > 0 for count in manifest.minimum_input_artifacts.values())
            and not manifest.input_artifact_kinds
        ):
            raise ValueError(
                "an algorithm requiring input artifacts must declare accepted kinds"
            )

        registration = AlgorithmRegistration(
            manifest=manifest,
            factories=MappingProxyType(dict(factories)),
        )
        with self._lock:
            if key in self._algorithms:
                raise ValueError(
                    f"algorithm {manifest.algorithm_id!r} version "
                    f"{manifest.version!r} is already registered"
                )
            self._algorithms[key] = registration

    def register_domain(
        self,
        manifest: DomainManifest,
        factory: DomainFactory,
    ) -> None:
        """Register one domain adapter version and reject duplicates."""

        key = _manifest_key(manifest.domain_id, manifest.version, "domain")
        if manifest.protocol_version != PLATFORM_PROTOCOL_VERSION:
            raise ValueError(
                f"domain protocol version must be {PLATFORM_PROTOCOL_VERSION!r}"
            )
        if not callable(factory):
            raise TypeError("domain factory must be callable")

        registration = DomainRegistration(manifest=manifest, factory=factory)
        with self._lock:
            if key in self._domains:
                raise ValueError(
                    f"domain {manifest.domain_id!r} version "
                    f"{manifest.version!r} is already registered"
                )
            self._domains[key] = registration

    def register_runtime(
        self,
        manifest: RuntimeManifest,
        factory: RuntimePluginFactory,
    ) -> None:
        """Register an exact runtime plugin version."""

        key = _manifest_key(manifest.runtime_id, manifest.version, "runtime")
        if manifest.protocol_version != PLATFORM_PROTOCOL_VERSION:
            raise ValueError(
                f"runtime protocol version must be {PLATFORM_PROTOCOL_VERSION!r}"
            )
        if not manifest.interfaces:
            raise ValueError("runtime manifest must declare at least one interface")
        if not manifest.entrypoint.strip():
            raise ValueError("runtime entrypoint must not be empty")
        if not callable(factory):
            raise TypeError("runtime factory must be callable")
        registration = RuntimeRegistration(manifest=manifest, factory=factory)
        with self._lock:
            if key in self._runtimes:
                raise ValueError(
                    f"runtime {manifest.runtime_id!r} version "
                    f"{manifest.version!r} is already registered"
                )
            self._runtimes[key] = registration

    def register_runtime_plugin(self, plugin: RuntimePlugin) -> None:
        """Register a plugin object exposing its own manifest and factory."""

        self.register_runtime(plugin.manifest, plugin.create)

    def algorithm_registration(
        self,
        algorithm_id: str,
        version: str,
    ) -> AlgorithmRegistration:
        """Return an exact algorithm version or raise a descriptive error."""

        key = (algorithm_id, version)
        try:
            return self._algorithms[key]
        except KeyError:
            raise KeyError(
                f"algorithm {algorithm_id!r} version {version!r} is not registered"
            ) from None

    def domain_registration(
        self,
        domain_id: str,
        version: str,
    ) -> DomainRegistration:
        """Return an exact domain version or raise a descriptive error."""

        key = (domain_id, version)
        try:
            return self._domains[key]
        except KeyError:
            raise KeyError(
                f"domain {domain_id!r} version {version!r} is not registered"
            ) from None

    def runtime_registration(
        self,
        runtime_id: str,
        version: str,
    ) -> RuntimeRegistration:
        key = (runtime_id, version)
        try:
            return self._runtimes[key]
        except KeyError:
            raise KeyError(
                f"runtime {runtime_id!r} version {version!r} is not registered"
            ) from None

    def algorithm_manifest(
        self,
        algorithm_id: str,
        version: str,
    ) -> AlgorithmManifest:
        return self.algorithm_registration(algorithm_id, version).manifest

    def domain_manifest(self, domain_id: str, version: str) -> DomainManifest:
        return self.domain_registration(domain_id, version).manifest

    def runtime_manifest(self, runtime_id: str, version: str) -> RuntimeManifest:
        return self.runtime_registration(runtime_id, version).manifest

    def create_algorithm(self, reference: AlgorithmRef) -> object:
        """Build the selected interface without resolving an import string."""

        registration = self.algorithm_registration(
            reference.algorithm_id,
            reference.version,
        )
        try:
            factory = registration.factories[reference.interface]
        except KeyError:
            raise ValueError(
                f"algorithm {reference.algorithm_id!r} version "
                f"{reference.version!r} does not implement "
                f"{reference.interface.value!r}"
            ) from None
        return factory(reference)

    def create_domain(self, reference: DomainRef) -> object:
        """Build the exact registered domain adapter version."""

        registration = self.domain_registration(
            reference.domain_id,
            reference.version,
        )
        return registration.factory(reference)

    def create_runtime(
        self,
        reference: RuntimeRef,
        recorder: EventRecorder,
    ) -> RuntimeDriver[object, object]:
        registration = self.runtime_registration(
            reference.runtime_id,
            reference.version,
        )
        return registration.factory(recorder)

    def list_algorithms(self) -> tuple[AlgorithmManifest, ...]:
        """List manifests in deterministic identifier/version order."""

        with self._lock:
            keys = sorted(self._algorithms)
            return tuple(self._algorithms[key].manifest for key in keys)

    def list_domains(self) -> tuple[DomainManifest, ...]:
        """List manifests in deterministic identifier/version order."""

        with self._lock:
            keys = sorted(self._domains)
            return tuple(self._domains[key].manifest for key in keys)

    def list_runtimes(self) -> tuple[RuntimeManifest, ...]:
        with self._lock:
            keys = sorted(self._runtimes)
            return tuple(self._runtimes[key].manifest for key in keys)


def _manifest_key(identifier: str, version: str, kind: str) -> RegistryKey:
    if not identifier.strip():
        raise ValueError(f"{kind} identifier must not be empty")
    if not version.strip():
        raise ValueError(f"{kind} version must not be empty")
    return identifier, version


default_registry = PlatformRegistry()


def register_algorithm(
    manifest: AlgorithmManifest,
    factories: Mapping[AlgorithmInterface, AlgorithmFactory],
) -> None:
    """Register an algorithm in the process-wide default registry."""

    default_registry.register_algorithm(manifest, factories)


def register_domain(manifest: DomainManifest, factory: DomainFactory) -> None:
    """Register a domain in the process-wide default registry."""

    default_registry.register_domain(manifest, factory)


def register_runtime(
    manifest: RuntimeManifest,
    factory: RuntimePluginFactory,
) -> None:
    """Register a runtime in the process-wide default registry."""

    default_registry.register_runtime(manifest, factory)


def register_runtime_plugin(plugin: RuntimePlugin) -> None:
    default_registry.register_runtime_plugin(plugin)
