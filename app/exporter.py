import os
import subprocess

PYTHON = "/opt/services/paybot/venv/bin/python"
SCRIPT = "/opt/services/paybot/app/export_one.py"

def export_one():
    env = os.environ.copy()
    if not env.get("GSHEET_ID"):
        raise RuntimeError("GSHEET_ID is not set")

    subprocess.run(
        [PYTHON, SCRIPT],
        env=env,
        check=True,
    )
