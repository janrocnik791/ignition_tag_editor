"""Editor-first jedro (mejniki B+): delovni projekt, baseline, relacije, operacije.

Ta paket je namerno brez Qt odvisnosti, da ga je mogoce uvoziti in testirati
headless. GUI zivi loceno v paketu ``ui/``.

Mejnik B1 uvaja samostojen ``project.sqlite`` (model projekta, shema, migracijski
tekac, zivljenjski cikel). Uvoz tagov v baseline pride v B2.
"""

from .import_service import (  # noqa: F401
    ImportSourceError,
    compute_node_uid,
    compute_provider_uid,
    discover_sources,
    import_source,
    list_providers,
    parse_provider_name,
    validate_source,
)
from .project import (  # noqa: F401
    Project,
    ProjectError,
    ProjectSchemaError,
    create_project,
    open_project,
    recover,
)
from .schema import SCHEMA_VERSION  # noqa: F401
