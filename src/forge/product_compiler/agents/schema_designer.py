"""
forge.product_compiler.agents.schema_designer — Database schema from Intent Document.

Stage 5 of the pipeline: converts entities + relationships into
a complete PostgreSQL schema with tables, indexes, foreign keys, and raw SQL.
"""

from __future__ import annotations

import json
import structlog
from pathlib import Path

from forge.llm import LLMBackend
from forge.product_compiler.models import (
    ColumnDefinition,
    DatabaseSchema,
    Entity,
    FKDefinition,
    IndexDefinition,
    IntentDocument,
    Relationship,
    TableDefinition,
)

log = structlog.get_logger(__name__)

SCHEMA_DESIGNER_SYSTEM = """You are a database schema design agent.

Your job: produce a complete PostgreSQL schema from an Intent Document.
You receive entities, fields, and relationships — you produce CREATE TABLE statements.

Rules:
- Use PostgreSQL types: text, integer, bigint, boolean, timestamp with time zone,
  uuid, jsonb, citext (for emails), bytea
- Always include: id (uuid PRIMARY KEY), created_at, updated_at
- Use soft deletes (deleted_at TIMESTAMP) when the entity has deleted_at_supported=true
- Use audit log tables when entity.audit_log=true
- Foreign keys use ON DELETE CASCADE by default
- Index columns that appear in WHERE, ORDER BY, or JOIN conditions
- Unique constraints on email/username fields
- Add reasonable defaults (now() for timestamps, '' for text)
"""


class SchemaDesignerAgent:
    def __init__(self, llm: LLMBackend):
        self.llm = llm

    def run(self, intent: IntentDocument) -> DatabaseSchema:
        """
        Convert an Intent Document into a complete DatabaseSchema.
        Uses LLM to reason about field types and relationships,
        then generates raw SQL.
        """
        # Step 1: Map entities → tables with columns
        tables = self._design_tables(intent.entities)

        # Step 2: Map relationships → foreign keys
        foreign_keys = self._design_foreign_keys(intent.relationships, tables)

        # Step 3: Suggest indexes from entity fields
        indexes = self._design_indexes(tables, intent.entities)

        # Step 4: Generate raw SQL
        raw_sql = self._generate_sql(tables, indexes, foreign_keys)

        schema = DatabaseSchema(
            tables=tables,
            indexes=indexes,
            foreign_keys=foreign_keys,
            raw_sql=raw_sql,
        )

        log.info("schema_designer.complete", tables=len(tables), indexes=len(indexes), fks=len(foreign_keys))
        return schema

    def _design_tables(self, entities: list[Entity]) -> list[TableDefinition]:
        """Convert IntentDocument entities to table definitions."""
        prompt = f"""Design PostgreSQL table definitions from these entities.

Entities:
{json.dumps([{"name": e.name, "fields": [{"name": f.name, "type": f.type, "required": f.required, "deleted_at_supported": f.deleted_at_supported} for f in e.fields], "deletable": e.deletable, "audit_log": e.audit_log} for e in entities], indent=2)}

For each entity produce a table with:
- id: uuid PRIMARY KEY DEFAULT gen_random_uuid()
- created_at: timestamp with time zone DEFAULT now()
- updated_at: timestamp with time zone DEFAULT now()
- All entity fields mapped to postgres types (string→text, int→integer, bool→boolean, datetime→timestamp with time zone)
- For soft-delete: deleted_at timestamp with time zone (null=not deleted)
- For audit_log: a separate _audit table (entityname_audit_log)

Output ONLY valid JSON:
{{
  "tables": [
    {{
      "name": "snake_case_entity_name",
      "columns": [
        {{
          "name": "column_name",
          "type": "postgres_type",
          "nullable": bool,
          "default": "default_value",
          "primary_key": bool,
          "unique": bool,
          "index": bool,
          "description": ""
        }}
      ],
      "primary_key": "id",
      "soft_delete_column": null or "deleted_at",
      "audit_log_table": null or "entityname_audit_log",
      "description": ""
    }}
  ]
}}
"""
        raw = self.llm.complete_json(
            prompt=prompt,
            system=SCHEMA_DESIGNER_SYSTEM,
            max_tokens=4096,
            temperature=0.2,
        )
        raw = raw if isinstance(raw, dict) else json.loads(raw)

        tables = []
        for t in raw.get("tables", []):
            columns = [
                ColumnDefinition(
                    name=c["name"],
                    type=c["type"],
                    nullable=c.get("nullable", False),
                    default=c.get("default", ""),
                    primary_key=c.get("primary_key", False),
                    unique=c.get("unique", False),
                    index=c.get("index", False),
                    description=c.get("description", ""),
                )
                for c in t.get("columns", [])
            ]
            tables.append(
                TableDefinition(
                    name=t["name"],
                    columns=columns,
                    primary_key=t.get("primary_key", "id"),
                    soft_delete_column=t.get("soft_delete_column"),
                    audit_log_table=t.get("audit_log_table"),
                    description=t.get("description", ""),
                )
            )

        # If LLM returned no tables (entities were empty), create a minimal default
        if not tables and not entities:
            tables.append(
                TableDefinition(
                    name="app_state",
                    columns=[
                        ColumnDefinition(name="id", type="uuid", primary_key=True, default="gen_random_uuid()"),
                        ColumnDefinition(name="created_at", type="timestamp with time zone", default="now()"),
                        ColumnDefinition(name="updated_at", type="timestamp with time zone", default="now()"),
                    ],
                )
            )

        return tables

    def _design_foreign_keys(
        self, relationships: list[Relationship], tables: list[TableDefinition]
    ) -> list[FKDefinition]:
        """Convert relationships to foreign key definitions."""
        if not relationships:
            return []

        prompt = f"""Design foreign key constraints from these relationships.

Relationships:
{json.dumps([{"from": r.from_entity, "to": r.to_entity, "type": r.relationship_type, "fk_field": r.foreign_key_field, "on_delete": r.on_delete} for r in relationships], indent=2)}

Existing tables: {[t.name for t in tables]}

Output ONLY valid JSON:
{{
  "foreign_keys": [
    {{
      "from_table": "table_name",
      "from_column": "column_name",
      "to_table": "referenced_table",
      "to_column": "id",
      "on_delete": "cascade|set_null|restrict"
    }}
  ]
}}
"""
        raw = self.llm.complete_json(
            prompt=prompt,
            system="You are a PostgreSQL foreign key design agent.",
            max_tokens=2048,
            temperature=0.1,
        )
        raw = raw if isinstance(raw, dict) else json.loads(raw)

        return [
            FKDefinition(
                from_table=fk["from_table"],
                from_column=fk["from_column"],
                to_table=fk["to_table"],
                to_column=fk.get("to_column", "id"),
                on_delete=fk.get("on_delete", "cascade"),
            )
            for fk in raw.get("foreign_keys", [])
        ]

    def _design_indexes(
        self, tables: list[TableDefinition], entities: list[Entity]
    ) -> list[IndexDefinition]:
        """Suggest indexes based on entity field usage patterns."""
        indexes = []
        for table in tables:
            for col in table.columns:
                if col.index or col.unique:
                    idx_name = f"idx_{table.name}_{col.name}"
                    if col.unique:
                        idx_name = f"uq_{table.name}_{col.name}"
                    indexes.append(
                        IndexDefinition(
                            name=idx_name,
                            table=table.name,
                            columns=[col.name],
                            unique=col.unique,
                            method="btree",
                        )
                    )
        return indexes

    def _generate_sql(
        self,
        tables: list[TableDefinition],
        indexes: list[IndexDefinition],
        foreign_keys: list[FKDefinition],
    ) -> str:
        """Generate raw PostgreSQL CREATE TABLE statements."""
        statements = []

        for table in tables:
            col_defs = []
            for col in table.columns:
                parts = [f'  {col.name} {col.type}']
                if not col.nullable:
                    parts.append("NOT NULL")
                if col.default:
                    parts.append(f"DEFAULT {col.default}")
                if col.unique and not col.primary_key:
                    parts.append("UNIQUE")
                col_defs.append(" ".join(parts))

            # Primary key constraint
            if table.primary_key:
                col_defs.append(f"  PRIMARY KEY ({table.primary_key})")

            # Foreign keys
            table_fks = [fk for fk in foreign_keys if fk.from_table == table.name]
            for fk in table_fks:
                col_defs.append(
                    f"  FOREIGN KEY ({fk.from_column}) REFERENCES {fk.to_table}({fk.to_column}) ON DELETE {fk.on_delete.upper()}"
                )

            create_stmt = f"CREATE TABLE {table.name} (\n" + ",\n".join(col_defs) + "\n);"
            statements.append(create_stmt)

        # Soft delete audit log tables
        for table in tables:
            if table.audit_log_table:
                audit_stmt = f"""CREATE TABLE {table.audit_log_table} (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  {table.name}_id uuid NOT NULL,
  operation text NOT NULL,
  changed_by text,
  changed_at timestamp with time zone DEFAULT now(),
  old_data jsonb,
  new_data jsonb
);"""
                statements.append(audit_stmt)

        # Index statements
        for idx in indexes:
            unique = "UNIQUE " if idx.unique else ""
            columns = ", ".join(idx.columns)
            statements.append(f"CREATE {unique}INDEX {idx.name} ON {idx.table} USING {idx.method} ({columns});")

        return "\n\n".join(statements)
