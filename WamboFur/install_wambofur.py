"""Drag & drop this file into the Maya viewport to install Wambo Fur.

It writes a module file (WamboFur.mod) into your Maya modules folder pointing
at this directory, loads the plug-in now and sets it to auto-load.
"""
import os
import sys

import maya.cmds as cmds


def _install():
    root = os.path.dirname(os.path.abspath(__file__)).replace("\\", "/")
    mod_dir = os.path.join(cmds.internalVar(userAppDir=True), "modules")
    if not os.path.isdir(mod_dir):
        os.makedirs(mod_dir)
    mod_path = os.path.join(mod_dir, "WamboFur.mod")
    with open(mod_path, "w") as f:
        f.write("+ WamboFur 1.2.0 %s\n" % root)
        f.write("MAYA_PLUG_IN_PATH +:= plug-ins\n")
        f.write("PYTHONPATH +:= scripts\n")
        f.write("XBMLANGPATH +:= icons\n")
    scripts = root + "/scripts"
    if scripts not in sys.path:
        sys.path.append(scripts)
    plug = root + "/plug-ins/wamboFur.py"
    if not cmds.pluginInfo("wamboFur", q=True, loaded=True):
        cmds.loadPlugin(plug)
    cmds.pluginInfo(plug, edit=True, autoload=True)
    cmds.confirmDialog(title="Wambo Fur",
                       message="Wambo Fur installed.\n\nModule file:\n%s\n\nUse the 'Wambo Fur' menu." % mod_path,
                       button=["OK"])


def onMayaDroppedPythonFile(*args):
    _install()
