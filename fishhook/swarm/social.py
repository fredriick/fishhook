"""Social network graph for agent interactions."""

from __future__ import annotations

import random
from typing import Any

import networkx as nx
import numpy as np

from fishhook.swarm.agent import Agent
from fishhook.utils.logging import get_logger

logger = get_logger("swarm.social")


class SocialNetwork:
    def __init__(self, connection_probability: float = 0.01) -> None:
        self._graph = nx.Graph()
        self._connection_prob = connection_probability

    def build_from_agents(self, agents: list[Agent]) -> None:
        self._graph.clear()
        for agent in agents:
            self._graph.add_node(agent.id, agent=agent)

        n = len(agents)
        ids = [a.id for a in agents]

        for i in range(n):
            for j in range(i + 1, n):
                if random.random() < self._connection_prob:
                    self._graph.add_edge(ids[i], ids[j])
                    agents[i].add_connection(ids[j])
                    agents[j].add_connection(ids[i])

        self._add_scale_free_connections(agents)

        edges = self._graph.number_of_edges()
        logger.info(
            f"Social network built: {n} agents, {edges} connections, "
            f"avg degree: {2 * edges / max(n, 1):.1f}"
        )

    def _add_scale_free_connections(self, agents: list[Agent]) -> None:
        n = len(agents)
        if n < 10:
            return

        m_param = max(2, int(n * self._connection_prob))
        ids = [a.id for a in agents]

        degrees = dict(self._graph.degree())
        total_degree = sum(degrees.values()) or 1

        for agent in agents:
            if self._graph.degree(agent.id) == 0:
                weights = []
                for other_id in ids:
                    if other_id != agent.id:
                        w = (degrees.get(other_id, 0) + 1) / (total_degree + n)
                        weights.append(w)
                if weights:
                    total_w = sum(weights)
                    weights = [w / total_w for w in weights]
                    targets = np.random.choice(
                        [oid for oid in ids if oid != agent.id],
                        size=min(m_param, n - 1),
                        replace=False,
                        p=weights,
                    )
                    for target_id in targets:
                        self._graph.add_edge(agent.id, int(target_id))
                        agent.add_connection(int(target_id))

    def get_neighbors(self, agent_id: int) -> list[Agent]:
        if agent_id not in self._graph:
            return []
        neighbors = []
        for neighbor_id in self._graph.neighbors(agent_id):
            data = self._graph.nodes[neighbor_id]
            if "agent" in data:
                neighbors.append(data["agent"])
        return neighbors

    def get_neighbor_opinions(self, agent_id: int) -> list[float]:
        neighbors = self.get_neighbors(agent_id)
        return [n.opinion for n in neighbors]

    def detect_communities(self) -> dict[int, int]:
        if self._graph.number_of_nodes() < 3:
            return {nid: 0 for nid in self._graph.nodes()}

        try:
            communities = list(nx.community.louvain_communities(self._graph, seed=42))
            group_map = {}
            for i, community in enumerate(communities):
                for node_id in community:
                    group_map[node_id] = i
            return group_map
        except Exception:
            return {nid: 0 for nid in self._graph.nodes()}

    def get_influencers(self, top_k: int = 10) -> list[tuple[int, float]]:
        try:
            centrality = nx.betweenness_centrality(self._graph)
            sorted_nodes = sorted(centrality.items(), key=lambda x: x[1], reverse=True)
            return sorted_nodes[:top_k]
        except Exception:
            return []

    def get_stats(self) -> dict[str, Any]:
        if self._graph.number_of_nodes() == 0:
            return {"nodes": 0, "edges": 0}

        degrees = [d for _, d in self._graph.degree()]
        return {
            "nodes": self._graph.number_of_nodes(),
            "edges": self._graph.number_of_edges(),
            "avg_degree": sum(degrees) / len(degrees) if degrees else 0,
            "max_degree": max(degrees) if degrees else 0,
            "density": nx.density(self._graph),
            "components": nx.number_connected_components(self._graph),
        }
