from caa_pdf_extractor.database import SessionLocal
from sqlalchemy import text


with SessionLocal() as session:
    result = session.execute(
        text("""
            SELECT
                id,
                jsonb_object_keys(document_json) AS top_level_key
            FROM public.source_documents
            WHERE document_json IS NOT NULL
            LIMIT 20
        """)
    )

    print("TOP-LEVEL JSON KEYS:")
    keys = sorted({row.top_level_key for row in result})
    for key in keys:
        print(f"  {key}")

    result = session.execute(
        text("""
            SELECT document_json
            FROM public.source_documents
            WHERE document_json IS NOT NULL
            ORDER BY id
            LIMIT 1
        """)
    )

    doc = result.scalar()

    print("\nFIRST DOCUMENT STRUCTURE:")
    print(f"Top-level keys: {list(doc.keys())}")

    if isinstance(doc.get("document"), dict):
        print(
            f"document keys: "
            f"{list(doc['document'].keys())}"
        )

        print(
            f"patient_id: "
            f"{doc['document'].get('patient_id')}"
        )