"""Wambo Fur - procedural fur / hair / feather grooming system for Autodesk Maya 2024+.

Package layout
--------------
core/  Pure-Python engine (no Maya dependency): data types, node operators,
       procedural graph, Arnold .ass writer. Testable outside Maya.
maya/  Maya integration: scene I/O, fur system nodes, groom brushes, menu.
ui/    Qt (PySide2 / PySide6) node-graph editor and tool windows.
"""

__version__ = "1.0.0"
VENDOR = "Wambo"
