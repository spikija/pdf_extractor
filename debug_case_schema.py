from caa_pdf_extractor.database import SessionLocal
from sqlalchemy import text

with SessionLocal() as session:
    result = session.execute(
        text("""
            SELECT
                column_name,
                data_type,
                is_nullable,
                column_default
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'case'
            ORDER BY ordinal_position
        """)
    )

    print("CASE SCHEMA:")
    for row in result:
        nullable = "(nullable)" if row.is_nullable == "YES" else "(not null)"
        print(
            f"  {row.column_name}: "
            f"{row.data_type} "
            f"{nullable} "
            f"{row.column_default}"
        )

    result = session.execute(
        text("""
            SELECT
                tc.constraint_name,
                kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
            WHERE tc.table_schema = 'public'
              AND tc.table_name = 'case'
              AND tc.constraint_type = 'UNIQUE'
        """)
    )

    print("\nUNIQUE CONSTRAINTS:")
    rows = list(result)
    if not rows:
        print("  none")
    else:
        for row in rows:
            print(f"  {row.constraint_name} on {row.column_name}")

    result = session.execute(
        text("SELECT COUNT(*) FROM public.case")
    )
    print(f"\nEXISTING CASES: {result.scalar()}")

    result = session.execute(
        text("""
            SELECT id, fallnr
            FROM public.case
            WHERE fallnr = :fallnr
        """),
        {"fallnr": "2473679415"},
    )

    case = result.fetchone()
    print(f"\nCASE FOR PATIENT 2473679415: {case}")