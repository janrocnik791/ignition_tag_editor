"""Nalaganje pravil required/optional clanov (rules/member_requirements.yaml).

Validator NE ugiba, kateri clani so obvezni. Pravila obstajajo samo, ce jih
uporabnik eksplicitno zapise. Ce datoteka ne obstaja ali je ni mogoce prebrati,
vrnemo prazna pravila -- odsotnost clana ostane najvec INFO.

Format (mapping typeId -> {required: [...], optional: [...]}):

    Siemens/Meritev:
      required: [Meritev]
      optional: [SetPoint, Simulacija]

Ker YAML ni v standardni knjiznici, poskusimo PyYAML; ce ga ni, uporabimo
minimalen vgrajen parser za zgornji ozek format ali sosednji .json.
"""

from __future__ import annotations

import json
import os
from typing import Dict, List


def _empty() -> Dict[str, Dict[str, List[str]]]:
    return {}


def _parse_minimal_yaml(text: str) -> Dict[str, Dict[str, List[str]]]:
    """Zelo ozek parser za nas format. Podpira samo:

        <typeId>:
          required: [a, b]
          optional: [c]

    Vse vrstice, ki so komentar (#) ali prazne, se ignorirajo. Ob cemer koli
    nepricakovanem vrne, kar je uspel razbrati (defenzivno).
    """
    result: Dict[str, Dict[str, List[str]]] = {}
    current = None

    def parse_list(val: str) -> List[str]:
        val = val.strip()
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            if not inner:
                return []
            return [x.strip().strip("'\"") for x in inner.split(",") if x.strip()]
        return []

    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if not line.startswith((" ", "\t")):
            # kljuc na vrhnjem nivoju: "<typeId>:"
            if line.endswith(":"):
                current = line[:-1].strip().strip("'\"")
                result.setdefault(current, {"required": [], "optional": []})
            else:
                current = None
        else:
            if current is None:
                continue
            key, _sep, val = line.strip().partition(":")
            key = key.strip()
            if key in ("required", "optional"):
                result[current][key] = parse_list(val)
    return result


def load_member_requirements(rules_dir: str) -> Dict[str, Dict[str, List[str]]]:
    """Nalozi pravila iz rules_dir. Vrne {} ce jih ni."""
    yaml_path = os.path.join(rules_dir, "member_requirements.yaml")
    json_path = os.path.join(rules_dir, "member_requirements.json")

    if os.path.exists(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return _normalize(data)
        except Exception:
            return _empty()

    if os.path.exists(yaml_path):
        try:
            with open(yaml_path, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception:
            return _empty()
        try:
            import yaml  # type: ignore

            data = yaml.safe_load(text) or {}
            return _normalize(data)
        except Exception:
            return _normalize(_parse_minimal_yaml(text))

    return _empty()


def _normalize(data) -> Dict[str, Dict[str, List[str]]]:
    out: Dict[str, Dict[str, List[str]]] = {}
    if not isinstance(data, dict):
        return out
    for type_id, spec in data.items():
        if not isinstance(spec, dict):
            continue
        req = spec.get("required") or []
        opt = spec.get("optional") or []
        out[str(type_id)] = {
            "required": [str(x) for x in req],
            "optional": [str(x) for x in opt],
        }
    return out
