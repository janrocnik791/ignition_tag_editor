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
from .automation import (  # noqa: F401
    AUTOMATION_EVIDENCE_TYPES,
    AutomationError,
    propose_automation,
)
from .export import (  # noqa: F401
    ExportError,
    canonical_export_bytes,
    compute_export_scope,
    compute_full_export_scopes,
    serialize_ignition_json,
    verify_round_trip,
    verify_ignition_reexport,
    write_package,
    write_production_package,
)
from .project import (  # noqa: F401
    Project,
    ProjectError,
    ProjectSchemaError,
    create_project,
    open_project,
    recover,
)
from .operations import (  # noqa: F401
    CREATE_OPERATION_TYPES,
    OPERATION_STATUSES,
    OPERATION_TYPES,
    OperationError,
    active_operations,
    apply_operation_to_state,
    build_simulation_state,
    create_operation,
    get_operation,
    invert_operation,
    list_operations,
    load_baseline_state,
    ordered_operations,
    operation_cursor,
    redo,
    remove_operation,
    reorder_operation,
    validate_operation,
    undo,
)
from .relationships import (  # noqa: F401
    EVIDENCE_TYPES,
    QUERY_EVIDENCE_TYPES,
    ORIGINS,
    RELATIONSHIP_ROLES,
    RELATIONSHIP_STATES,
    SUGGESTION_EVIDENCE_TYPES,
    RelationshipError,
    confirm_relationship,
    create_manual_relationship,
    create_suggestion_relationship,
    discover_exact,
    query_relationships,
    refresh_relationship_validity,
    reject_relationship,
    relationship_validity,
    remove_manual_relationship,
)
from .reference_context import (  # noqa: F401
    REFERENCE_EVIDENCE_TYPE,
    ReferenceContextError,
    apply_reference_index,
    import_reference_context,
)
from .repository import (  # noqa: F401
    MAX_SEARCH_PAGE_SIZE,
    RepositoryError,
    SEARCH_FIELDS,
    SEARCH_MODES,
    breadcrumbs,
    child_count,
    full_path,
    get_children,
    get_node,
    get_parent,
    get_provider_root,
    get_search_filters,
    node_details,
    search_nodes,
)
from .schema import SCHEMA_VERSION  # noqa: F401
from .simulation import (  # noqa: F401
    MAX_SIM_PAGE_SIZE,
    SimTree,
    SimulationError,
    diff,
    sim_children,
    sim_details,
)
from .validation import validate_project  # noqa: F401
from .udt_context import ProjectUdtContext  # noqa: F401
