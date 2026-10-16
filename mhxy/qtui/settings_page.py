"""Qt 设置页；只编辑已有字段，校验通过后合并最新配置并保存。"""

import math

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from ..core import config as cfg_mod
from ..gui.shortcuts import (
    DEFAULT_FAILSAFE, DEFAULT_STOP_HOTKEY, FAILSAFE_CORNERS, HOTKEY_NAMES,
    _compose_stop_hotkey, _split_stop_hotkey,
)
from . import theme as T


class SettingsPage(QWidget):
    config_changed = Signal(object)

    # 范围与原设置页的滑块一致，不导入旧 Tk 页面。
    SPEED_FIELDS = (
        ("humanize", "speed", "整体速度倍率", 0.5, 3.0),
        ("humanize", "snipe_speed", "命中下单极速倍率", 1.0, 6.0),
        ("humanize", "px_per_step", "鼠标移动步长(px)", 6, 40),
        ("loop", "shelf_load_wait_sec", "商城打开等待(秒)", 0.2, 3.0),
        ("loop", "shop_close_wait_sec", "商城关闭等待(秒)", 0.1, 1.5),
        ("loop", "second_buy_load_wait_sec", "二次购买加载等待(秒)", 0.02, 0.3),
        ("loop", "second_buy_retry_interval_sec", "二次购买重试间隔(秒)", 0.01, 0.1),
        ("loop", "second_buy_timeout_sec", "二次购买匹配超时(秒)", 0.2, 3.0),
        ("loop", "purchase_timeout_sec", "购买结果等待(秒)", 0.5, 5.0),
        ("humanize", "idle_chance", "走神概率", 0.0, 0.10),
        ("humanize", "click_radius", "落点随机半径(px)", 0, 12),
        ("humanize", "interval_jitter", "间隔抖动比例", 0.0, 0.8),
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("role", "page")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*([T.SPACE_LG] * 4))
        heading = QLabel("设置", self)
        heading.setProperty("role", "heading")
        layout.addWidget(heading)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        content = QWidget(scroll)
        form = QFormLayout(content)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.window_title = QLineEdit(content)
        form.addRow("游戏窗口标题关键字", self.window_title)
        self.backend = QComboBox(content)
        self.backend.addItems(["sendinput", "pyautogui", "pydirectinput"])
        form.addRow("鼠标输入后端", self.backend)
        hotkey = QWidget(content)
        row = QHBoxLayout(hotkey)
        row.setContentsMargins(0, 0, 0, 0)
        self.modifiers = [QCheckBox(name, hotkey) for name in ("Ctrl", "Alt", "Shift")]
        for checkbox in self.modifiers:
            row.addWidget(checkbox)
        self.hotkey_key = QComboBox(hotkey)
        self.hotkey_key.addItems(HOTKEY_NAMES)
        row.addWidget(self.hotkey_key)
        form.addRow("急停快捷键（停止全部任务）", hotkey)
        self.failsafe = QComboBox(content)
        for value, label in FAILSAFE_CORNERS.items():
            self.failsafe.addItem(label, value)
        form.addRow("失控急停角", self.failsafe)
        self.appearance = QComboBox(content)
        self.appearance.addItem("夜间", "dark")
        self.appearance.addItem("白天", "light")
        form.addRow("外观", self.appearance)
        self.threshold = QLineEdit(content)
        form.addRow("识别置信度（0.60～0.99）", self.threshold)
        form.addRow(QLabel("速度与节奏（手速）", content))
        self.speed_fields = {}
        for _loc, key, label, lo, hi in self.SPEED_FIELDS:
            edit = QLineEdit(content)
            edit.setToolTip(f"{lo}～{hi}")
            self.speed_fields[key] = edit
            form.addRow(f"{label}（{lo}～{hi}）", edit)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        self.status = QLabel(self)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.save_button = QPushButton("保存设置", self)
        self.save_button.clicked.connect(self.save)
        layout.addWidget(self.save_button)
        self.refresh()

    def refresh(self):
        cfg = cfg_mod.load_config()
        self.window_title.setText(cfg.get("window_title", "梦幻西游"))
        self.backend.setCurrentText(cfg.get("input_backend", "sendinput"))
        *mods, key = _split_stop_hotkey(cfg.get("hotkey_stop", DEFAULT_STOP_HOTKEY))
        for checkbox, checked in zip(self.modifiers, mods):
            checkbox.setChecked(checked)
        self.hotkey_key.setCurrentText(key)
        for combo, value in ((self.failsafe, cfg.get("failsafe_corner", DEFAULT_FAILSAFE)),
                             (self.appearance, cfg.get("appearance", "dark"))):
            combo.setCurrentIndex(max(0, combo.findData(value)))
        loop = cfg_mod.task_config(cfg, "sniper")["loop"]
        self.threshold.setText(str(loop.get("match_threshold", 0.85)))
        for loc, key, *_ in self.SPEED_FIELDS:
            src = cfg.get("humanize", {}) if loc == "humanize" else loop
            default = (cfg_mod.DEFAULT_CONFIG["humanize"] if loc == "humanize"
                       else cfg_mod.DEFAULT_CONFIG["tasks"]["sniper"]["loop"])
            self.speed_fields[key].setText(str(src.get(key, default[key])))
        self.status.clear()

    @staticmethod
    def _number(edit, label, lo, hi, integer=False):
        try:
            value = float(edit.text().strip())
        except ValueError:
            raise ValueError(f"{label}请输入数字") from None
        if not math.isfinite(value) or not lo <= value <= hi or (integer and not value.is_integer()):
            raise ValueError(f"{label}须为 {lo}～{hi} 的{'整数' if integer else '数字'}")
        return int(value) if integer else round(value, 3)

    def save(self):
        try:
            title = self.window_title.text().strip()
            if not title:
                raise ValueError("游戏窗口标题关键字不能为空")
            key = self.hotkey_key.currentText()
            if key not in HOTKEY_NAMES:
                raise ValueError("请选择有效的急停主键")
            if self.backend.currentText() not in ("sendinput", "pyautogui", "pydirectinput"):
                raise ValueError("请选择有效的输入后端")
            if self.failsafe.currentData() not in FAILSAFE_CORNERS or self.appearance.currentData() not in ("dark", "light"):
                raise ValueError("请选择有效的失控角与外观")
            threshold = self._number(self.threshold, "识别置信度", 0.60, 0.99)
            values = {(loc, key): self._number(self.speed_fields[key], label, lo, hi,
                                              key in ("px_per_step", "click_radius"))
                      for loc, key, label, lo, hi in self.SPEED_FIELDS}
            # 校验全部完成后再读盘合并，保留标定、角色和各任务刚保存的内容。
            cfg = cfg_mod.load_config()
            cfg.update(window_title=title, input_backend=self.backend.currentText(),
                       hotkey_stop=_compose_stop_hotkey(*(c.isChecked() for c in self.modifiers), key=self.hotkey_key.currentText()),
                       failsafe_corner=self.failsafe.currentData(), appearance=self.appearance.currentData())
            tc = cfg_mod.task_config(cfg, "sniper")
            loop = tc.setdefault("loop", {})
            loop["match_threshold"] = threshold
            for (loc, key), value in values.items():
                (cfg.setdefault("humanize", {}) if loc == "humanize" else loop)[key] = value
            cfg_mod.set_task_config(cfg, "sniper", tc)
            cfg_mod.save_config(cfg)
        except (ValueError, OSError, cfg_mod.ConfigWriteRejected) as exc:
            self.status.setText(f"未保存：{exc}")
            return False
        self.config_changed.emit(cfg)
        self.status.setText("设置已保存")
        return True

    def showEvent(self, event):
        self.refresh()
        super().showEvent(event)
