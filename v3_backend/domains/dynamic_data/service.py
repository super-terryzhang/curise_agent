"""Stable public entrypoints; domain internals stay behind this facade."""

from .lifecycle import set_field_status, set_record_status, set_table_status
from .query import (
    get_record,
    get_table,
    list_changes,
    list_fields,
    list_records,
    list_tables,
    search_link_targets,
)
from .records import create_record, get_record_values, update_record
from .repository import lock_tables as lock_tables_in_transaction
from .structures import create_field, create_table, reorder_fields, update_field, update_table
from .system_records import persist_system_extension_values_in_transaction, save_system_record

__all__ = [
    "create_field",
    "create_table",
    "reorder_fields",
    "update_field",
    "update_table",
    "create_record",
    "update_record",
    "get_record_values",
    "set_table_status",
    "set_field_status",
    "set_record_status",
    "list_tables",
    "get_table",
    "list_fields",
    "get_record",
    "list_records",
    "list_changes",
    "search_link_targets",
    "save_system_record",
    "persist_system_extension_values_in_transaction",
    "lock_tables_in_transaction",
]
