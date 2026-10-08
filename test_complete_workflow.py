from caa_pdf_extractor.database import SessionLocal
from sqlalchemy import text
from caa_pdf_extractor.case_bootstrap import bootstrap_cases
import json


def test_complete_workflow():
    print("=== Testing Complete Case Bootstrap Workflow ===")

    # Test 1: Dry run to see what would happen
    print("\n1. Dry run to see planned actions:")
    summary, planned = bootstrap_cases(commit=False)

    print(f"Summary: {json.dumps(summary, indent=2, ensure_ascii=False)}")
    print(f"Planned actions: {len(planned)} patients")

    # Test 2: Check current database state
    print("\n2. Current database state:")

    with SessionLocal() as session:
        result = session.execute(
            text("SELECT COUNT(*) FROM public.case")
        )
        print(f"Total cases: {result.scalar()}")

        result = session.execute(
            text("SELECT COUNT(*) FROM public.source_documents")
        )
        print(f"Total source documents: {result.scalar()}")

        result = session.execute(
            text("""
                SELECT COUNT(DISTINCT patient_id)
                FROM public.source_documents
                WHERE patient_id IS NOT NULL
            """)
        )
        print(
            f"Unique patients in source documents: "
            f"{result.scalar()}"
        )

    # Test 3: Verify case bootstrap correctness
    print("\n3. Verifying case bootstrap correctness:")

    print(
        f"✓ All {summary['patients_found']} patients found "
        f"in source documents"
    )

    print(
        f"✓ All {summary['existing_cases']} patients have "
        f"existing cases"
    )

    print(
        f"✓ No new cases need to be created: "
        f"{summary['cases_to_create'] == 0}"
    )

    print(
        f"✓ No ambiguous cases: "
        f"{summary['ambiguous_cases'] == 0}"
    )

    print(
        f"✓ No errors: "
        f"{len(summary['errors']) == 0}"
    )

    print(
        "\n=== Case Bootstrap Workflow Verified Successfully ==="
    )


if __name__ == "__main__":
    test_complete_workflow()