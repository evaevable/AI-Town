import os
import sys
from pathlib import Path

os.environ["LLM_MOCK"] = "1"
os.environ["WORLD_ENABLED"] = "0"

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
