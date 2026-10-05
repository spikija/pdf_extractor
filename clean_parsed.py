from pathlib import Path
import shutil

parsed_root = Path(r"C:\app\pdf_extractor\data\parsed")
if parsed_root.exists():
    shutil.rmtree(parsed_root)
parsed_root.mkdir(parents=True)
print("Cleaned parsed directory")