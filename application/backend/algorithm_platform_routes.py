"""General algorithm experiment platform control-plane routes.

The HTTP layer only accepts declarative JSON/YAML experiment definitions.
Executable implementations are resolved from the process-local trusted
``PlatformRegistry``; configuration values are never treated as import paths.
"""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import yaml
from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import FileResponse, Response
from experiment.algorithm_platform.training_checkpoints import list_training_checkpoints, read_training_checkpoint

from experiment.algorithm_platform import (
    Aggregation,
    AlgorithmExecutionCancelled,
    AlgorithmInterface,
    AlgorithmRef,
    ArtifactKind,
    ArtifactRef,
    BranchReplayRequest,
    Budget,
    ComparisonMode,
    ConstraintOperator,
    ConstraintSpec,
    Direction,
    DomainRef,
    ExecutionRepository,
    ExecutionPlan,
    ExecutionRecord,
    ExecutionStatus,
    ExperimentRepository,
    ExperimentCompiler,
    LocalArtifactStore,
    LocalOrchestrator,
    LocalReplayOrchestrator,
    ObjectiveComponent,
    ObjectiveSpec,
    PLATFORM_PROTOCOL_VERSION,
    ReplayDiagnostics,
    ReplayDiffPolicy,
    ReplayDiffer,
    ReplayMode,
    ReplayTrace,
    ReplayView,
    RunFailure,
    RunPurpose,
    RunStatus,
    RunSeeds,
    RunSpec,
    ScenarioRef,
    StoredExperiment,
    TuningOrchestrator,
    branch_run_spec,
    default_registry,
    experiment_spec_from_mapping,
    experiment_spec_to_mapping,
    extract_decisions,
    load_replay_trace,
    replay_plan_digest,
)
from experiment.algorithm_platform.models import EventType, ExperimentSpec
from experiment.algorithm_platform.event_monitor import ExecutionEventMonitor
from experiment.algorithm_platform.serialization import to_jsonable
from experiment.algorithm_platform_plugins.dfjsp_t import (
    MEMETIC_PIBT_MANIFEST,
    CTDE_PPO_MANIFEST,
    DFJSPT_DOMAIN_MANIFEST,
    register_dfjsp_t_plugins,
)


ROOT = Path(__file__).resolve().parents[2]
DATASET_ROOT = ROOT / "dataset"
PLATFORM_ROOT = ROOT / "dataset" / "algorithm_platform"
ARTIFACT_ROOT = PLATFORM_ROOT / "artifacts"
EXECUTION_STATE_ROOT = PLATFORM_ROOT / "execution_state"
EXPERIMENT_ROOT = PLATFORM_ROOT / "experiments"

router = APIRouter(prefix="/algorithm-platform", tags=["algorithm-platform"])


def _ensure_dfjsp_t_plugins_registered() -> None:
    """Register the bundled plugin once and reject a partial registration."""

    expected_domain = (
        DFJSPT_DOMAIN_MANIFEST.domain_id,
        DFJSPT_DOMAIN_MANIFEST.version,
    )
    expected_algorithms = {
        (manifest.algorithm_id, manifest.version)
        for manifest in (
            MEMETIC_PIBT_MANIFEST,
            CTDE_PPO_MANIFEST,
        )
    }
    registered_domains = {
        (manifest.domain_id, manifest.version)
        for manifest in default_registry.list_domains()
    }
    registered_algorithms = {
        (manifest.algorithm_id, manifest.version)
        for manifest in default_registry.list_algorithms()
    }
    expected_presence = {
        expected_domain in registered_domains,
        *(key in registered_algorithms for key in expected_algorithms),
    }
    if expected_presence == {True}:
        if to_jsonable(
            default_registry.domain_manifest(*expected_domain)
        ) != to_jsonable(DFJSPT_DOMAIN_MANIFEST):
            raise RuntimeError("registered DFJSP-T domain manifest does not match")
        for expected in (
            MEMETIC_PIBT_MANIFEST,
            CTDE_PPO_MANIFEST,
        ):
            actual = default_registry.algorithm_manifest(
                expected.algorithm_id,
                expected.version,
            )
            if to_jsonable(actual) != to_jsonable(expected):
                raise RuntimeError(
                    "registered DFJSP-T algorithm manifest does not match: "
                    f"{expected.algorithm_id} {expected.version}"
                )
        return
    if True in expected_presence:
        raise RuntimeError("DFJSP-T plugin registration is incomplete")
    register_dfjsp_t_plugins(default_registry)


_ensure_dfjsp_t_plugins_registered()

_artifact_store = LocalArtifactStore(ARTIFACT_ROOT)
_execution_repository = ExecutionRepository(EXECUTION_STATE_ROOT)
_execution_repository.recover_interrupted()
_experiment_repository = ExperimentRepository(EXPERIMENT_ROOT)
_compiler = ExperimentCompiler(default_registry)
_orchestrator = LocalOrchestrator(
    PLATFORM_ROOT,
    registry=default_registry,
    artifact_store=_artifact_store,
)
_tuning_orchestrator = TuningOrchestrator(
    PLATFORM_ROOT,
    registry=default_registry,
    artifact_store=_artifact_store,
)
_replay_orchestrator = LocalReplayOrchestrator(
    str(PLATFORM_ROOT),
    registry=default_registry,
    artifact_store=_artifact_store,
)
_execution_threads: dict[str, threading.Thread] = {}
_execution_cancel_events: dict[str, threading.Event] = {}
_execution_threads_lock = threading.Lock()
_event_monitor = ExecutionEventMonitor()


@router.get("/catalog")
def get_catalog() -> dict[str, object]:
    registered_algorithms = default_registry.list_algorithms()
    optimizers = tuple(
        manifest
        for manifest in registered_algorithms
        if AlgorithmInterface.SEARCH_OPTIMIZER in manifest.interfaces
    )
    algorithms = tuple(
        manifest
        for manifest in registered_algorithms
        if AlgorithmInterface.SEARCH_OPTIMIZER not in manifest.interfaces
    )
    domains = default_registry.list_domains()
    runtimes = default_registry.list_runtimes()
    return _json_response(
        {
            "protocol_version": PLATFORM_PROTOCOL_VERSION,
            "algorithms": algorithms,
            "optimizers": optimizers,
            "domains": domains,
            "runtimes": runtimes,
            "interfaces": sorted(
                {
                    interface.value
                    for manifest in registered_algorithms
                    for interface in manifest.interfaces
                }
            ),
        }
    )


@router.get("/datasets")
def list_algorithm_datasets() -> dict[str, object]:
    items = tuple(_dataset_view(path, include_instances=False) for path in _dataset_manifest_paths())
    return _json_response({"items": items, "count": len(items)})


@router.get("/datasets/{dataset_id}")
def get_algorithm_dataset(dataset_id: str) -> dict[str, object]:
    return _json_response(_dataset_view(_dataset_manifest_path(dataset_id), include_instances=True))


@router.get("/datasets/{dataset_id}/results")
def get_algorithm_dataset_results(dataset_id: str) -> dict[str, object]:
    dataset = _dataset_view(_dataset_manifest_path(dataset_id), include_instances=True)
    dataset_uri = str(dataset["uri"])
    items: list[dict[str, object]] = []
    for record in _execution_repository.list():
        if record.purpose is not RunPurpose.EVALUATE or record.status is not ExecutionStatus.SUCCEEDED:
            continue
        try:
            experiment = _experiment_repository.get(record.experiment_id).spec
        except KeyError:
            continue
        plan = _compiler.compile(experiment)
        runs = {run.run_id: run for run in plan.runs}
        for result in _run_payloads(record.execution_id):
            run = runs.get(str(result.get("run_id", "")))
            if run is None or run.scenario.uri.partition("#")[0] != dataset_uri:
                continue
            manifest = default_registry.algorithm_manifest(run.algorithm.algorithm_id, run.algorithm.version)
            items.append(
                {
                    **result,
                    "dataset_id": dataset_id,
                    "dataset_name": dataset["name"],
                    "instance_id": run.scenario.metadata.get("instance_id", run.scenario.scenario_id),
                    "scenario_id": run.scenario.scenario_id,
                    "algorithm_id": run.algorithm.algorithm_id,
                    "algorithm_name": manifest.name,
                    "algorithm_version": run.algorithm.version,
                    "algorithm_parameters": run.algorithm.parameters,
                    "execution_id": record.execution_id,
                    "experiment_id": record.experiment_id,
                    "finished_at": record.finished_at,
                }
            )
    return _json_response({"dataset": dataset, "items": items, "count": len(items)})


@router.get("/experiments")
def list_experiments() -> dict[str, object]:
    execution_summaries: dict[str, dict[str, object]] = {}
    for record in _execution_repository.list():
        summary = execution_summaries.setdefault(
            record.experiment_id,
            {
                "latest_execution_id": None,
                "latest_status": None,
                "execution_count": 0,
                "latest_replay_execution_id": None,
                "latest_replay_status": None,
                "replay_count": 0,
            },
        )
        if record.purpose is RunPurpose.REPLAY:
            if summary["latest_replay_execution_id"] is None:
                summary["latest_replay_execution_id"] = record.execution_id
                summary["latest_replay_status"] = record.status
            summary["replay_count"] = int(summary["replay_count"]) + 1
            continue
        if summary["latest_execution_id"] is None:
            summary["latest_execution_id"] = record.execution_id
            summary["latest_status"] = record.status
        summary["execution_count"] = int(summary["execution_count"]) + 1
    items = tuple(
        {
            **_stored_experiment_view(stored),
            **execution_summaries.get(
                stored.experiment_id,
                {
                    "latest_execution_id": None,
                    "latest_status": None,
                    "execution_count": 0,
                    "latest_replay_execution_id": None,
                    "latest_replay_status": None,
                    "replay_count": 0,
                },
            ),
        }
        for stored in _experiment_repository.list()
    )
    return _json_response({"items": items, "count": len(items)})


@router.post("/experiments", status_code=201)
def create_experiment(payload: dict[str, object] = Body(...)) -> dict[str, object]:
    experiment = _require_inline_experiment(payload)
    try:
        stored = _experiment_repository.put(experiment)
    except FileExistsError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return _json_response(_stored_experiment_view(stored))


@router.get("/experiments/{experiment_id}")
def get_experiment(experiment_id: str) -> dict[str, object]:
    try:
        stored = _experiment_repository.get(experiment_id)
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return _json_response(_stored_experiment_view(stored))


@router.get("/executions")
def list_executions() -> dict[str, object]:
    records = _execution_repository.list()
    return _json_response({"items": records, "count": len(records)})


@router.post("/validate")
def validate_experiment(payload: dict[str, object] = Body(...)) -> dict[str, object]:
    try:
        experiment = _experiment_from_request(payload)
        compilation = _compile_view(experiment)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, yaml.YAMLError) as error:
        return _json_response(
            {
                "valid": False,
                "errors": (
                    {
                        "code": type(error).__name__,
                        "message": str(error),
                    },
                ),
            }
        )
    return _json_response(
        {
            "valid": True,
            "errors": (),
            "experiment": experiment_spec_to_mapping(experiment),
            "plan_digest": compilation["plan_digest"],
            "dynamic": compilation["dynamic"],
        }
    )


@router.post("/compile")
def compile_experiment(payload: dict[str, object] = Body(...)) -> dict[str, object]:
    experiment = _require_experiment(payload)
    try:
        compilation = _compile_view(experiment)
    except (KeyError, TypeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _json_response(
        {
            "experiment": experiment_spec_to_mapping(experiment),
            **compilation,
        }
    )


@router.post("/executions", status_code=202)
def create_execution(payload: dict[str, object] = Body(...)) -> dict[str, object]:
    experiment = _require_experiment(payload)
    mode = payload.get("mode")
    if mode not in {"auto", "execute", "tune"}:
        raise HTTPException(
            status_code=422,
            detail="mode must be one of: auto, execute, tune",
        )
    if mode == "tune" and experiment.purpose is not RunPurpose.TUNE:
        raise HTTPException(
            status_code=422,
            detail="tune mode requires an experiment with purpose 'tune'",
        )
    if mode == "execute" and experiment.purpose is RunPurpose.TUNE:
        raise HTTPException(
            status_code=422,
            detail="tuning experiments require mode 'tune' or 'auto'",
        )

    try:
        compilation = _compile_view(experiment)
    except (KeyError, TypeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    execution_id = _new_execution_id()
    try:
        stored_experiment = _experiment_repository.put(experiment)
    except FileExistsError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    record = _execution_repository.create(
        execution_id,
        experiment,
        metadata={
            "mode": (
                "tune" if experiment.purpose is RunPurpose.TUNE else "execute"
            ),
            "config_format": payload.get("config_format"),
            "experiment_digest": stored_experiment.digest,
            "dynamic": compilation["dynamic"],
        },
    )
    record = _execution_repository.compiled(
        execution_id,
        str(compilation["plan_digest"]),
    )
    cancel_event = threading.Event()
    thread = threading.Thread(
        target=_execute_in_background,
        args=(
            execution_id,
            experiment,
            compilation.get("plan"),
            cancel_event,
        ),
        name=f"algorithm-platform-{execution_id}",
        daemon=True,
    )
    with _execution_threads_lock:
        _execution_threads[execution_id] = thread
        _execution_cancel_events[execution_id] = cancel_event
    try:
        thread.start()
    except Exception as error:
        with _execution_threads_lock:
            _execution_threads.pop(execution_id, None)
            _execution_cancel_events.pop(execution_id, None)
        record = _execution_repository.failed(
            execution_id,
            _execution_failure(error),
        )
    return _json_response(record)


@router.get("/executions/{execution_id}")
def get_execution(execution_id: str) -> dict[str, object]:
    record = _require_execution(execution_id)
    stored = _require_stored_experiment(record.experiment_id)
    if record.purpose is RunPurpose.REPLAY:
        source_experiment = _stored_experiment_view(stored)
        return _json_response(
            {
                "execution": record,
                "experiment": source_experiment,
                "source_experiment": source_experiment,
                "replay": dict(record.metadata),
            }
        )
    return _json_response(
        {
            "execution": record,
            "experiment": _stored_experiment_view(stored),
        }
    )


@router.delete("/executions/{execution_id}")
def delete_execution(execution_id: str) -> dict[str, object]:
    _validate_component_identifier(execution_id, "execution_id")
    with _execution_threads_lock:
        record = _require_execution(execution_id)
        if record.purpose is not RunPurpose.TRAIN:
            raise HTTPException(status_code=409, detail="only training records can be deleted")
        if execution_id in _execution_threads or record.status not in {
            ExecutionStatus.SUCCEEDED, ExecutionStatus.FAILED, ExecutionStatus.CANCELLED,
        }:
            raise HTTPException(status_code=409, detail="训练尚未结束，请等待执行完全停止后再删除记录")
        _execution_repository.archive(execution_id, PLATFORM_ROOT / "trash" / "execution_records")
        _event_monitor.discard(execution_id)
    return {"execution_id": execution_id, "deleted": True}


@router.post("/executions/{execution_id}/cancel", status_code=202)
def cancel_execution(execution_id: str) -> dict[str, object]:
    record = _require_execution(execution_id)
    if record.status in {
        ExecutionStatus.CANCEL_REQUESTED,
        ExecutionStatus.CANCELLED,
    }:
        with _execution_threads_lock:
            cancel_event = _execution_cancel_events.get(execution_id)
        if cancel_event is not None:
            cancel_event.set()
        return _json_response(record)
    if record.status not in {
        ExecutionStatus.COMPILED,
        ExecutionStatus.RUNNING,
    }:
        raise HTTPException(
            status_code=409,
            detail=f"execution is already terminal: {record.status.value}",
        )
    with _execution_threads_lock:
        cancel_event = _execution_cancel_events.get(execution_id)
    if cancel_event is None:
        raise HTTPException(
            status_code=409,
            detail="execution does not have a cooperative background worker",
        )
    try:
        record = _execution_repository.request_cancel(execution_id)
    except ValueError as error:
        current = _execution_repository.get(execution_id)
        if current.status in {
            ExecutionStatus.CANCEL_REQUESTED,
            ExecutionStatus.CANCELLED,
        }:
            cancel_event.set()
            return _json_response(current)
        if current.status in {
            ExecutionStatus.SUCCEEDED,
            ExecutionStatus.FAILED,
        }:
            raise HTTPException(
                status_code=409,
                detail=f"execution is already terminal: {current.status.value}",
            ) from error
        raise
    cancel_event.set()
    return _json_response(record)


def _checkpoint_entries(execution_id: str) -> list[dict[str, object]]:
    _validate_component_identifier(execution_id, "execution_id")
    _require_execution(execution_id)
    root = PLATFORM_ROOT / "executions"
    directories = [root / execution_id, *sorted(root.glob(f"{execution_id}.*"))]
    entries: list[dict[str, object]] = []
    for directory in directories:
        for checkpoint_dir in sorted(directory.glob("*/checkpoints")):
            for record in list_training_checkpoints(checkpoint_dir):
                entries.append({**record, "workspace_execution_id": directory.name,
                                "run_id": checkpoint_dir.parent.name, "_directory": checkpoint_dir})
    return entries


@router.get("/executions/{execution_id}/checkpoints")
def get_execution_checkpoints(execution_id: str) -> dict[str, object]:
    return {"items": [{key: value for key, value in entry.items() if key != "_directory"}
                      for entry in _checkpoint_entries(execution_id)]}


def _checkpoint_snapshot(execution_id: str, run_id: str, name: str, workspace_execution_id: str):
    for entry in _checkpoint_entries(execution_id):
        if (entry["run_id"], entry["name"], entry["workspace_execution_id"]) == (run_id, name, workspace_execution_id):
            return read_training_checkpoint(entry["_directory"] / f"{name}.ckpt")
    raise HTTPException(status_code=404, detail="checkpoint does not exist for this execution")


@router.get("/executions/{execution_id}/checkpoints/{run_id}/{name}")
def download_training_checkpoint(execution_id: str, run_id: str, name: str, workspace_execution_id: str):
    metadata, data = _checkpoint_snapshot(execution_id, run_id, name, workspace_execution_id)
    return Response(data, media_type="application/x-pytorch",
                    headers={"Content-Disposition": f'attachment; filename="{name}.pt"'})


@router.post("/executions/{execution_id}/checkpoints/{run_id}/{name}/export")
def export_training_checkpoint(execution_id: str, run_id: str, name: str, workspace_execution_id: str):
    metadata, data = _checkpoint_snapshot(execution_id, run_id, name, workspace_execution_id)
    # Only explicitly exported snapshots enter the immutable artifact store.
    # Routine improvements atomically replace the run-local best checkpoint.
    artifact = _artifact_store.put_bytes(data, kind=ArtifactKind.CHECKPOINT,
                                        media_type="application/x-pytorch", metadata=metadata)
    return _json_response(artifact)


@router.get("/executions/{execution_id}/metrics")
def get_execution_metrics(execution_id: str) -> dict[str, object]:
    record = _require_execution(execution_id)
    runs = _run_payloads(execution_id)
    report = _execution_report_payload(record)
    execution_events = _event_monitor.summary(execution_id, _execution_event_paths(execution_id))
    metric_items: dict[tuple[str, str], dict[str, object]] = {
        (
            str(
                run.get(
                    "workspace_execution_id",
                    run.get("execution_id", execution_id),
                )
            ),
            str(run.get("run_id", "")),
        ): {
            "execution_id": run.get("execution_id", execution_id),
            "workspace_execution_id": run.get(
                "workspace_execution_id",
                run.get("execution_id", execution_id),
            ),
            "run_id": run.get("run_id"),
            "status": run.get("status"),
            "metrics": run.get("metrics", {}),
            "objective_values": run.get("objective_values", ()),
            "constraint_values": run.get("constraint_values", {}),
            "constraint_violations": run.get(
                "constraint_violations", {}
            ),
            "feasible": run.get("feasible"),
        }
        for run in runs
    }
    candidate_progress: dict[str, dict[str, object]] = {}
    benchmark_progress: tuple[object, ...] = ()
    for event in execution_events:
        if event.event_type is not EventType.METRIC_UPDATED:
            if event.event_type is EventType.CANDIDATE_PROPOSED:
                candidate_id = str(event.payload["candidate_id"])
                candidate_progress[candidate_id] = {
                    "candidate_id": candidate_id,
                    "parameters": event.payload.get("parameters", {}),
                    "status": "running",
                }
            elif event.event_type is EventType.CANDIDATE_EVALUATED:
                candidate_id = str(event.payload["candidate_id"])
                candidate_progress.setdefault(
                    candidate_id,
                    {"candidate_id": candidate_id, "parameters": {}},
                ).update(
                    {
                        **dict(event.payload),
                        "status": event.payload.get("status", "succeeded"),
                    }
                )
            elif event.event_type is EventType.BENCHMARK_FINISHED:
                results = event.payload.get("results", ())
                if isinstance(results, tuple):
                    benchmark_progress = results
            continue
        metrics = event.payload.get("metrics")
        if isinstance(metrics, Mapping):
            key = (event.execution_id, event.run_id)
            if key not in metric_items:
                metric_items[key] = {
                    "execution_id": event.execution_id,
                    "run_id": event.run_id,
                    "status": "running",
                    "metrics": metrics,
                    "objective_values": (),
                    "constraint_values": {},
                    "constraint_violations": {},
                    "feasible": None,
                }
            elif metric_items[key]["status"] == "running":
                metric_items[key]["metrics"] = metrics
    response: dict[str, object] = {
        "execution_id": execution_id,
        "status": record.status,
        "items": tuple(metric_items[key] for key in sorted(metric_items)),
    }
    if report is not None and "candidate_results" in report:
        response["candidates"] = report.get("candidate_results", ())
        response["incumbents"] = report.get("incumbents", ())
        response["benchmark"] = report.get("benchmark_results", ())
    elif candidate_progress:
        response["candidates"] = tuple(candidate_progress.values())
        response["benchmark"] = benchmark_progress
    return _json_response(response)


@router.get("/executions/{execution_id}/logs")
def get_execution_logs(
    execution_id: str,
    cursor: str | None = Query(default=None, pattern=r"^[a-f0-9]{32}:[0-9]+$"),
    limit: int = Query(default=1000, ge=1, le=2000),
    tail: bool = False,
    before: str | None = Query(default=None, pattern=r"^[a-f0-9]{32}:[0-9]+$"),
    level: str = Query(default="debug", pattern=r"^(debug|info|warning|error)$"),
) -> Response:
    _require_execution(execution_id)
    return Response(
        content=_event_monitor.page(execution_id, _execution_event_paths(execution_id), cursor, limit,
                                    tail=tail, before=before, level=level),
        media_type="application/json",
    )


@router.get("/executions/{execution_id}/training-monitor")
def get_training_monitor(execution_id: str) -> Response:
    _require_execution(execution_id)
    return Response(content=_event_monitor.training(execution_id, _execution_event_paths(execution_id)),
                    media_type="application/json")


@router.get("/executions/{execution_id}/manifest")
def get_execution_manifest(execution_id: str) -> dict[str, object]:
    record = _require_execution(execution_id)
    if record.result_manifest_id is None:
        raise HTTPException(
            status_code=409,
            detail="execution has not published a result manifest",
        )
    try:
        manifest = _artifact_store.read_manifest(record.result_manifest_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return _json_response(manifest)


@router.get("/executions/{execution_id}/comparison")
def get_execution_comparison(execution_id: str) -> dict[str, object]:
    record = _require_execution(execution_id)
    report = _execution_report_payload(record)
    comparison = None if report is None else report.get("comparison_report")
    if comparison is None:
        raise HTTPException(
            status_code=404,
            detail="execution does not contain an algorithm comparison report",
        )
    if not isinstance(comparison, Mapping):
        raise HTTPException(
            status_code=500,
            detail="comparison report artifact is not an object",
        )
    algorithms = tuple(
        {
            **dict(item),
            "complete": (
                item.get("observed_run_count") == item.get("expected_run_count")
                and item.get("authoritative_run_count")
                == item.get("expected_run_count")
            ),
        }
        for item in comparison.get("algorithms", ())
        if isinstance(item, Mapping)
    )
    complete = bool(
        _object_value(comparison.get("design", {}), "comparison.design").get(
            "balanced",
            False,
        )
        and all(bool(item["complete"]) for item in algorithms)
    )
    return _json_response(
        {**dict(comparison), "algorithms": algorithms, "complete": complete}
    )


@router.get("/executions/{execution_id}/runs")
def get_execution_runs(execution_id: str) -> dict[str, object]:
    record = _require_execution(execution_id)
    runs = _run_payloads(execution_id)
    return _json_response(
        {
            "execution_id": execution_id,
            "status": record.status,
            "items": runs,
            "count": len(runs),
        }
    )


@router.get("/runs/{run_id}/replay")
def get_run_replay(
    run_id: str,
    mode: str = Query("full", pattern="^(full|decision)$"),
    execution_id: str | None = Query(None),
) -> dict[str, object]:
    trace = _load_run_trace(run_id, execution_id)
    source_run = _source_run_for_trace(trace, run_id)
    diagnostics = ReplayDiagnostics().inspect(trace)
    items = trace.events if mode == "full" else extract_decisions(trace)
    try:
        factory_config = _factory_config_for_run(source_run)
    except (FileNotFoundError, OSError, ValueError):
        factory_config = None
    return _json_response(
        {
            "trace": trace,
            "diagnostics": diagnostics,
            "view": mode,
            "items": items,
            "factory_config": factory_config,
        }
    )


@router.post(
    "/executions/{execution_id}/runs/{run_id}/replay/reproduce",
    status_code=201,
)
def reproduce_run(
    execution_id: str,
    run_id: str,
    payload: dict[str, object] = Body(...),
) -> dict[str, object]:
    source_record = _require_execution(execution_id)
    source = _load_run_trace(run_id, execution_id)
    diagnostics = ReplayDiagnostics().inspect(source)
    if not diagnostics.deterministic_replay_ready:
        raise HTTPException(
            status_code=409,
            detail="source trace is not ready for deterministic reproduction",
        )
    source_run = _load_source_run(source.execution_id, run_id)
    replay_execution_id = _optional_string(payload.get("execution_id"))
    if replay_execution_id is None:
        replay_execution_id = _new_replay_execution_id("reproduce")
    _validate_component_identifier(replay_execution_id, "execution_id")
    if replay_execution_id == source.execution_id:
        raise HTTPException(
            status_code=422,
            detail="replay execution_id must differ from the source execution",
        )
    try:
        policy = _replay_diff_policy(payload.get("policy", {}))
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    _create_replay_execution_record(
        replay_execution_id,
        source_record,
        source_run,
        source_run.algorithm,
        source_execution_id=source.execution_id,
        plan_digest=replay_plan_digest(
            ReplayMode.DETERMINISTIC,
            source.execution_id,
            source.run_id,
            source_run,
        ),
        mode="deterministic",
    )
    try:
        report = _replay_orchestrator.reproduce(
            source,
            source_run,
            execution_id=replay_execution_id,
            diff_policy=policy,
        )
        _execution_repository.succeeded(
            replay_execution_id,
            report.execution_manifest.manifest_id,
        )
    except FileExistsError as error:
        _fail_replay_execution(replay_execution_id, error)
        raise HTTPException(status_code=409, detail=str(error)) from error
    except (KeyError, TypeError, ValueError) as error:
        _fail_replay_execution(replay_execution_id, error)
        raise HTTPException(status_code=422, detail=str(error)) from error
    except Exception as error:
        _fail_replay_execution(replay_execution_id, error)
        raise
    return _replay_execution_view(report)


@router.post(
    "/executions/{execution_id}/runs/{run_id}/replay/branch",
    status_code=201,
)
def branch_run(
    execution_id: str,
    run_id: str,
    payload: dict[str, object] = Body(...),
) -> dict[str, object]:
    source_record = _require_execution(execution_id)
    source = _load_run_trace(run_id, execution_id)
    diagnostics = ReplayDiagnostics().inspect(source)
    if not diagnostics.branch_replay_ready:
        raise HTTPException(
            status_code=409,
            detail="source trace is not ready for snapshot branching",
        )
    source_run = _load_source_run(source.execution_id, run_id)
    replay_execution_id = _required_string(payload, "execution_id")
    replay_run_id = _required_string(payload, "run_id")
    _validate_component_identifier(replay_execution_id, "execution_id")
    _validate_component_identifier(replay_run_id, "run_id")
    if replay_execution_id == source.execution_id:
        raise HTTPException(
            status_code=422,
            detail="branch execution_id must differ from the source execution",
        )
    if replay_run_id == source.run_id:
        raise HTTPException(
            status_code=422,
            detail="branch run_id must differ from the source run",
        )

    sequence = payload.get("snapshot_sequence")
    if isinstance(sequence, bool) or not isinstance(sequence, int):
        raise HTTPException(
            status_code=422,
            detail="snapshot_sequence must be an integer",
        )
    anchor = next(
        (
            item
            for item in source.snapshots
            if item.after_sequence == sequence
        ),
        None,
    )
    if anchor is None:
        raise HTTPException(
            status_code=422,
            detail=f"source trace has no snapshot at sequence {sequence}",
        )
    algorithm_value = payload.get("algorithm")
    if not isinstance(algorithm_value, Mapping):
        raise HTTPException(
            status_code=422,
            detail="algorithm must be an AlgorithmRef object",
        )
    source_experiment = _require_stored_experiment(
        source_record.experiment_id
    ).spec
    try:
        algorithm = _algorithm_ref_from_public_mapping(
            algorithm_value,
            source_experiment,
        )
        metadata_value = payload.get("metadata", {})
        if not isinstance(metadata_value, Mapping):
            raise TypeError("metadata must be an object")
        request = BranchReplayRequest(
            source=source,
            anchor=anchor,
            algorithm=algorithm,
            execution_id=replay_execution_id,
            run_id=replay_run_id,
            metadata=dict(metadata_value),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    _create_replay_execution_record(
        replay_execution_id,
        source_record,
        source_run,
        algorithm,
        source_execution_id=source.execution_id,
        plan_digest=replay_plan_digest(
            ReplayMode.BRANCH,
            source.execution_id,
            source.run_id,
            branch_run_spec(request, source_run),
        ),
        mode="branch",
    )
    try:
        report = _replay_orchestrator.branch(request, source_run)
        _execution_repository.succeeded(
            replay_execution_id,
            report.execution_manifest.manifest_id,
        )
    except FileExistsError as error:
        _fail_replay_execution(replay_execution_id, error)
        raise HTTPException(status_code=409, detail=str(error)) from error
    except (KeyError, TypeError, ValueError) as error:
        _fail_replay_execution(replay_execution_id, error)
        raise HTTPException(status_code=422, detail=str(error)) from error
    except Exception as error:
        _fail_replay_execution(replay_execution_id, error)
        raise
    return _replay_execution_view(report)


@router.post("/replay/diff")
def diff_replays(payload: dict[str, object] = Body(...)) -> dict[str, object]:
    try:
        left_run_id = _required_string(payload, "left_run_id")
        right_run_id = _required_string(payload, "right_run_id")
        mode = payload.get("mode", "full")
        if mode not in {"full", "decision"}:
            raise ValueError("mode must be 'full' or 'decision'")
        left_execution_id = _optional_string(payload.get("left_execution_id"))
        right_execution_id = _optional_string(payload.get("right_execution_id"))
        left = _load_run_trace(left_run_id, left_execution_id)
        right = _load_run_trace(right_run_id, right_execution_id)
        policy = _replay_diff_policy(payload.get("policy", {}))
        report = ReplayDiffer().compare(
            left,
            right,
            view=(
                ReplayView.FULL
                if mode == "full"
                else ReplayView.DECISIONS
            ),
            policy=policy,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _diff_report_view(report)


@router.get("/artifacts/{digest:path}")
def get_artifact(digest: str) -> FileResponse:
    try:
        path = _artifact_store.resolve_digest(digest)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error

    media_type = "application/octet-stream"
    for manifest in _artifact_store.iter_manifests():
        match = next(
            (artifact for artifact in manifest.artifacts if artifact.digest == digest),
            None,
        )
        if match is not None:
            media_type = match.media_type
            break
    return FileResponse(
        path,
        media_type=media_type,
        headers={"X-Artifact-Digest": digest},
    )


def _experiment_from_request(payload: Mapping[str, object]) -> ExperimentSpec:
    experiment_id = payload.get("experiment_id")
    if experiment_id is not None:
        if "config" in payload or "config_format" in payload:
            raise ValueError(
                "provide either experiment_id or config_format/config, not both"
            )
        if not isinstance(experiment_id, str) or not experiment_id.strip():
            raise TypeError("experiment_id must be a non-empty string")
        return _experiment_repository.get(experiment_id).spec

    config_format = _required_string(payload, "config_format").lower()
    if config_format not in {"json", "yaml", "yml"}:
        raise ValueError("config_format must be 'json' or 'yaml'")
    config = payload["config"]
    if isinstance(config, Mapping):
        value = config
    elif isinstance(config, str):
        value = (
            json.loads(config)
            if config_format == "json"
            else yaml.safe_load(config)
        )
    else:
        raise TypeError("config must be an object or a JSON/YAML string")
    if not isinstance(value, Mapping):
        raise TypeError("experiment configuration must be an object")
    return experiment_spec_from_mapping(value)


def _require_experiment(payload: Mapping[str, object]) -> ExperimentSpec:
    try:
        return _experiment_from_request(payload)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, yaml.YAMLError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


def _require_inline_experiment(payload: Mapping[str, object]) -> ExperimentSpec:
    if "experiment_id" in payload:
        raise HTTPException(
            status_code=422,
            detail="creating an experiment requires config_format and config",
        )
    return _require_experiment(payload)


def _validate_scenario_references(experiment: ExperimentSpec) -> None:
    """Resolve DFJSP-T data before accepting a new execution."""
    if experiment.domain.domain_id != DFJSPT_DOMAIN_MANIFEST.domain_id:
        return
    adapter = default_registry.create_domain(experiment.domain)
    scenarios: list[ScenarioRef] = list(experiment.scenarios)
    if experiment.tuning is not None:
        scenarios.extend(experiment.tuning.training_scenarios)
        scenarios.extend(experiment.tuning.benchmark_scenarios)
    scenarios.extend(
        ScenarioRef(
            scenario_id=str(item["scenario_id"]), uri=str(item["uri"]),
            digest=str(item["digest"]), metadata=dict(item.get("metadata", {})),
        )
        for item in experiment.domain.parameters.get("validation_scenarios", ())
    )
    errors: list[str] = []
    for scenario in scenarios:
        try:
            adapter.load_problem(scenario)
        except (FileNotFoundError, KeyError, ValueError) as error:
            errors.append(f"{scenario.scenario_id}: {error}")
    if errors:
        raise ValueError(
            "数据集引用不可用，请在实验配置中点击“更新数据集引用”；"
            "若所选实例已不存在，请重新选择数据集。\n" + "\n".join(errors)
        )


def _compile_view(experiment: ExperimentSpec) -> dict[str, object]:
    if experiment.purpose in (RunPurpose.TRAIN, RunPurpose.TUNE) and any(
        algorithm.algorithm_id == "ctde_ppo" and algorithm.interface is AlgorithmInterface.TRAINABLE
        for algorithm in experiment.algorithms
    ):
        from experiment.algorithm_platform_plugins.dfjsp_t.ppo import PPO_SELECTION_OBJECTIVE
        if experiment.objective != PPO_SELECTION_OBJECTIVE:
            raise ValueError("PPO 选模目标必须为单目标最大化验证平均累计奖励 episode_reward，请更新实验配置")
    _validate_scenario_references(experiment)
    if experiment.purpose is RunPurpose.TUNE:
        return {
            "plan_digest": _compiler.digest(experiment),
            "dynamic": True,
            "plan": None,
        }
    plan = _compiler.compile(experiment)
    return {
        "plan_digest": plan.plan_digest,
        "dynamic": False,
        "plan": plan,
    }


def _execute_in_background(
    execution_id: str,
    experiment: ExperimentSpec,
    plan: ExecutionPlan | None,
    cancel_event: threading.Event,
) -> None:
    try:
        failed_runs = ()
        if cancel_event.is_set():
            _cancel_execution_record(execution_id)
            return
        _execution_repository.started(execution_id)
        if experiment.purpose is RunPurpose.TUNE:
            report = _tuning_orchestrator.execute(
                experiment,
                execution_id=execution_id,
                cancel_event=cancel_event,
            )
            manifest_id = report.artifact_manifest.manifest_id
        else:
            if plan is None:
                raise ValueError("standard execution is missing its compiled plan")
            report = _orchestrator.execute(
                plan,
                execution_id=execution_id,
                cancel_event=cancel_event,
            )
            manifest_id = report.execution_manifest.manifest_id
            failed_runs = tuple(
                result
                for result in report.run_results
                if result.status in {RunStatus.FAILED, RunStatus.TIMED_OUT}
            )
        current = _execution_repository.get(execution_id)
        if (
            cancel_event.is_set()
            or current.status is ExecutionStatus.CANCEL_REQUESTED
        ):
            _cancel_execution_record(execution_id)
        elif failed_runs:
            _execution_repository.failed(
                execution_id,
                RunFailure(
                    code="run_failed",
                    message=f"{len(failed_runs)} run(s) did not complete successfully",
                    details={
                        "runs": tuple(
                            {
                                "run_id": result.run_id,
                                "status": result.status.value,
                                "failure": (
                                    None
                                    if result.failure is None
                                    else {
                                        "code": result.failure.code,
                                        "message": result.failure.message,
                                    }
                                ),
                            }
                            for result in failed_runs
                        )
                    },
                ),
                result_manifest_id=manifest_id,
            )
        else:
            _execution_repository.succeeded(execution_id, manifest_id)
    except AlgorithmExecutionCancelled:
        _cancel_execution_record(execution_id)
    except Exception as error:
        current = _execution_repository.get(execution_id)
        if (
            cancel_event.is_set()
            or current.status is ExecutionStatus.CANCEL_REQUESTED
        ):
            _cancel_execution_record(execution_id)
        elif current.status in {
            ExecutionStatus.DRAFT,
            ExecutionStatus.COMPILED,
            ExecutionStatus.RUNNING,
        }:
            _execution_repository.failed(
                execution_id,
                _execution_failure(error),
            )
    finally:
        with _execution_threads_lock:
            _execution_threads.pop(execution_id, None)
            _execution_cancel_events.pop(execution_id, None)


def _cancel_execution_record(execution_id: str) -> ExecutionRecord:
    current = _execution_repository.get(execution_id)
    if current.status in {
        ExecutionStatus.COMPILED,
        ExecutionStatus.RUNNING,
    }:
        try:
            current = _execution_repository.request_cancel(execution_id)
        except ValueError:
            current = _execution_repository.get(execution_id)
    if current.status is ExecutionStatus.CANCEL_REQUESTED:
        try:
            return _execution_repository.cancelled(execution_id)
        except ValueError:
            return _execution_repository.get(execution_id)
    return current


def _execution_failure(error: Exception) -> RunFailure:
    return RunFailure(
        code=type(error).__name__,
        message=str(error) or type(error).__name__,
        details={"exception_type": type(error).__name__},
    )


def _create_replay_execution_record(
    execution_id: str,
    source_record: ExecutionRecord,
    source_run: RunSpec,
    algorithm: AlgorithmRef,
    *,
    source_execution_id: str,
    plan_digest: str,
    mode: str,
) -> None:
    source_experiment = _require_stored_experiment(
        source_record.experiment_id
    ).spec
    try:
        _execution_repository.create(
            execution_id,
            source_experiment,
            purpose=RunPurpose.REPLAY,
            metadata={
                "mode": "replay",
                "replay_mode": mode,
                "parent_execution_id": source_record.execution_id,
                "source_experiment_id": source_experiment.experiment_id,
                "source_execution_id": source_execution_id,
                "source_run_id": source_run.run_id,
                "source_scenario_id": source_run.scenario.scenario_id,
                "algorithm_id": algorithm.algorithm_id,
                "algorithm_version": algorithm.version,
            },
        )
    except FileExistsError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    _execution_repository.compiled(execution_id, plan_digest)
    _execution_repository.started(execution_id)


def _fail_replay_execution(execution_id: str, error: Exception) -> None:
    _execution_repository.failed(
        execution_id,
        _execution_failure(error),
    )


def _require_execution(execution_id: str) -> ExecutionRecord:
    try:
        return _execution_repository.get(execution_id)
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


def _require_stored_experiment(experiment_id: str) -> StoredExperiment:
    try:
        return _experiment_repository.get(experiment_id)
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


def _dataset_manifest_paths() -> tuple[Path, ...]:
    return tuple(
        path
        for path in sorted(DATASET_ROOT.glob("*/manifest.json"))
        if tuple(path.parent.glob("*.jsonl"))
    )


def _dataset_manifest_path(dataset_id: str) -> Path:
    path = DATASET_ROOT / dataset_id / "manifest.json"
    if path not in _dataset_manifest_paths():
        raise HTTPException(status_code=404, detail=f"unknown dataset: {dataset_id}")
    return path


def _dataset_view(manifest_path: Path, *, include_instances: bool) -> dict[str, object]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    data_path = next(iter(sorted(manifest_path.parent.glob("*.jsonl"))))
    split = "training" if manifest["split"] == "train" else str(manifest["split"])
    dataset_id = manifest_path.parent.name
    names = {
        "dfjsp_t_train": "DFJSP-T 训练集",
        "dfjsp_t_validation": "DFJSP-T 调优集",
        "dfjsp_t_benchmark": "DFJSP-T 测试集",
    }
    value: dict[str, object] = {
        "dataset_id": dataset_id,
        "name": names.get(dataset_id, dataset_id),
        "domain_id": "dfjsp_t",
        "split": split,
        "count": int(manifest["count"]),
        "uri": data_path.relative_to(ROOT).as_posix(),
        "digest": f"sha256:{hashlib.sha256(data_path.read_bytes()).hexdigest()}",
    }
    if include_instances:
        value["instances"] = tuple(
            {
                "instance_id": item["instance_id"],
                "digest": f"sha256:{item['canonical_hash']}",
                "seed": item.get("seed"),
                "profile": item.get("profile"),
            }
            for item in manifest["instances"]
        )
    return value


def _stored_experiment_view(stored: StoredExperiment) -> dict[str, object]:
    return {
        "experiment_id": stored.experiment_id,
        "digest": stored.digest,
        "created_at": stored.created_at,
        "spec": experiment_spec_to_mapping(stored.spec),
    }


def _execution_report_payload(
    record: ExecutionRecord,
) -> dict[str, object] | None:
    if record.result_manifest_id is None:
        return None
    try:
        manifest = _artifact_store.read_manifest(record.result_manifest_id)
    except FileNotFoundError:
        return None
    reports = tuple(
        artifact
        for artifact in manifest.artifacts
        if artifact.kind is ArtifactKind.REPORT
    )
    preferred = next(
        (
            artifact
            for artifact in reports
            if artifact.metadata.get("report_type") == "execution"
        ),
        None,
    )
    artifact = preferred or (reports[-1] if reports else None)
    if artifact is None:
        return None
    value = json.loads(_artifact_store.read_bytes(artifact))
    if not isinstance(value, dict):
        raise TypeError("execution report artifact must contain a JSON object")
    return value


def _run_payloads(execution_id: str) -> tuple[dict[str, object], ...]:
    record = _require_execution(execution_id)
    report = _execution_report_payload(record)
    if report is not None and "run_results" in report:
        return tuple(
            {
                **dict(item),
                "execution_id": execution_id,
                "owner_execution_id": execution_id,
                "route_execution_id": execution_id,
                "workspace_execution_id": execution_id,
            }
            for item in report["run_results"]
            if isinstance(item, Mapping)
        )

    child_ids: tuple[str, ...] = ()
    if report is not None:
        child_ids = tuple(
            str(child_id)
            for key in (
                "training_execution_ids",
                "optimization_execution_ids",
                "benchmark_execution_ids",
            )
            for child_id in report.get(key, ())
        )
    run_payloads: dict[tuple[str, str], dict[str, object]] = {}
    accepted_execution_ids = {execution_id, *child_ids}
    for manifest in _artifact_store.iter_manifests():
        manifest_execution_id = str(
            manifest.metadata.get("execution_id", "")
        )
        if manifest_execution_id not in accepted_execution_ids and not (
            report is None
            and manifest_execution_id.startswith(f"{execution_id}.")
        ):
            continue
        run_id = manifest.metadata.get("run_id")
        if not isinstance(run_id, str):
            continue
        for artifact in reversed(manifest.artifacts):
            if artifact.kind is not ArtifactKind.REPORT:
                continue
            value = json.loads(_artifact_store.read_bytes(artifact))
            if (
                isinstance(value, dict)
                and value.get("run_id") == run_id
                and "status" in value
                and "metrics" in value
            ):
                run_payloads[(manifest_execution_id, run_id)] = {
                    **value,
                    "execution_id": execution_id,
                    "owner_execution_id": execution_id,
                    "route_execution_id": execution_id,
                    "workspace_execution_id": manifest_execution_id,
                }
                break
    for event in _event_monitor.summary(execution_id, _execution_event_paths(execution_id)):
        key = (event.execution_id, event.run_id)
        if event.event_type is EventType.RUN_STARTED:
            run_payloads.setdefault(
                key,
                {
                    "run_id": event.run_id,
                    "status": "running",
                    "metrics": {},
                    "objective_values": (),
                    "artifacts": (),
                    "algorithm_id": event.payload.get("algorithm_id"),
                    "algorithm_version": event.payload.get("algorithm_version"),
                    "scenario_id": event.payload.get("scenario_id"),
                    "execution_id": execution_id,
                    "owner_execution_id": execution_id,
                    "route_execution_id": execution_id,
                    "workspace_execution_id": event.execution_id,
                },
            )
        elif event.event_type is EventType.METRIC_UPDATED and key in run_payloads:
            metrics = event.payload.get("metrics")
            if isinstance(metrics, Mapping):
                run_payloads[key]["metrics"] = dict(metrics)
        elif event.event_type is EventType.RUN_FINISHED and key in run_payloads:
            run_payloads[key]["status"] = event.payload.get(
                "status",
                "completed",
            )
    if record.status in {
        ExecutionStatus.FAILED,
        ExecutionStatus.CANCELLED,
    }:
        for payload in run_payloads.values():
            if payload["status"] == "running":
                payload["status"] = record.status
    return tuple(run_payloads[key] for key in sorted(run_payloads))


def _execution_event_paths(execution_id: str) -> tuple[Path, ...]:
    paths: list[Path] = []
    standard_root = PLATFORM_ROOT / "executions"
    direct = standard_root / execution_id
    if direct.is_dir():
        paths.extend(direct.rglob("events.jsonl"))
    if standard_root.is_dir():
        for child in standard_root.glob(f"{execution_id}.*"):
            if child.is_dir():
                paths.extend(child.rglob("events.jsonl"))
    tuning_log = PLATFORM_ROOT / "tuning" / execution_id / "events.jsonl"
    if tuning_log.is_file():
        paths.append(tuning_log)

    if not paths:
        event_artifacts = {}
        for manifest in _artifact_store.iter_manifests():
            manifest_execution_id = str(
                manifest.metadata.get("execution_id", "")
            )
            if not (
                manifest_execution_id == execution_id
                or manifest_execution_id.startswith(f"{execution_id}.")
            ):
                continue
            for artifact in manifest.artifacts:
                if artifact.kind is ArtifactKind.EVENT_LOG:
                    event_artifacts[artifact.digest] = artifact
        paths.extend(
            _verified_artifact_path(event_artifacts[digest])
            for digest in sorted(event_artifacts)
        )

    return tuple(sorted(set(paths)))


def _load_run_trace(
    run_id: str,
    execution_id: str | None,
) -> ReplayTrace:
    _validate_component_identifier(run_id, "run_id")
    if execution_id is not None:
        _validate_component_identifier(execution_id, "execution_id")
    candidates = _run_event_logs(run_id, execution_id)
    if not candidates:
        raise HTTPException(status_code=404, detail=f"unknown run: {run_id}")
    if len(candidates) > 1:
        execution_ids = tuple(item[0] for item in candidates)
        raise HTTPException(
            status_code=409,
            detail={
                "message": "run_id exists in multiple executions; provide execution_id",
                "execution_ids": execution_ids,
            },
        )
    selected_execution_id, path, artifact = candidates[0]
    provenance = _replay_provenance(selected_execution_id)
    trace = load_replay_trace(
        path,
        mode=(
            ReplayMode.RECORDED
            if provenance is None
            else ReplayMode(str(provenance["mode"]))
        ),
        event_log=artifact,
        metadata=(
            {"source_execution_id": selected_execution_id}
            if provenance is None
            else {
                "replay_execution_id": selected_execution_id,
                "source_execution_id": provenance["source_execution_id"],
                "source_run_id": provenance["source_run_id"],
            }
        ),
    )
    if provenance is not None:
        trace = replace(
            trace,
            parent_run_id=(
                None
                if provenance.get("parent_run_id") is None
                else str(provenance["parent_run_id"])
            ),
            branch_after_sequence=(
                None
                if provenance.get("branch_after_sequence") is None
                else int(provenance["branch_after_sequence"])
            ),
        )
    return trace


def _replay_provenance(execution_id: str) -> dict[str, object] | None:
    for manifest in _artifact_store.iter_manifests():
        if (
            manifest.metadata.get("execution_id") != execution_id
            or manifest.metadata.get("replay_mode") is None
        ):
            continue
        report_artifact = next(
            (
                artifact
                for artifact in manifest.artifacts
                if artifact.kind is ArtifactKind.REPORT
                and artifact.metadata.get("report_type") == "replay"
            ),
            None,
        )
        report_value: Mapping[str, object] = {}
        if report_artifact is not None:
            loaded = json.loads(_artifact_store.read_bytes(report_artifact))
            report_value = _object_value(loaded, "replay report")
        return {
            "mode": manifest.metadata["replay_mode"],
            "source_execution_id": manifest.metadata["source_execution_id"],
            "source_run_id": manifest.metadata["source_run_id"],
            "parent_run_id": report_value.get("parent_run_id"),
            "branch_after_sequence": report_value.get(
                "branch_after_sequence"
            ),
        }
    return None


def _source_run_for_trace(trace: ReplayTrace, run_id: str) -> RunSpec:
    return _load_source_run(trace.execution_id, run_id)


def _load_source_run(execution_id: str, run_id: str) -> RunSpec:
    plan_artifacts: dict[str, ArtifactRef] = {}
    for manifest in _artifact_store.iter_manifests():
        for artifact in manifest.artifacts:
            if (
                artifact.kind is ArtifactKind.EXECUTION_PLAN
                and artifact.metadata.get("execution_id") == execution_id
            ):
                plan_artifacts[artifact.digest] = artifact
    if not plan_artifacts:
        raise HTTPException(
            status_code=409,
            detail=(
                "source execution has not published an immutable execution plan"
            ),
        )

    matches: list[Mapping[str, object]] = []
    for artifact in plan_artifacts.values():
        if not _artifact_store.verify(artifact):
            raise OSError("source execution plan failed integrity verification")
        value = json.loads(_artifact_store.read_bytes(artifact))
        if not isinstance(value, Mapping):
            raise TypeError("execution plan artifact must contain a JSON object")
        runs = value.get("runs")
        if not isinstance(runs, list):
            raise TypeError("execution plan runs must be an array")
        matches.extend(
            item
            for item in runs
            if isinstance(item, Mapping) and item.get("run_id") == run_id
        )
    if not matches:
        raise HTTPException(
            status_code=404,
            detail=f"run {run_id!r} is not present in the persisted plan",
        )
    if len(matches) != 1:
        raise HTTPException(
            status_code=409,
            detail=f"run {run_id!r} appears in multiple persisted plans",
        )
    return _run_spec_from_mapping(matches[0])


def _run_spec_from_mapping(value: Mapping[str, object]) -> RunSpec:
    domain_value = _object_value(value["domain"], "run.domain")
    algorithm_value = _object_value(value["algorithm"], "run.algorithm")
    scenario_value = _object_value(value["scenario"], "run.scenario")
    objective_value = _object_value(value["objective"], "run.objective")
    budget_value = _object_value(value["budget"], "run.budget")
    seeds_value = _object_value(value["seeds"], "run.seeds")
    algorithm_budget_value = algorithm_value.get("budget")
    return RunSpec(
        run_id=str(value["run_id"]),
        trial_id=str(value["trial_id"]),
        experiment_id=str(value["experiment_id"]),
        domain=DomainRef(
            domain_id=str(domain_value["domain_id"]),
            version=str(domain_value["version"]),
            parameters=dict(
                _object_value(
                    domain_value.get("parameters", {}),
                    "run.domain.parameters",
                )
            ),
        ),
        algorithm=AlgorithmRef(
            algorithm_id=str(algorithm_value["algorithm_id"]),
            version=str(algorithm_value["version"]),
            interface=AlgorithmInterface(str(algorithm_value["interface"])),
            budget=(
                None
                if algorithm_budget_value is None
                else _budget_from_mapping(
                    _object_value(
                        algorithm_budget_value,
                        "run.algorithm.budget",
                    )
                )
            ),
            parameters=dict(
                _object_value(
                    algorithm_value.get("parameters", {}),
                    "run.algorithm.parameters",
                )
            ),
            input_artifacts=tuple(
                _artifact_ref_from_mapping(
                    _object_value(item, "run.algorithm.input_artifacts[]")
                )
                for item in _array_value(
                    algorithm_value.get("input_artifacts", []),
                    "run.algorithm.input_artifacts",
                )
            ),
        ),
        scenario=ScenarioRef(
            scenario_id=str(scenario_value["scenario_id"]),
            uri=str(scenario_value["uri"]),
            digest=str(scenario_value["digest"]),
            metadata=dict(
                _object_value(
                    scenario_value.get("metadata", {}),
                    "run.scenario.metadata",
                )
            ),
        ),
        objective=_objective_from_mapping(objective_value),
        purpose=RunPurpose(str(value["purpose"])),
        budget=_budget_from_mapping(budget_value),
        seeds=RunSeeds(
            algorithm=int(seeds_value["algorithm"]),
            environment=int(seeds_value["environment"]),
            instance=int(seeds_value["instance"]),
            exogenous={
                str(name): int(seed)
                for name, seed in _object_value(
                    seeds_value.get("exogenous", {}),
                    "run.seeds.exogenous",
                ).items()
            },
        ),
        repetition=int(value.get("repetition", 0)),
        input_artifacts=tuple(
            _artifact_ref_from_mapping(
                _object_value(item, "run.input_artifacts[]")
            )
            for item in _array_value(
                value.get("input_artifacts", []),
                "run.input_artifacts",
            )
        ),
        metadata=dict(
            _object_value(value.get("metadata", {}), "run.metadata")
        ),
    )


def _budget_from_mapping(value: Mapping[str, object]) -> Budget:
    return Budget(
        wall_time_seconds=_optional_float(value.get("wall_time_seconds")),
        decision_time_seconds=_optional_float(
            value.get("decision_time_seconds")
        ),
        max_iterations=_optional_integer(value.get("max_iterations")),
        max_evaluations=_optional_integer(value.get("max_evaluations")),
        max_steps=_optional_integer(value.get("max_steps")),
        cpu_cores=_optional_float(value.get("cpu_cores")),
        gpu_count=_optional_integer(value.get("gpu_count")),
        memory_bytes=_optional_integer(value.get("memory_bytes")),
    )


def _objective_from_mapping(value: Mapping[str, object]) -> ObjectiveSpec:
    return ObjectiveSpec(
        mode=ComparisonMode(str(value["mode"])),
        components=tuple(
            ObjectiveComponent(
                name=str(component["name"]),
                metric=str(component["metric"]),
                direction=Direction(str(component["direction"])),
                weight=float(component.get("weight", 1.0)),
                aggregation=Aggregation(
                    str(component.get("aggregation", "mean"))
                ),
            )
            for component in (
                _object_value(item, "run.objective.components[]")
                for item in _array_value(
                    value["components"],
                    "run.objective.components",
                )
            )
        ),
        constraints=tuple(
            ConstraintSpec(
                metric=str(constraint["metric"]),
                operator=ConstraintOperator(str(constraint["operator"])),
                threshold=float(constraint["threshold"]),
                aggregation=Aggregation(
                    str(constraint.get("aggregation", "max"))
                ),
                tolerance=float(constraint.get("tolerance", 0.0)),
                name=(
                    None
                    if constraint.get("name") is None
                    else str(constraint["name"])
                ),
            )
            for constraint in (
                _object_value(item, "run.objective.constraints[]")
                for item in _array_value(
                    value.get("constraints", []),
                    "run.objective.constraints",
                )
            )
        ),
        feasibility_first=bool(value.get("feasibility_first", True)),
    )


def _artifact_ref_from_mapping(value: Mapping[str, object]) -> ArtifactRef:
    return ArtifactRef(
        digest=str(value["digest"]),
        kind=ArtifactKind(str(value["kind"])),
        media_type=str(value["media_type"]),
        size_bytes=int(value["size_bytes"]),
        uri=str(value["uri"]),
        metadata=dict(
            _object_value(value.get("metadata", {}), "artifact.metadata")
        ),
    )


def _algorithm_ref_from_public_mapping(
    value: Mapping[str, object],
    source_experiment: ExperimentSpec,
) -> AlgorithmRef:
    experiment_value = experiment_spec_to_mapping(source_experiment)
    experiment_value["algorithms"] = (dict(value),)
    return experiment_spec_from_mapping(experiment_value).algorithms[0]


def _factory_config_for_run(run: RunSpec) -> object | None:
    if run.domain.domain_id != DFJSPT_DOMAIN_MANIFEST.domain_id:
        return None
    domain = default_registry.create_domain(run.domain)
    problem = domain.load_problem(run.scenario)
    return None if problem.is_corpus else problem.instance


def _object_value(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be an object")
    return value


def _array_value(value: object, name: str) -> list[object]:
    if not isinstance(value, list):
        raise TypeError(f"{name} must be an array")
    return value


def _optional_float(value: object) -> float | None:
    return None if value is None else float(value)


def _optional_integer(value: object) -> int | None:
    return None if value is None else int(value)


def _run_event_logs(
    run_id: str,
    execution_id: str | None,
) -> tuple[tuple[str, Path, object | None], ...]:
    artifact_matches: dict[str, tuple[str, Path, ArtifactRef]] = {}
    for manifest in _artifact_store.iter_manifests():
        if manifest.metadata.get("run_id") != run_id:
            continue
        discovered_execution_id = str(manifest.metadata.get("execution_id", ""))
        if execution_id is not None and not (
            discovered_execution_id == execution_id
            or discovered_execution_id.startswith(f"{execution_id}.")
        ):
            continue
        artifact = next(
            (
                item
                for item in manifest.artifacts
                if item.kind is ArtifactKind.EVENT_LOG
            ),
            None,
        )
        if artifact is not None:
            artifact_matches[discovered_execution_id] = (
                discovered_execution_id,
                _verified_artifact_path(artifact),
                artifact,
            )
    if artifact_matches:
        return tuple(artifact_matches[key] for key in sorted(artifact_matches))

    workspace_root = PLATFORM_ROOT / "executions"
    workspace_matches: dict[str, tuple[str, Path, object | None]] = {}
    if workspace_root.is_dir():
        for path in workspace_root.glob(f"*/{run_id}/events.jsonl"):
            discovered_execution_id = path.parent.parent.name
            if execution_id is not None and not (
                discovered_execution_id == execution_id
                or discovered_execution_id.startswith(f"{execution_id}.")
            ):
                continue
            workspace_matches[discovered_execution_id] = (
                discovered_execution_id,
                path,
                None,
            )
    return tuple(workspace_matches[key] for key in sorted(workspace_matches))


def _verified_artifact_path(artifact: ArtifactRef) -> Path:
    if not _artifact_store.verify(artifact):
        raise OSError(
            f"artifact failed integrity verification: {artifact.digest}"
        )
    return _artifact_store.resolve(artifact)


def _replay_diff_policy(value: object) -> ReplayDiffPolicy:
    if not isinstance(value, Mapping):
        raise TypeError("policy must be an object")
    event_types_value = value.get("event_types", ())
    ignored_paths_value = value.get(
        "ignored_payload_paths",
        tuple(ReplayDiffPolicy().ignored_payload_paths),
    )
    if not isinstance(event_types_value, (list, tuple)):
        raise TypeError("policy.event_types must be an array")
    if not isinstance(ignored_paths_value, (list, tuple)):
        raise TypeError("policy.ignored_payload_paths must be an array")
    return ReplayDiffPolicy(
        float_tolerance=float(value.get("float_tolerance", 0.0)),
        simulation_time_tolerance=float(
            value.get("simulation_time_tolerance", 0.0)
        ),
        compare_sequence_numbers=_strict_boolean(
            value.get("compare_sequence_numbers", True),
            "policy.compare_sequence_numbers",
        ),
        compare_payloads=_strict_boolean(
            value.get("compare_payloads", True),
            "policy.compare_payloads",
        ),
        compare_state_hashes=_strict_boolean(
            value.get("compare_state_hashes", True),
            "policy.compare_state_hashes",
        ),
        event_types=frozenset(EventType(str(item)) for item in event_types_value),
        ignored_payload_paths=frozenset(str(item) for item in ignored_paths_value),
        max_differences=int(value.get("max_differences", 1000)),
    )


def _required_string(value: Mapping[str, object], key: str) -> str:
    item = value[key]
    if not isinstance(item, str) or not item.strip():
        raise TypeError(f"{key} must be a non-empty string")
    return item


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise TypeError("execution id must be a non-empty string")
    return value


def _strict_boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{name} must be a boolean")
    return value


def _validate_component_identifier(value: str, name: str) -> None:
    if value in {"", ".", ".."} or any(
        character
        not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."
        for character in value
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                f"{name} may contain only letters, digits, dash, underscore, "
                "and dot"
            ),
        )


def _new_execution_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"exec-{timestamp}-{uuid.uuid4().hex[:12]}"


def _new_replay_execution_id(mode: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"replay-{mode}-{timestamp}-{uuid.uuid4().hex[:12]}"


def _replay_execution_view(report) -> dict[str, object]:
    value = to_jsonable(report)
    if not isinstance(value, dict):
        raise TypeError("replay execution report must serialize to an object")
    if report.diff is not None:
        value["diff"] = _diff_report_view(report.diff)
    return value


def _diff_report_view(report) -> dict[str, object]:
    value = to_jsonable(report)
    if not isinstance(value, dict):
        raise TypeError("replay diff report must serialize to an object")
    value["equivalent"] = report.equivalent
    value["first_divergence"] = to_jsonable(report.first_divergence)
    return value


def _json_response(value: object):
    return to_jsonable(value)
