#!/usr/bin/env python3
"""
PrismSSH - Modern SSH Client (Refactored)

This is the refactored version that uses the modular architecture.
Run this instead of the original prismssh.py for better code organization.
"""

import sys
from pathlib import Path

def main():
    """Main entry point for refactored PrismSSH."""
    # Add src directory to Python path
    src_path = Path(__file__).parent / "src"
    sys.path.insert(0, str(src_path))
    
    try:
        # Import and run the main application
        from main import main as app_main
        app_main()
    except ImportError as e:
        print(f"Import error: {e}")
        print("Please ensure all dependencies are installed:")
        print("pip install -r requirements.txt")
        sys.exit(1)
    except Exception as e:
        print(f"Error starting PrismSSH: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()