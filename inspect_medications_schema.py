from sqlalchemy import inspect

from caa_pdf_extractor.database import engine


def inspect_medications_schema():
    print("=== INSPECTING MEDICATIONS SCHEMA ===")

    inspector = inspect(engine)

    tables = inspector.get_table_names(schema="public")

    if "medications" not in tables:
        print("Medications table does not exist")
        return

    print("\n=== TABLE COLUMNS ===")
    columns = inspector.get_columns(
        "medications",
        schema="public",
    )

    for col in columns:
        print(f"Column: {col['name']}")
        print(f"  Type: {col['type']}")
        print(f"  Nullable: {col['nullable']}")

        if col.get("default") is not None:
            print(f"  Default: {col['default']}")

        print()

    print("=== PRIMARY KEY ===")
    pk_constraint = inspector.get_pk_constraint(
        "medications",
        schema="public",
    )

    print(
        f"Primary Key: "
        f"{pk_constraint.get('constrained_columns')}"
    )

    print("\n=== UNIQUE CONSTRAINTS ===")
    unique_constraints = inspector.get_unique_constraints(
        "medications",
        schema="public",
    )

    if unique_constraints:
        for uc in unique_constraints:
            print(
                f"  {uc.get('name')}: "
                f"{uc.get('column_names')}"
            )
    else:
        print("  No unique constraints found")

    print("\n=== FOREIGN KEYS ===")
    fk_constraints = inspector.get_foreign_keys(
        "medications",
        schema="public",
    )

    if fk_constraints:
        for fk in fk_constraints:
            print(f"  {fk.get('name')}:")
            print(
                f"    Columns: "
                f"{fk.get('constrained_columns')}"
            )
            print(
                f"    Referenced table: "
                f"{fk.get('referred_table')}"
            )
            print(
                f"    Referenced columns: "
                f"{fk.get('referred_columns')}"
            )
    else:
        print("  No foreign key constraints found")


if __name__ == "__main__":
    inspect_medications_schema()