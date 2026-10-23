"""``python -m trading_core <command>`` — same as the ``algo`` launcher."""

import sys

from trading_core.cli import main

sys.exit(main())
