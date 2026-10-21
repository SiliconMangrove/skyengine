# SkyEngine: Flexible Manufacturing Simulation and Scheduling Platform

English | [简体中文](README.zh-CN.md)

**SkyEngine (Tiangong)** is an integrated simulation and scheduling platform for flexible manufacturing systems. It brings processing operations, material transport, machines, and AGV resources into a shared factory scenario, connecting algorithmic decisions, simulation execution, visualization, and experimental analysis. It provides a common environment for scheduling research and manufacturing system demonstrations.

The project addresses a concrete question: when processing, transport, resource contention, and dynamic disturbances jointly affect production, how will a schedule execute in simulation, and how can it be compared and improved? To address this question, SkyEngine organizes operation scheduling, task allocation, and path planning into interchangeable components, while progressively adding training, parameter exploration, dataset testing, and execution replay capabilities.

## Core Capabilities

| Area | Description |
| --- | --- |
| Factory simulation | Model job operations, eligible machines, AGVs, roads, material stations, and buffers, and simulate the coordination of processing and transport |
| Scheduling and planning | Integrate operation scheduling, task allocation, path planning, and joint scheduling algorithms to study different decision methods in the same scenarios |
| Dynamic scenarios | Represent order releases, equipment failures, processing-time variations, and rescheduling of plans that have not yet been executed |
| Visualization and analysis | View vehicle positions, machine processing, order progress, metrics, and events; export execution records for offline analysis |
| Algorithm experiments | Create training, hyperparameter exploration, and testing tasks; manage models and checkpoints; compare dataset results and replay execution |
| Deployment and integration | Deploy the platform, simulation engine, and external algorithm services with Docker Compose, with CPU or GPU computing configurations |

The grid factory supports joint research on processing and multi-vehicle transport; PacketFactory provides a non-grid factory environment; StaticFactory supports scenario demonstrations and interface development. Each environment retains its own configuration methods and execution semantics.

## Overall Architecture

SkyEngine combines platform services, factory simulation, and algorithm components:

```text
User interface: Factory management · Live dashboards · Log analysis · Algorithm experiment workbench
                                         │
Platform services: Run control · Experiment orchestration · Metrics and events · Model and result management
                                         │
Factory simulation: Jobs · Machines · AGVs · Roads · Materials and dynamic disturbances
                                         ↕
Algorithm components: Operation scheduling · Task allocation · Path planning · Joint scheduling policies
```

The platform organizes runs and presents results, factory environments enforce resource constraints and advance state, and algorithms generate decisions from observations. This separation allows algorithms to be replaced while retaining the factory and interface, and enables training, testing, and factory deployment to reuse the production simulation core.

The project is intended for researchers in flexible manufacturing, operations research, and multi-agent path finding, as well as engineering teams that need to demonstrate and analyze manufacturing scheduling processes.

## Development History

### September 2026: DFJSP-T Joint Scheduling

Unified the execution core used by training and production factories, refined the semantics of material transport, finished-product delivery, and finite buffers, and introduced graph-based policies, candidate rescheduling, rolling traffic planning, and parallel sampling.

### September 2026: General-Purpose Algorithm Experiment Platform

Brought training, hyperparameter exploration, and testing into a unified experiment workbench, adding dataset management, result comparison, model and checkpoint handoff, recorded execution replay, reproduction, and snapshot branching.

### June–July 2026: Execution Analysis and Interaction Improvements

Refined the display of machine operations and orders, and advanced dynamic order insertion, state and event recording, run archiving, offline analysis, configurable metric dashboards, and batch processing.

### First Half of 2026: Service Abstraction and Containerization

Developed factory lifecycle management and DockerProxy, connecting the platform, simulation engine, and FJSP/MAPF algorithm services through containers.

2026-01 · v1.0.0 Stable Release

Completed the end-to-end loop across simulation, scheduling, services, and visualization. With stable architectural interfaces, the system entered a stable phase supporting extensibility and reproducibility.

2025-12 · v0.9.0

Decoupled the algorithm and service layers, introduced Factory Proxy and a unified metrics system, and established the foundations of the platform architecture.

2025-10 · v0.8.0

Established Factory as the core abstraction and introduced Coordinator, enabling layered coordination of operation scheduling, task allocation, and path planning.

2025-08 · v0.7.0

Evolved from a scheduling prototype into a system-level simulation framework, introducing an event-driven model and basic visualization capabilities.

2025-06 · v0.6.0

Completed a minimal runnable prototype and demonstrated the feasibility of the overall modeling approach for flexible manufacturing scheduling and multi-agent simulation.

## Getting Started

- [Quick Deployment](docs/QUICKSTART.md)
- [User Documentation](docs/README.md)
- [Algorithm Experiment and Optimization Platform](docs/算法实验与优化平台.md)

## Development and Research

- [Developer Documentation Index](../docs/README.md)
- [Research Questions and Implementation Gaps](../docs/文档功能与实现差距.md)
- [Agent Handoff Index](../memory/README.md)

Related repositories are kept as sibling directories in the workspace:

| Repository | Responsibility |
| --- | --- |
| `skyengine` | Platform services, frontend, factory simulation, and experiment infrastructure |
| [skyengine-DFJSPT](https://github.com/SiliconMangrove/skyengine-DFJSPT) | DFJSP-T joint scheduling policies, candidate search, traffic planning, and PPO |
| [SkyEngine-FJSP](https://github.com/skyrimforest/SkyEngine-FJSP) | Operation scheduling algorithms and their containerized HTTP services |
| [SkyEngine-MAPF](https://github.com/skyrimforest/SkyEngine-MAPF) | Multi-agent path finding algorithms and their containerized HTTP services |

## Contact and License

* [Lei Xie lxie@nju.edu.cn](mailto:lxie@nju.edu.cn)
* [Hao Wu wuhao@smail.nju.edu.cn](mailto:wuhao@smail.nju.edu.cn)

See [LICENSE](LICENSE) for licensing information.
