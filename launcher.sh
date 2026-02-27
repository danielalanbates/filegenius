#!/usr/bin/env python3
import sys
import os

# Resolve paths relative to the app bundle
bundle_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
resources_dir = os.path.join(bundle_dir, 'Resources')

# Add Resources to Python path so 'filegenius' package is importable
sys.path.insert(0, resources_dir)

# Change to the filegenius directory for relative paths (icons, images)
os.chdir(os.path.join(resources_dir, 'filegenius'))

# Run the FileGenius application
from filegenius import main
main()