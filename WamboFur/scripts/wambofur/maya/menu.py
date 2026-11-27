"""'Wambo Fur' menu in Maya's main menu bar."""
import maya.cmds as cmds
import maya.mel as mel

MENU = "wamboFurMenu"


def _open(*a):
    from ..ui import main_window
    main_window.show()


def _new_fur(*a):
    from . import scene, system
    mesh = scene.selected("mesh")
    if not mesh:
        cmds.warning("Wambo Fur: select a polygon mesh")
        return
    s = system.FurSystem.create(mesh, name=mesh.split("|")[-1] + "_fur1")
    s.update_preview()
    _open()


def _update_all(*a):
    from . import system
    for name in system.list_systems():
        system.FurSystem(name).update_preview()


def _brush(mode):
    def run(*a):
        from . import brushes, system
        systems = system.list_systems()
        sel = cmds.ls(selection=True) or []
        target = next((s for s in systems if s in sel), systems[0] if systems else None)
        if not target:
            cmds.warning("Wambo Fur: no fur system")
            return
        g = system.FurSystem(target).guides_group()
        if not g:
            cmds.warning("Wambo Fur: create guides first")
            return
        brushes.activate(mode, g)
    return run


def _export(*a):
    from . import system, render
    for name in system.list_systems():
        paths, n = render.render(system.FurSystem(name))
        print("Wambo Fur: %s -> %s (%d curves)" % (name, paths[0], n))


def create():
    remove()
    gmain = mel.eval("$tmp = $gMainWindow")
    cmds.menu(MENU, label="Wambo Fur", parent=gmain, tearOff=True)
    cmds.menuItem(label="Wambo Fur Editor...", command=_open, image="hairPaint.png")
    cmds.menuItem(divider=True)
    cmds.menuItem(label="New Fur on Selected Mesh", command=_new_fur)
    cmds.menuItem(label="Update All Previews", command=_update_all)
    cmds.menuItem(divider=True, dividerLabel="Groom Brushes")
    for m in ("comb", "length", "smooth", "puff", "cut", "add", "remove"):
        cmds.menuItem(label=m.title(), command=_brush(m))
    cmds.menuItem(divider=True)
    cmds.menuItem(label="Export All for Render (current frame)", command=_export)


def remove():
    if cmds.menu(MENU, exists=True):
        cmds.deleteUI(MENU)
