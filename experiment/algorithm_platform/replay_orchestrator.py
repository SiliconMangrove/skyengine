"""Concrete local execution for deterministic and branched replay."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, replace

from .artifacts import ArtifactManifest, LocalArtifactStore
from .models import (
    AlgorithmInterface,
    ArtifactKind,
    ArtifactRef,
    DomainCapability,
    EventType,
    RunPurpose,
    RunResult,
    RunSpec,
    StateSnapshot,
)
from .orchestrator import LocalOrchestrator
from .registry import PlatformRegistry, default_registry
from .replay import (
    BranchReplayRequest,
    ReplayDiagnostics,
    ReplayDiagnosticsReport,
    ReplayDiffPolicy,
    ReplayDiffReport,
    ReplayDiffer,
    ReplayMode,
    ReplayTrace,
    load_replay_trace,
)
from .schema import validate_schema_value
from .serialization import to_jsonable


def branch_run_spec(
    request: BranchReplayRequest,
    source_run: RunSpec,
) -> RunSpec:
    """Build the immutable run executed by a snapshot branch."""

    return replace(
        source_run,
        run_id=request.run_id,
        trial_id=f"{source_run.trial_id}.branch",
        algorithm=request.algorithm,
        purpose=RunPurpose.REPLAY,
        budget=request.algorithm.budget or source_run.budget,
        input_artifacts=tuple(request.algorithm.input_artifacts),
        metadata={
            **dict(source_run.metadata),
            **dict(request.metadata),
            "replay_mode": ReplayMode.BRANCH.value,
            "parent_execution_id": request.source.execution_id,
            "parent_run_id": request.source.run_id,
            "branch_after_sequence": request.anchor.after_sequence,
            "branch_snapshot_digest": request.anchor.snapshot.artifact.digest,
        },
    )


def replay_plan_digest(
    mode: ReplayMode,
    source_execution_id: str,
    source_run_id: str,
    run: RunSpec,
) -> str:
    """Return the digest used by a replay plan artifact and lifecycle record."""

    data = json.dumps(
        to_jsonable(
            _replay_plan_payload(
                mode,
                source_execution_id,
                source_run_id,
                run,
            )
        ),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


@dataclass(frozen=True, slots=True)
class ReplayExecutionReport:
    """Durable result of one replay execution."""

    execution_id: str
    mode: ReplayMode
    source_execution_id: str
    source_run_id: str
    trace: ReplayTrace
    result: RunResult
    diagnostics: ReplayDiagnosticsReport
    diff: ReplayDiffReport | None
    run_manifest: ArtifactManifest
    plan_artifact: ArtifactRef
    report_artifact: ArtifactRef
    execution_manifest: ArtifactManifest


class LocalReplayOrchestrator:
    """Rerun an exact RunSpec or continue it from a domain snapshot."""

    def __init__(
        self,
        workspace_root: str,
        *,
        registry: PlatformRegistry = default_registry,
        artifact_store: LocalArtifactStore | None = None,
    ) -> None:
        self._registry = registry
        self._orchestrator = LocalOrchestrator(
            workspace_root,
            registry=registry,
            artifact_store=artifact_store,
        )
        self._artifact_store = self._orchestrator.artifact_store

    def reproduce(
        self,
        source: ReplayTrace,
        run: RunSpec,
        *,
        execution_id: str,
        diff_policy: ReplayDiffPolicy = ReplayDiffPolicy(),
    ) -> ReplayExecutionReport:
        """Execute the same immutable run and compare the resulting event stream."""

        if source.run_id != run.run_id:
            raise ValueError("source trace and RunSpec must identify the same run")
        diagnostics = ReplayDiagnostics().inspect(source)
        if not diagnostics.deterministic_replay_ready:
            raise ValueError(
                "source trace does not contain complete deterministic replay evidence"
            )
        domain_manifest = self._registry.domain_manifest(
            run.domain.domain_id,
            run.domain.version,
        )
        if (
            DomainCapability.DETERMINISTIC_REPLAY
            not in domain_manifest.capabilities
        ):
            raise ValueError("domain does not declare deterministic replay support")

        initial_snapshot = self._reproduction_snapshot(source, run)
        result, run_manifest = self._orchestrator.execute_run(
            run,
            execution_id=execution_id,
            initial_snapshot=initial_snapshot,
        )
        trace = self._trace_from_result(
            result,
            mode=ReplayMode.DETERMINISTIC,
            metadata={
                "source_execution_id": source.execution_id,
                "source_run_id": source.run_id,
            },
        )
        diff = ReplayDiffer().compare(source, trace, policy=diff_policy)
        return self._finish(
            execution_id=execution_id,
            source=source,
            trace=trace,
            result=result,
            run=run,
            run_manifest=run_manifest,
            diff=diff,
            provenance_artifacts=(
                ()
                if initial_snapshot is None
                else (initial_snapshot.artifact,)
            ),
        )

    def branch(
        self,
        request: BranchReplayRequest,
        source_run: RunSpec,
    ) -> ReplayExecutionReport:
        """Restore a source snapshot and continue with another online algorithm."""

        if request.source.run_id != source_run.run_id:
            raise ValueError("source trace and RunSpec must identify the same run")
        diagnostics = ReplayDiagnostics().inspect(request.source)
        if not diagnostics.branch_replay_ready:
            raise ValueError(
                "source trace does not contain complete snapshot branch evidence"
            )
        matching_anchor = next(
            (
                anchor
                for anchor in request.source.snapshots
                if anchor.after_sequence == request.anchor.after_sequence
                and anchor.snapshot.artifact.digest
                == request.anchor.snapshot.artifact.digest
            ),
            None,
        )
        if matching_anchor is None:
            raise ValueError("branch anchor is not part of the source trace")
        if request.algorithm.interface is not AlgorithmInterface.ONLINE:
            raise ValueError("branch replay requires an online algorithm")

        domain_manifest = self._registry.domain_manifest(
            source_run.domain.domain_id,
            source_run.domain.version,
        )
        if (
            DomainCapability.SNAPSHOT_RESTORE
            not in domain_manifest.capabilities
        ):
            raise ValueError("domain does not declare snapshot restore support")
        algorithm_manifest = self._registry.algorithm_manifest(
            request.algorithm.algorithm_id,
            request.algorithm.version,
        )
        if AlgorithmInterface.ONLINE not in algorithm_manifest.interfaces:
            raise ValueError("selected algorithm does not implement online execution")
        validate_schema_value(
            request.algorithm.parameters,
            algorithm_manifest.parameter_schema,
            path="algorithm.parameters",
        )
        if (
            algorithm_manifest.supported_domains
            and source_run.domain.domain_id
            not in algorithm_manifest.supported_domains
        ):
            raise ValueError("selected algorithm does not support the source domain")
        unsupported_inputs = tuple(
            artifact.kind
            for artifact in request.algorithm.input_artifacts
            if artifact.kind not in algorithm_manifest.input_artifact_kinds
        )
        if unsupported_inputs:
            raise ValueError(
                "selected algorithm received undeclared input artifact kinds: "
                f"{unsupported_inputs}"
            )
        minimum_inputs = algorithm_manifest.minimum_input_artifacts.get(
            AlgorithmInterface.ONLINE,
            0,
        )
        if len(request.algorithm.input_artifacts) < minimum_inputs:
            raise ValueError(
                "selected algorithm requires at least "
                f"{minimum_inputs} input artifacts for online execution"
            )
        if not self._artifact_store.verify(request.anchor.snapshot.artifact):
            raise OSError("branch snapshot failed content-integrity verification")

        branch_run = branch_run_spec(request, source_run)
        result, run_manifest = self._orchestrator.execute_run(
            branch_run,
            execution_id=request.execution_id,
            initial_snapshot=request.anchor.snapshot,
        )
        trace = self._trace_from_result(
            result,
            mode=ReplayMode.BRANCH,
            metadata=dict(request.metadata),
        )
        trace = replace(
            trace,
            parent_run_id=request.source.run_id,
            branch_after_sequence=request.anchor.after_sequence,
        )
        return self._finish(
            execution_id=request.execution_id,
            source=request.source,
            trace=trace,
            result=result,
            run=branch_run,
            run_manifest=run_manifest,
            diff=None,
            provenance_artifacts=(request.anchor.snapshot.artifact,),
        )

    def _trace_from_result(
        self,
        result: RunResult,
        *,
        mode: ReplayMode,
        metadata: dict[str, object],
    ) -> ReplayTrace:
        if result.event_log is None:
            raise ValueError("replay execution did not publish an event log")
        event_path = self._artifact_store.resolve(result.event_log)
        return load_replay_trace(
            event_path,
            mode=mode,
            event_log=result.event_log,
            metadata=metadata,
        )

    def _reproduction_snapshot(
        self,
        source: ReplayTrace,
        run: RunSpec,
    ) -> StateSnapshot | None:
        digest = run.metadata.get("branch_snapshot_digest")
        if digest is None:
            return None
        reset = next(
            (
                event
                for event in source.events
                if event.event_type is EventType.ENVIRONMENT_RESET
            ),
            None,
        )
        if reset is None:
            raise ValueError("branched source trace lacks its restore event")
        restored = reset.payload.get("restored_snapshot")
        if not isinstance(restored, Mapping):
            raise ValueError("branched source trace lacks restored snapshot metadata")
        artifact = next(
            (
                artifact
                for manifest in self._artifact_store.iter_manifests()
                for artifact in manifest.artifacts
                if artifact.digest == digest
                and artifact.kind is ArtifactKind.STATE_SNAPSHOT
            ),
            None,
        )
        if artifact is None:
            raise FileNotFoundError(
                f"branch source snapshot artifact does not exist: {digest}"
            )
        if not self._artifact_store.verify(artifact):
            raise OSError("branch source snapshot failed content-integrity verification")
        return StateSnapshot(
            state_version=int(restored["state_version"]),
            simulation_time=float(restored["simulation_time"]),
            artifact=artifact,
            state_hash=str(restored["state_hash"]),
        )

    def _finish(
        self,
        *,
        execution_id: str,
        source: ReplayTrace,
        trace: ReplayTrace,
        result: RunResult,
        run: RunSpec,
        run_manifest: ArtifactManifest,
        diff: ReplayDiffReport | None,
        provenance_artifacts: tuple[ArtifactRef, ...] = (),
    ) -> ReplayExecutionReport:
        diagnostics = ReplayDiagnostics().inspect(trace)
        plan_artifact = self._artifact_store.put_json(
            _replay_plan_payload(
                trace.mode,
                source.execution_id,
                source.run_id,
                run,
            ),
            kind=ArtifactKind.EXECUTION_PLAN,
            metadata={
                "execution_id": execution_id,
                "experiment_id": run.experiment_id,
                "plan_type": "replay",
                "replay_mode": trace.mode.value,
            },
        )
        report_artifact = self._artifact_store.put_json(
            {
                "execution_id": execution_id,
                "mode": trace.mode,
                "source_execution_id": source.execution_id,
                "source_run_id": source.run_id,
                "run_result": result,
                "event_log": trace.event_log,
                "trace_metadata": dict(trace.metadata),
                "parent_run_id": trace.parent_run_id,
                "branch_after_sequence": trace.branch_after_sequence,
                "diagnostics": diagnostics,
                "diff": diff,
            },
            kind=ArtifactKind.REPORT,
            metadata={
                "execution_id": execution_id,
                "report_type": "replay",
                "replay_mode": trace.mode.value,
                "source_execution_id": source.execution_id,
                "source_run_id": source.run_id,
            },
        )
        execution_manifest = self._artifact_store.write_manifest(
            f"{execution_id}.replay",
            (
                *provenance_artifacts,
                plan_artifact,
                *result.artifacts,
                report_artifact,
            ),
            metadata={
                "execution_id": execution_id,
                "replay_mode": trace.mode.value,
                "source_execution_id": source.execution_id,
                "source_run_id": source.run_id,
                "run_manifest_id": run_manifest.manifest_id,
                "equivalent": None if diff is None else diff.equivalent,
            },
        )
        return ReplayExecutionReport(
            execution_id=execution_id,
            mode=trace.mode,
            source_execution_id=source.execution_id,
            source_run_id=source.run_id,
            trace=trace,
            result=result,
            diagnostics=diagnostics,
            diff=diff,
            run_manifest=run_manifest,
            plan_artifact=plan_artifact,
            report_artifact=report_artifact,
            execution_manifest=execution_manifest,
        )


def _replay_plan_payload(
    mode: ReplayMode,
    source_execution_id: str,
    source_run_id: str,
    run: RunSpec,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "replay_mode": mode,
        "source_execution_id": source_execution_id,
        "source_run_id": source_run_id,
        "runs": (run,),
    }
