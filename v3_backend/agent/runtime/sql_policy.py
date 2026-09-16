"""Compile a deliberately small read-only SQL language over approved business columns."""

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import traverse_scope

SCHEMA = {
    "products": "id code product_name_en product_name_jp country_id category_id supplier_id port_id unit price contract_price unit_size status",
    "suppliers": "id name country_id contact email phone address zip_code fax",
    "countries": "id name code status",
    "ports": "id name code country_id location status",
    "categories": "id name code description status",
    "v2_exchange_rates": "id from_currency to_currency rate effective_date",
}

# Reject unknown syntax/functions rather than chasing new dangerous keywords.
ALLOWED_NODES = frozenset(
    [
        "Select",
        "Subquery",
        "CTE",
        "With",
        "Table",
        "TableAlias",
        "Identifier",
        "Column",
        "Alias",
        "Literal",
        "Star",
        "Count",
        "Avg",
        "Sum",
        "Max",
        "Min",
        "Coalesce",
        "Round",
        "Abs",
        "Lower",
        "Upper",
        "Length",
        "Trim",
        "Join",
        "Where",
        "Group",
        "Having",
        "Order",
        "Ordered",
        "Limit",
        "Offset",
        "Distinct",
        "And",
        "Or",
        "Not",
        "EQ",
        "NEQ",
        "GT",
        "GTE",
        "LT",
        "LTE",
        "Add",
        "Sub",
        "Mul",
        "Div",
        "Mod",
        "Paren",
        "In",
        "Between",
        "Is",
        "Null",
        "Boolean",
        "Case",
        "If",
        "Neg",
        "Union",
        "From",
        "Like",
        "ILike",
    ]
)


def compile_query(sql: str, dialect: str) -> str:
    if len(sql) > 10000:
        raise ValueError("query too long")
    statements = sqlglot.parse(sql, read=dialect)
    if len(statements) != 1 or not isinstance(statements[0], (exp.Select, exp.Union)):
        raise ValueError("one read-only query required")
    tree = statements[0]
    if sum(1 for _ in tree.walk()) > 500:
        raise ValueError("query too complex")
    for node in tree.walk():
        if type(node).__name__ not in ALLOWED_NODES:
            raise ValueError("unsupported query syntax")
        if isinstance(node, exp.Table) and (node.db or node.catalog):
            raise ValueError("qualified tables are not available")
        if isinstance(node, exp.With) and node.args.get("recursive"):
            raise ValueError("recursive queries are not available")
    for scope in traverse_scope(tree):
        for source in scope.sources.values():
            if isinstance(source, exp.Table) and source.name.lower() not in SCHEMA:
                raise ValueError("table is not available")
    tree = qualify(
        tree,
        dialect=dialect,
        schema={
            table: dict.fromkeys(columns.split(), "UNKNOWN") for table, columns in SCHEMA.items()
        },
        infer_schema=False,
        validate_qualify_columns=True,
    )
    # Qualification expands '*' using only the approved schema. Schema-qualified
    # physical tables prevent pg_temp/search_path from shadowing application tables.
    if dialect == "postgres":
        for scope in traverse_scope(tree):
            for source in scope.sources.values():
                if isinstance(source, exp.Table):
                    source.set("db", exp.to_identifier("public", quoted=True))
    return f'SELECT * FROM ({tree.sql(dialect=dialect)}) AS "approved_result" LIMIT 51'
