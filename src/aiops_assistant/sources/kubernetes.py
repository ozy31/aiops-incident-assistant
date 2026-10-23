"""Recent Kubernetes events and rollouts for the workload an alert points at.

Optional: requires the ``k8s`` extra (``pip install 'aiops-incident-assistant[k8s]'``)
and read-only RBAC on events, deployments, statefulsets and replicasets.
The official client is synchronous, so calls run in a worker thread.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from typing import Any

from ..models import Alert, K8sEvent, Rollout

_POD_HASH = re.compile(r"-[a-z0-9]{8,10}-[a-z0-9]{5}$")
_STS_ORDINAL = re.compile(r"-\d+$")


def workload_from_labels(labels: dict[str, str]) -> tuple[str | None, str | None]:
    """Return (kind, name) of the workload referenced by alert labels."""
    for key, kind in (("deployment", "Deployment"), ("statefulset", "StatefulSet")):
        if labels.get(key):
            return kind, labels[key]
    pod = labels.get("pod")
    if pod:
        if _POD_HASH.search(pod):
            return "Deployment", _POD_HASH.sub("", pod)
        if _STS_ORDINAL.search(pod):
            return "StatefulSet", _STS_ORDINAL.sub("", pod)
    if labels.get("app"):
        return "Deployment", labels["app"]
    return None, None


class KubernetesSource:
    name = "kubernetes"

    def __init__(self, max_events: int = 30) -> None:
        from kubernetes import client, config

        try:
            config.load_incluster_config()  # type: ignore[no-untyped-call]
        except config.ConfigException:
            config.load_kube_config()  # type: ignore[no-untyped-call]
        self._core = client.CoreV1Api()
        self._apps = client.AppsV1Api()
        self._max_events = max_events

    async def fetch(
        self, alert: Alert, start: datetime, end: datetime
    ) -> tuple[list[K8sEvent], list[Rollout]]:
        namespace = alert.labels.get("namespace")
        if not namespace:
            return [], []
        kind, name = workload_from_labels(alert.labels)
        return await asyncio.to_thread(self._fetch_sync, namespace, kind, name, start)

    def _fetch_sync(
        self, namespace: str, kind: str | None, name: str | None, start: datetime
    ) -> tuple[list[K8sEvent], list[Rollout]]:
        events: list[K8sEvent] = []
        items: list[Any] = self._core.list_namespaced_event(namespace).items
        for ev in items:
            ts = ev.last_timestamp or ev.event_time or ev.first_timestamp
            obj = f"{ev.involved_object.kind}/{ev.involved_object.name}"
            if ts is not None and ts < start:
                continue
            if name and name not in (ev.involved_object.name or ""):
                continue
            events.append(
                K8sEvent(
                    timestamp=ts,
                    type=ev.type or "",
                    reason=ev.reason or "",
                    object=obj,
                    message=ev.message or "",
                    count=ev.count or 1,
                )
            )
        events.sort(key=lambda e: e.timestamp or start, reverse=True)

        rollouts: list[Rollout] = []
        if kind == "Deployment" and name:
            rollouts = self._deployment_rollouts(namespace, name)
        elif kind == "StatefulSet" and name:
            rollouts = self._statefulset_rollout(namespace, name)
        return events[: self._max_events], rollouts

    @staticmethod
    def _images(pod_spec: Any) -> list[str]:
        return [c.image for c in (pod_spec.containers or [])]

    def _deployment_rollouts(self, namespace: str, name: str) -> list[Rollout]:
        from kubernetes.client.exceptions import ApiException

        try:
            dep: Any = self._apps.read_namespaced_deployment(name, namespace)
        except ApiException:
            return []
        selector = ",".join(f"{k}={v}" for k, v in (dep.spec.selector.match_labels or {}).items())
        rs_list: list[Any] = self._apps.list_namespaced_replica_set(
            namespace, label_selector=selector
        ).items
        rs_list.sort(key=lambda rs: rs.metadata.creation_timestamp, reverse=True)
        return [
            Rollout(
                kind="ReplicaSet",
                name=rs.metadata.name,
                revision=(rs.metadata.annotations or {}).get("deployment.kubernetes.io/revision"),
                created=rs.metadata.creation_timestamp,
                images=self._images(rs.spec.template.spec),
                ready=f"{rs.status.ready_replicas or 0}/{rs.spec.replicas or 0}",
            )
            for rs in rs_list[:3]
        ]

    def _statefulset_rollout(self, namespace: str, name: str) -> list[Rollout]:
        from kubernetes.client.exceptions import ApiException

        try:
            sts: Any = self._apps.read_namespaced_stateful_set(name, namespace)
        except ApiException:
            return []
        return [
            Rollout(
                kind="StatefulSet",
                name=name,
                revision=sts.status.update_revision,
                created=sts.metadata.creation_timestamp,
                images=self._images(sts.spec.template.spec),
                ready=f"{sts.status.ready_replicas or 0}/{sts.spec.replicas or 0}",
            )
        ]
