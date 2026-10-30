import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "outil"))
import interface

class InterfaceTests(unittest.TestCase):
    def test_hidden_window_constructs_and_closes(self):
        real_tk = interface.tk.Tk
        def root():
            window = real_tk()
            window.withdraw()
            window.after(150, window.destroy)
            return window
        with patch.object(interface.tk, "Tk", root):
            interface.main()

if __name__ == "__main__":
    unittest.main()
