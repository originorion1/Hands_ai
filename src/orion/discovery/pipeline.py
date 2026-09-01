"""Composition root for the first ORION discovery-to-graph loop."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..contracts import Observation
from ..understanding.graph import GraphStore, project_observations


class DiscoverySource(Protocol):
    def discover(self) -> tuple[Observation, ...]: ...


@dataclass(frozen=True, slots=True)
class DiscoveryPipelineResult:
    observations: tuple[Observation, ...]
    nodes_added: int


class DiscoveryPipeline:
    """Run discovery and project only observed facts into the canonical graph."""

    def __init__(self, source: DiscoverySource, graph: GraphStore) -> None:
        self._source = source
        self._graph = graph

    def run(self) -> DiscoveryPipelineResult:
        observations = self._source.discover()
        nodes_added = project_observations(self._graph, observations)
        return DiscoveryPipelineResult(
            observations=observations,
            nodes_added=nodes_added,
        )
