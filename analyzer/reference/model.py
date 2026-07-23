"""Podatkovni model referencnega uvoza (dataclasses).

Vsak normaliziran zapis ohrani izvor (datoteka, list, vrstica, stolpec) in
originalne vrednosti. Neznani stolpci se ohranijo v raw_row_json (ne izginejo).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class ReferenceSource:
    path: str
    filename: str
    sheet_name: str
    site: Optional[str]
    line: Optional[str]
    profile_id: str
    sha256: str
    size_bytes: int
    header_json: str
    row_count: int = 0
    group_count: int = 0
    member_count: int = 0
    issue_count: int = 0
    previous_sha256: Optional[str] = None
    imported_at: Optional[str] = None
    id: Optional[int] = None


@dataclass
class ExpectedMember:
    member_key: str
    expected_name: str
    required: Optional[bool]
    note: Optional[str]
    source_row: int
    source_col_index: int
    source_col_header: str
    raw_value: str


@dataclass
class ExpectedGroup:
    site: Optional[str]
    line: Optional[str]
    tech_number: Optional[str]
    maximo_id: Optional[str]
    description: Optional[str]
    group_type: str
    prefix: Optional[str]
    source_row: int
    raw_row_json: str
    status: str = "OK"
    members: List[ExpectedMember] = field(default_factory=list)
    id: Optional[int] = None


@dataclass
class ExpectedLineTag:
    site: Optional[str]
    line: Optional[str]
    label: Optional[str]
    old_name: Optional[str]
    new_name: Optional[str]
    note: Optional[str]
    source_row: int
    raw_row_json: str


@dataclass
class LegendEntry:
    group_type: Optional[str]
    kind: Optional[str]
    old_token: Optional[str]
    new_token: Optional[str]
    meaning: Optional[str]
    source_row: int
    source_col_index: int


@dataclass
class MemberTemplate:
    group_type: str
    member_key: str
    ordinal: int


@dataclass
class ImportIssue:
    severity: str
    code: str
    sheet: Optional[str]
    source_row: Optional[int]
    source_col_index: Optional[int]
    canonical_key: Optional[str]
    message: str
    raw_context_json: Optional[str] = None


@dataclass
class ParsedSheet:
    """Rezultat branja enega lista: normalizirani zapisi + ugotovitve."""

    source: ReferenceSource
    groups: List[ExpectedGroup] = field(default_factory=list)
    line_tags: List[ExpectedLineTag] = field(default_factory=list)
    legend: List[LegendEntry] = field(default_factory=list)
    templates: List[MemberTemplate] = field(default_factory=list)
    issues: List[ImportIssue] = field(default_factory=list)
