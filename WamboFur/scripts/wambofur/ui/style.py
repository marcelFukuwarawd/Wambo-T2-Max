"""Visual theme (original Wambo Fur look)."""

BG = "#1e2127"
PANEL = "#262a31"
PANEL_2 = "#2e333b"
LINE = "#3a404a"
TEXT = "#d7dce3"
TEXT_DIM = "#8a93a0"
ACCENT = "#f0a83a"
ACCENT_2 = "#5fc4f0"
ERROR = "#ff5c5c"

STYLESHEET = """
QWidget { background: %(BG)s; color: %(TEXT)s; font-size: 11px; }
QLabel, QCheckBox { background: transparent; }
QWidget#wfPanel, QTabWidget::pane, QScrollArea, QScrollArea > QWidget > QWidget { background: %(PANEL)s; }
QTabWidget::pane { border: 1px solid %(LINE)s; border-radius: 4px; top: -1px; }
QTabBar::tab { background: %(BG)s; color: %(TEXT_DIM)s; padding: 6px 14px; border: 1px solid %(LINE)s;
               border-bottom: none; border-top-left-radius: 4px; border-top-right-radius: 4px; margin-right: 2px; }
QTabBar::tab:selected { background: %(PANEL)s; color: %(TEXT)s; border-bottom: 2px solid %(ACCENT)s; }
QPushButton, QToolButton { background: %(PANEL_2)s; border: 1px solid %(LINE)s; border-radius: 4px; padding: 4px 9px; }
QPushButton:hover, QToolButton:hover { border-color: %(ACCENT)s; }
QPushButton:pressed, QToolButton:pressed { background: %(LINE)s; }
QToolButton:checked, QPushButton:checked { background: %(ACCENT)s; color: #1a1a1a; border-color: %(ACCENT)s; font-weight: bold; }
QPushButton#wfPrimary { background: %(ACCENT)s; color: #1a1a1a; font-weight: bold; border: none; }
QPushButton#wfPrimary:hover { background: #ffbd5a; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox { background: %(BG)s; border: 1px solid %(LINE)s; border-radius: 3px;
               padding: 2px 4px; selection-background-color: %(ACCENT)s; selection-color: #111; }
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus { border-color: %(ACCENT_2)s; }
QSlider::groove:horizontal { height: 4px; background: %(LINE)s; border-radius: 2px; }
QSlider::sub-page:horizontal { background: %(ACCENT)s; border-radius: 2px; }
QSlider::handle:horizontal { background: %(TEXT)s; width: 10px; margin: -4px 0; border-radius: 5px; }
QCheckBox::indicator { width: 13px; height: 13px; border: 1px solid %(LINE)s; border-radius: 3px; background: %(BG)s; }
QCheckBox::indicator:checked { background: %(ACCENT)s; border-color: %(ACCENT)s; }
QLabel#wfTitle { font-size: 14px; font-weight: bold; color: %(TEXT)s; }
QLabel#wfDim { color: %(TEXT_DIM)s; }
QLabel#wfSection { color: %(ACCENT)s; font-weight: bold; padding-top: 6px; }
QFrame#wfBar { background: %(PANEL)s; border: 1px solid %(LINE)s; border-radius: 4px; }
QStatusBar, QLabel#wfStatus { color: %(TEXT_DIM)s; }
QListWidget { background: %(PANEL)s; border: 1px solid %(LINE)s; }
QListWidget::item:selected { background: %(ACCENT)s; color: #111; }
QMenu { background: %(PANEL)s; border: 1px solid %(LINE)s; }
QMenu::item:selected { background: %(ACCENT)s; color: #111; }
QSplitter::handle { background: %(LINE)s; }
""" % dict(BG=BG, PANEL=PANEL, PANEL_2=PANEL_2, LINE=LINE, TEXT=TEXT, TEXT_DIM=TEXT_DIM,
           ACCENT=ACCENT, ACCENT_2=ACCENT_2)
