#!/usr/bin/python3
import subprocess
import os
from display import Display

if __name__ == "__main__":
    # Get directory of this script (its-a-plane.py)
    base_dir = os.path.dirname(os.path.abspath(__file__))

    # Build path to web/app.py
    app_path = os.path.join(base_dir, "web", "app.py")

    # Start Flask server in background
    subprocess.Popen(["python3", app_path])

    # Start display loop
    run_text = Display()
    run_text.run()
