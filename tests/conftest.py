"""Skupni pytest fixtures: zgradi indeks iz data/fixtures v zacasno DB."""

from __future__ import annotations

import hashlib
import os
import sqlite3

import pytest

from analyzer.build import build_index

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES_RAW = os.path.join(ROOT, "data", "fixtures", "testsite")
FIXTURES_VALIDATE = os.path.join(ROOT, "data", "fixtures", "validate")


def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


@pytest.fixture(scope="session")
def fixtures_raw() -> str:
    return FIXTURES_RAW


@pytest.fixture(scope="session")
def built_db(tmp_path_factory, fixtures_raw):
    db_path = str(tmp_path_factory.mktemp("db") / "index.sqlite")
    summary = build_index(fixtures_raw, db_path)
    return {"db": db_path, "summary": summary}


@pytest.fixture()
def conn(built_db):
    c = sqlite3.connect(built_db["db"])
    yield c
    c.close()


@pytest.fixture(scope="session")
def validate_db(tmp_path_factory):
    db_path = str(tmp_path_factory.mktemp("vdb") / "vindex.sqlite")
    build_index(FIXTURES_VALIDATE, db_path)
    return {"db": db_path, "raw": FIXTURES_VALIDATE}


@pytest.fixture()
def vconn(validate_db):
    c = sqlite3.connect(validate_db["db"])
    yield c
    c.close()


@pytest.fixture()
def raw_hashes(fixtures_raw):
    """sha256 vseh fixture datotek za preverjanje nespremenljivosti."""
    out = {}
    for name in os.listdir(fixtures_raw):
        p = os.path.join(fixtures_raw, name)
        if os.path.isfile(p):
            out[name] = _sha(p)
    return out
