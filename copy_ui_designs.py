import shutil
import os

artifact_dir = r"C:\Users\emre\.gemini\antigravity-ide\brain\81825bbb-82d0-4e35-9c31-20a2ca352b92"
target_dir = r"c:\Users\emre\Desktop\QR odeme\static\img\ui_designs"

os.makedirs(target_dir, exist_ok=True)

files = [f for f in os.listdir(artifact_dir) if f.startswith("ui_design_concept_") and f.endswith(".png")]
for f in files:
    src = os.path.join(artifact_dir, f)
    dst = os.path.join(target_dir, f)
    shutil.copy2(src, dst)
    print(f"Copied {f} to {dst}")
