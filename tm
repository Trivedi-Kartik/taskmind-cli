#!/usr/bin/env sh
# Run TaskMind without installing anything: ./tm <command>
# -I keeps Python from importing code from the current directory (e.g. a repo
# with its own `taskmind/` folder), so only this checkout's code ever runs.
SELF=$(readlink -f "$0" 2>/dev/null || python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$0")
TASKMIND_DIR=$(dirname "$SELF") exec python3 -I -c '
import os, sys
sys.path.insert(0, os.environ["TASKMIND_DIR"])
from taskmind.cli import main
sys.exit(main())' "$@"
