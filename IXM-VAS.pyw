"""Start IXM-VAS. The desktop shortcut runs this with the app's own pythonw."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ixm_vas.app import main  # noqa: E402

main()
