"""Editor-first jedro (mejniki B+): delovni projekt, baseline, relacije, operacije.

Ta paket je namerno brez Qt odvisnosti, da ga je mogoce uvoziti in testirati
headless. GUI zivi loceno v paketu ``ui/``.

Mejnik B1 uvaja samostojen ``project.sqlite`` (model projekta, shema, migracijski
tekac, zivljenjski cikel). Uvoz tagov v baseline pride v B2.
"""

from .project import (  # noqa: F401
    Project,
    ProjectError,
    ProjectSchemaError,
    create_project,
    open_project,
    recover,
)
from .schema import SCHEMA_VERSION  # noqa: F401
