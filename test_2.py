from caa_pdf_extractor.database import SessionLocal
from sqlalchemy import text

with SessionLocal() as session:
    result = session.execute(
        text("""
            SELECT
                column_name,
                data_type,
                is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'source_documents'
            ORDER BY ordinal_position
        """)
    )

    print("SOURCE_DOCUMENTS SCHEMA:")
    for row in result:
        print(
            f"{row.column_name} | "
            f"{row.data_type} | "
            f"nullable={row.is_nullable}"
        )