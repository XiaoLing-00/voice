# UI/components/ButtonFactory.py
"""
统一按钮工厂。
提供 primary / solid / ghost / tag 四种风格。
"""

from PySide6.QtWidgets import QPushButton
from PySide6.QtCore import Qt

from UI.components.info.Theme import T


class ButtonFactory:
    @staticmethod
    def primary(text: str, color: str = T.NEON, height: int = 38) -> QPushButton:
        """镂空描边按钮，hover 时加深背景。"""
        btn = QPushButton(text)
        btn.setFixedHeight(height)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(f"""
            QPushButton {{
                background: {color}22; color: {color};
                border: 1px solid {color}66;
                border-radius: {height // 2}px;
                font-size: 13px; font-weight: 700;
                padding: 0 18px; font-family: {T.FONT};
            }}
            QPushButton:hover  {{ background: {color}44; border-color: {color}; }}
            QPushButton:pressed {{ background: {color}66; }}
            QPushButton:disabled {{
                background: {T.BORDER}; color: {T.TEXT_MUTE};
                border-color: {T.BORDER};
            }}
        """)
        return btn

    @staticmethod
    @staticmethod
    def solid(text: str, color: str = T.NEON, height: int = 38, width: int | None = None) -> QPushButton:
        """实心填充按钮（主操作）。"""
        btn = QPushButton(text)
        btn.setFixedHeight(height)
        if width:
            btn.setFixedWidth(width)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(f"""
                QPushButton {{
                    background: {color}; color: #0A0A14;
                    border: none; border-radius: {height // 2}px;
                    font-size: 13px; font-weight: 800;
                    padding: 0 18px; font-family: {T.FONT};
                }}
                QPushButton:hover   {{ background: {color}CC; }}
                QPushButton:pressed {{ background: {color}AA; }}
                QPushButton:disabled {{
                    background: {T.BORDER}; color: {T.TEXT_MUTE};
                }}
            """)
        return btn

    @staticmethod
    def ghost(text: str, height: int = 30) -> QPushButton:
        """透明底色幽灵按钮，常用于次要操作。"""
        btn = QPushButton(text)
        btn.setFixedHeight(height)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {T.TEXT_DIM};
                border: 1px solid {T.BORDER2}; border-radius: 6px;
                font-size: 12px; padding: 0 12px; font-family: {T.FONT};
            }}
            QPushButton:hover {{
                color: {T.ACCENT}; border-color: {T.ACCENT};
            }}
        """)
        return btn

    @staticmethod
    def tag(text: str, color: str, height: int = 32) -> QPushButton:
        """标签式快捷按钮，hover 时着色。"""
        btn = QPushButton(text)
        btn.setFixedHeight(height)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(f"""
            QPushButton {{
                background: {T.SURFACE}; color: {T.TEXT_DIM};
                border: 1px solid {T.BORDER2};
                border-radius: {height // 2}px;
                font-size: 11px; font-weight: 600;
                padding: 0 14px; font-family: {T.FONT};
            }}
            QPushButton:hover {{
                color: {color}; border-color: {color}55;
                background: {color}11;
            }}
        """)
        return btn