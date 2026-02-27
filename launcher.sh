#!/bin/bash
# Launcher for FileGenius
# Uses Python.app framework binary for proper GUI/Tk support

# Get the directory of this script
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
RESOURCES_DIR="$DIR/../Resources"

# Use the GUI Python framework which is required for tkinter GUI apps
if [ -f "/Library/Frameworks/Python.framework/Versions/3.14/Resources/Python.app/Contents/MacOS/Python" ]; then
    PYTHON="/Library/Frameworks/Python.framework/Versions/3.14/Resources/Python.app/Contents/MacOS/Python"
elif [ -f "/Library/Frameworks/Python.framework/Versions/3.13/Resources/Python.app/Contents/MacOS/Python" ]; then
    PYTHON="/Library/Frameworks/Python.framework/Versions/3.13/Resources/Python.app/Contents/MacOS/Python"
elif [ -f "/Library/Frameworks/Python.framework/Versions/3.12/Resources/Python.app/Contents/MacOS/Python" ]; then
    PYTHON="/Library/Frameworks/Python.framework/Versions/3.12/Resources/Python.app/Contents/MacOS/Python"
else
    # Fallback to regular python3
    PYTHON="/usr/bin/python3"
fi

# Add Resources to Python path and run
export PYTHONPATH="$RESOURCES_DIR:$PYTHONPATH"
cd "$RESOURCES_DIR/filegenius"
exec "$PYTHON" -m filegenius
