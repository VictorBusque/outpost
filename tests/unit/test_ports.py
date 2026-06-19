"""Tests for the pure port allocator (engine/ports.py).

DoD core: first-fit picks the lowest free port; NGINX_PORT and declared listen
ports are excluded; exhaustion raises PortAllocationError. Declared-listen and
unix-socket services are absent from the result.
"""

from __future__ import annotations

import pytest

from sow.constants import NGINX_PORT
from sow.engine.ports import PortAllocationError, allocate_all
from sow.models import sowConfig
from tests.unit.conftest import minimal_config, minimal_service


def _config(services=None, **overrides) -> sowConfig:
    return sowConfig.model_validate(minimal_config(services, **overrides))


def test_allocated_port_is_first_in_range():
    cfg = _config()
    ports = allocate_all(cfg)
    assert ports == {"api": 18000}


def test_first_fit_skips_nginx_port():
    # Force the range to start at NGINX_PORT so the first free port must skip it.
    cfg = _config(port_range=f"{NGINX_PORT}-{NGINX_PORT + 5}")
    ports = allocate_all(cfg)
    assert ports == {"api": NGINX_PORT + 1}


def test_declared_listen_excluded_and_service_not_allocated():
    svc = minimal_service(listen="127.0.0.1:8080")
    cfg = _config({"web": svc})
    assert allocate_all(cfg) == {}  # declared listen -> no allocation


def test_declared_listen_port_is_excluded_from_pool():
    # One listen-less service + one declared on 18000 -> allocator must skip 18000.
    declared = minimal_service(listen="127.0.0.1:18000")
    listenless = minimal_service(git="https://github.com/me/other.git", command="./run")
    cfg = _config({"pinned": declared, "alloc": listenless})
    ports = allocate_all(cfg)
    assert ports == {"alloc": 18001}  # skipped the declared 18000


def test_unix_listen_service_not_allocated_but_does_not_block_pool():
    unix_svc = minimal_service(listen="/run/sow/api.sock")
    cfg = _config({"sock": unix_svc})
    assert allocate_all(cfg) == {}


def test_first_fit_assigns_distinct_ascending_ports():
    services = {
        f"s{i}": minimal_service(git=f"https://x/{i}.git", command="./run") for i in range(3)
    }
    cfg = _config(services)
    ports = allocate_all(cfg)
    assert sorted(ports.values()) == [18000, 18001, 18002]
    assert len(set(ports.values())) == 3


def test_exhaustion_raises():
    # Range of 2 ports, 3 listen-less services -> exhaustion.
    cfg = _config(
        {
            "a": minimal_service(command="./run"),
            "b": minimal_service(git="https://x/b.git", command="./run"),
            "c": minimal_service(git="https://x/c.git", command="./run"),
        },
        port_range="18000-18001",
    )
    with pytest.raises(PortAllocationError, match="exhausted"):
        allocate_all(cfg)
