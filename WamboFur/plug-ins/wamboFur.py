"""Wambo Fur - Maya plug-in entry point (Maya 2024+).

Load it from Windows > Settings/Preferences > Plug-in Manager, or
    cmds.loadPlugin("wamboFur.py")
"""
import sys
import os

import maya.api.OpenMaya as om
import maya.cmds as cmds

# make the package importable even when the module file is not installed
_here = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else ""
_scripts = os.path.normpath(os.path.join(_here, "..", "scripts"))
if _here and os.path.isdir(_scripts) and _scripts not in sys.path:
    sys.path.append(_scripts)

VENDOR = "Wambo"
VERSION = "1.0.0"


def maya_useNewAPI():
    pass


class WamboFurCmd(om.MPxCommand):
    """MEL/Python command:
        wamboFur               -> open the editor
        wamboFur -u            -> update every fur preview
        wamboFur -e            -> export every fur system for render (current frame)
        wamboFur -e -s 1 -f 100 -> export a frame range
    """
    NAME = "wamboFur"

    @staticmethod
    def creator():
        return WamboFurCmd()

    @staticmethod
    def syntax():
        s = om.MSyntax()
        s.addFlag("-u", "-update")
        s.addFlag("-e", "-export")
        s.addFlag("-s", "-start", om.MSyntax.kDouble)
        s.addFlag("-f", "-end", om.MSyntax.kDouble)
        return s

    def doIt(self, args):
        db = om.MArgDatabase(self.syntax(), args)
        from wambofur.maya import system, render
        if db.isFlagSet("-u"):
            for n in system.list_systems():
                system.FurSystem(n).update_preview()
            return
        if db.isFlagSet("-e"):
            start = db.flagArgumentDouble("-s", 0) if db.isFlagSet("-s") else None
            end = db.flagArgumentDouble("-f", 0) if db.isFlagSet("-f") else start
            out = []
            for n in system.list_systems():
                paths, _ = render.render(system.FurSystem(n), start, end)
                out.extend(paths)
            self.setResult(out)
            return
        from wambofur.ui import main_window
        main_window.show()


def initializePlugin(obj):
    plugin = om.MFnPlugin(obj, VENDOR, VERSION, "Any")
    plugin.registerCommand(WamboFurCmd.NAME, WamboFurCmd.creator, WamboFurCmd.syntax)
    if not om.MGlobal.mayaState() == om.MGlobal.kBatch:
        from wambofur.maya import menu
        cmds.evalDeferred(menu.create)


def uninitializePlugin(obj):
    plugin = om.MFnPlugin(obj)
    plugin.deregisterCommand(WamboFurCmd.NAME)
    try:
        from wambofur.maya import menu
        menu.remove()
    except Exception:
        pass
