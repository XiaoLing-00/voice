# UI/base_panel.py
from PySide6.QtWidgets import QFrame, QVBoxLayout, QLabel
from PySide6.QtCore import Qt


class PanelFrame(QFrame):
    def __init__(self, title: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("PanelFrame")
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet("""
            QFrame#PanelFrame {
                border: 1px solid #D1D5DB;
                border-radius: 10px;
                background-color: #FFFFFF;
            }
        """)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(14, 12, 14, 12)
        self.layout.setSpacing(8)

        if title:
            lbl = QLabel(title)
            lbl.setStyleSheet("font-weight: bold; font-size: 14px; color: #111827;")
            lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            self.layout.addWidget(lbl)
