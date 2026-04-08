# UI/components/ScoreCardBubble.py
"""
评分卡片气泡，在面试结束后展示各维度得分及改进建议。
依赖 eval_result 鸭子类型（含 tech/logic/depth/clarity/overall_score 和 suggestion）。
"""

from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel,
    QGraphicsDropShadowEffect,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

from UI.components.info.Theme import T


class ScoreCardBubble(QFrame):
    def __init__(self, eval_result, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)

        card = QFrame()
        card.setObjectName("score_card")
        card.setStyleSheet(f"""
            QFrame#score_card {{
                background: {T.SURFACE2};
                border: 1px solid {T.NEON}22;
                border-left: 3px solid {T.NEON};
                border-radius: 12px;
            }}
        """)

        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(T.NEON).darker(300))
        shadow.setOffset(0, 4)
        card.setGraphicsEffect(shadow)

        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(16, 14, 16, 14)
        card_lay.setSpacing(10)

        # ── 标题 ──────────────────────────────────────────────────────────────
        title = QLabel("📊  本题评估报告")
        title.setStyleSheet(
            f"font-weight: 700; font-size: 13px; color: {T.NEON};"
            f"font-family: {T.FONT}; background: transparent;"
        )
        card_lay.addWidget(title)

        # ── 得分行 ────────────────────────────────────────────────────────────
        scores_row = QHBoxLayout()
        scores_row.setSpacing(0)

        score_items = [
            ("技术", eval_result.tech_score,    T.NEON),
            ("逻辑", eval_result.logic_score,   T.PURPLE),
            ("深度", eval_result.depth_score,   T.YELLOW),
            ("表达", eval_result.clarity_score, T.GREEN),
        ]
        for label, score, color in score_items:
            item_frame = QFrame()
            item_frame.setStyleSheet("background: transparent;")
            item_lay = QVBoxLayout(item_frame)
            item_lay.setContentsMargins(10, 6, 10, 6)
            item_lay.setSpacing(2)
            item_lay.setAlignment(Qt.AlignCenter)

            val_lbl = QLabel(str(score))
            val_lbl.setAlignment(Qt.AlignCenter)
            val_lbl.setStyleSheet(
                f"font-size: 22px; font-weight: 900; color: {color};"
                f"font-family: {T.FONT_MONO}; background: transparent;"
            )
            name_lbl = QLabel(label)
            name_lbl.setAlignment(Qt.AlignCenter)
            name_lbl.setStyleSheet(
                f"font-size: 10px; color: {T.TEXT_DIM}; background: transparent;"
            )
            item_lay.addWidget(val_lbl)
            item_lay.addWidget(name_lbl)
            scores_row.addWidget(item_frame)

        # 分隔线
        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setStyleSheet(f"color: {T.BORDER2}; background: {T.BORDER2};")
        sep.setFixedWidth(1)
        scores_row.addWidget(sep)

        # 综合得分
        overall_frame = QFrame()
        overall_frame.setStyleSheet(
            f"background: {T.GREEN}11; border-radius: 8px;"
        )
        overall_lay = QVBoxLayout(overall_frame)
        overall_lay.setContentsMargins(14, 8, 14, 8)
        overall_lay.setAlignment(Qt.AlignCenter)

        overall_val = QLabel(f"{eval_result.overall_score:.1f}")
        overall_val.setAlignment(Qt.AlignCenter)
        overall_val.setStyleSheet(
            f"font-size: 26px; font-weight: 900; color: {T.GREEN};"
            f"font-family: {T.FONT_MONO}; background: transparent;"
        )
        overall_name = QLabel("综合")
        overall_name.setAlignment(Qt.AlignCenter)
        overall_name.setStyleSheet(
            f"font-size: 10px; color: {T.GREEN}AA; background: transparent;"
        )
        overall_lay.addWidget(overall_val)
        overall_lay.addWidget(overall_name)
        scores_row.addWidget(overall_frame)

        card_lay.addLayout(scores_row)

        # ── 建议 ──────────────────────────────────────────────────────────────
        if eval_result.suggestion:
            tip = QLabel(f"💡  {eval_result.suggestion}")
            tip.setWordWrap(True)
            tip.setStyleSheet(f"""
                font-size: 12px; color: {T.TEXT_DIM};
                background: {T.SURFACE3};
                border-radius: 6px;
                padding: 8px 10px;
                font-family: {T.FONT};
            """)
            card_lay.addWidget(tip)

        outer.addWidget(card, stretch=9)
        outer.addStretch(1)