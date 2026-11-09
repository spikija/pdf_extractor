#!/usr/bin/env python3
"""Archive-wide local T2 recovery. Dry-run by default; never connects to PostgreSQL."""
from caa_pdf_extractor.mri_t2_recovery import main

if __name__ == '__main__':
    main()
