import sys
from pathlib import Path

# make `import governance...` work under pytest
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
