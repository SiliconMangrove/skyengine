"""规范化实例并计算跨 split 去重哈希。"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


def normalize_instance(instance: Mapping[str, Any]) -> dict[str, Any]:
    value = json.loads(json.dumps(instance, sort_keys=True, ensure_ascii=False))
    value.pop("instance_id", None)
    value.pop("split", None)
    return value


def canonical_hash(instance: Mapping[str, Any]) -> str:
    payload = json.dumps(normalize_instance(instance), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
