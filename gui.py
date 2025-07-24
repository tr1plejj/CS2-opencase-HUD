import logging
import sys
import time
import ssl
from urllib.request import urlopen

import requests
from PyQt5.QtWidgets import (QApplication, QWidget, QLabel, QVBoxLayout,
                             QDialog, QLineEdit, QPushButton, QFormLayout, QComboBox,
                             QSlider, QHBoxLayout, QSizePolicy)
from PyQt5.QtGui import QPixmap
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QSettings

from pynput import keyboard

PANEL_STYLESHEET = """
    background-color: rgba(20, 30, 40, 0.9);
    border-radius: 10px;
    padding: 10px;
    color: white;
    font-family: Arial, sans-serif;
"""
TOGGLE_KEY = keyboard.KeyCode.from_char('`')


class AdvancedSettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Расширенные настройки HUD")
        self.settings = QSettings("MyHUD", "SteamApp")

        layout = QFormLayout(self)
        self.steamid_input = QLineEdit(self)
        self.sessionid_input = QLineEdit(self)
        self.loginsecure_input = QLineEdit(self)
        self.loginsecure_input.setEchoMode(QLineEdit.Password)
        self.case_price_input = QLineEdit(self)
        self.key_price_input = QLineEdit(self)
        self.screen_combo = QComboBox(self)
        for i, screen in enumerate(QApplication.screens()):
            self.screen_combo.addItem(f"Экран {i + 1}: {screen.size().width()}x{screen.size().height()}", i)

        self.scale_slider = QSlider(Qt.Horizontal, self)
        self.scale_slider.setRange(50, 150)
        self.scale_label = QLabel(self)
        self.scale_slider.valueChanged.connect(lambda v: self.scale_label.setText(f"{v}%"))

        self.steamid_input.setText(self.settings.value("steamid", ""))
        self.sessionid_input.setText(self.settings.value("sessionid", ""))
        self.loginsecure_input.setText(self.settings.value("steamloginsecure", ""))
        self.case_price_input.setText(self.settings.value("case_price", ""))
        self.key_price_input.setText(self.settings.value("key_price", ""))
        saved_screen_index = self.settings.value("screen_index", 0, type=int)
        self.screen_combo.setCurrentIndex(saved_screen_index)
        saved_scale = self.settings.value("scale", 100, type=int)
        self.scale_slider.setValue(saved_scale)
        self.scale_label.setText(f"{saved_scale}%")

        layout.addRow("SteamID:", self.steamid_input)
        layout.addRow("sessionid:", self.sessionid_input)
        layout.addRow("steamLoginSecure:", self.loginsecure_input)
        layout.addRow("Cтоимость кейса:", self.case_price_input)
        layout.addRow("Стоимость ключа:", self.key_price_input)
        layout.addRow("Экран для HUD:", self.screen_combo)
        scale_layout = QHBoxLayout()
        scale_layout.addWidget(self.scale_slider)
        scale_layout.addWidget(self.scale_label)
        layout.addRow("Масштаб HUD:", scale_layout)

        button_ok = QPushButton("Сохранить и запустить", self)
        button_ok.clicked.connect(self.accept)
        layout.addWidget(button_ok)

    def accept(self):
        self.settings.setValue("steamid", self.steamid_input.text())
        self.settings.setValue("sessionid", self.sessionid_input.text())
        self.settings.setValue("steamloginsecure", self.loginsecure_input.text())
        self.settings.setValue("case_price", self.case_price_input.text())
        self.settings.setValue("key_price", self.key_price_input.text())
        self.settings.setValue("screen_index", self.screen_combo.currentData())
        self.settings.setValue("scale", self.scale_slider.value())
        super().accept()

    def get_settings(self):
        return {
            "steamid": self.steamid_input.text(),
            "sessionid": self.sessionid_input.text(),
            "steamloginsecure": self.loginsecure_input.text(),
            "case_price": self.case_price_input.text(),
            "key_price": self.key_price_input.text(),
            "screen_index": self.screen_combo.currentData(),
            "scale": self.scale_slider.value() / 100.0
        }


class Worker(QThread):
    newItem = pyqtSignal(dict)
    statsUpdated = pyqtSignal(dict)

    def __init__(self, settings):
        super().__init__()
        self.steamid = settings.get("steamid")
        self.cookies = {
            "sessionid": settings.get("sessionid"),
            "steamLoginSecure": settings.get("steamloginsecure")
        }
        self.case_price = float(settings.get("case_price"))
        self.key_price = float(settings.get("key_price"))
        self.seen_items = set()
        self.is_running = True
        self.cases_opened = 0
        self.total_spent = 0.0
        self.total_drops_value = 0.0

    def _parse_price(self, price_str: str) -> float:
        cleaned_price = price_str.split()[0].replace(',', '.')
        try:
            return float(cleaned_price)
        except (ValueError, TypeError):
            return 0.0

    def _check_in_seen(self, item: dict) -> bool:
        assetid = item["assetid"]
        if assetid not in self.seen_items:
            self.seen_items.add(assetid)
            return False
        return True

    def _get_item_price(self, market_hash_name: str) -> str:
        try:
            url = 'https://steamcommunity.com/market/priceoverview/'
            params = {"appid": 730, "market_hash_name": market_hash_name, "currency": 5}
            response = requests.get(url, params=params, timeout=5).json()
            return response.get("lowest_price", "0,00 pуб.")
        except requests.RequestException:
            return "Ошибка цены"

    def _get_inventory(self) -> tuple:
        try:
            url = f"https://steamcommunity.com/inventory/{self.steamid}/730/2?l=russian&count=75"
            response = requests.get(url, cookies=self.cookies, timeout=5).json()
            return response.get("assets"), response.get("descriptions")
        except requests.RequestException:
            return None, None

    def run(self):
        print("Загрузка первоначального инвентаря...")
        initial_items_assets, _ = self._get_inventory()
        if initial_items_assets:
            for item in initial_items_assets:
                self._check_in_seen(item)
        print(f"Загружено {len(self.seen_items)} предметов. Начинаю отслеживание...")

        while self.is_running:
            items_assets, items_descriptions = self._get_inventory()
            if not items_assets or not items_descriptions:
                time.sleep(3)
                continue

            last_item_asset = items_assets[0]
            if not self._check_in_seen(last_item_asset):
                time.sleep(5)
                last_item_desc = next((desc for desc in items_descriptions if
                                       desc['classid'] == last_item_asset['classid'] and desc['instanceid'] ==
                                       last_item_asset['instanceid']), None)

                market_hash_name = last_item_desc.get("market_hash_name", "Без имени")
                print(f"Найден новый предмет: {market_hash_name}")

                price_str = self._get_item_price(market_hash_name)
                price_float = self._parse_price(price_str)

                self.cases_opened += 1
                self.total_spent += self.case_price + self.key_price
                self.total_drops_value += price_float
                self.statsUpdated.emit(
                    {"spent": self.total_spent, "drops": self.total_drops_value, "cases": self.cases_opened})

                icon_url = last_item_desc.get("icon_url", "")
                full_icon_url = f"https://cdn.steamcommunity.com/economy/image/{icon_url}/360fx360f" if icon_url else ""

                self.newItem.emit({"name": market_hash_name, "price_str": price_str, "price_float": price_float,
                                   "image_url": full_icon_url})

            time.sleep(3)

    def stop(self):
        self.is_running = False


class ItemPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.base_size = (320, 380)
        self.setStyleSheet(PANEL_STYLESHEET)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(10, 10, 10, 10)
        self.layout.setSpacing(5)

        self.title_label = QLabel()
        self.title_label.setAlignment(Qt.AlignCenter)
        self.title_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        self.layout.addWidget(self.title_label)

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignCenter)
        self.layout.addWidget(self.image_label, stretch=5)

        self.name_label = QLabel()
        self.name_label.setAlignment(Qt.AlignCenter)
        self.name_label.setWordWrap(True)
        self.layout.addWidget(self.name_label, stretch=1)

        self.price_label = QLabel()
        self.price_label.setAlignment(Qt.AlignCenter)
        self.price_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #5cc5fa;")
        self.layout.addWidget(self.price_label, stretch=1)

    def set_scale(self, scale):
        w, h = int(self.base_size[0] * scale), int(self.base_size[1] * scale)
        self.resize(w, h)
        self.setMinimumSize(w, h)

        font = self.name_label.font()
        font.setPointSize(int(10 * scale))
        for label in [self.name_label, self.price_label, self.title_label]:
            label.setFont(font)

    def set_image(self, pixmap):
        scaled = pixmap.scaled(self.image_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.image_label.setPixmap(scaled)

    def set_title(self, text):
        self.title_label.setText(text)

    def update_info(self, data: dict):
        self.name_label.setText(data.get("name", "N/A"))
        self.price_label.setText(f"Цена: {data.get('price_str', 'N/A')}")
        image_url = data.get("image_url")
        if image_url:
            try:
                ssl_context = ssl._create_unverified_context()
                image_data = urlopen(image_url, context=ssl_context, timeout=5).read()
                pixmap = QPixmap()
                pixmap.loadFromData(image_data)
                self.image_label.setPixmap(
                    pixmap.scaled(self.image_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
            except Exception as e:
                self.image_label.setText("Ошибка загрузки")
                print(f"Не удалось загрузить изображение: {e}")
        else:
            self.image_label.setText("Нет изображения")


class StatsPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.base_size = (320, 170)
        self.setStyleSheet(PANEL_STYLESHEET)
        self.layout = QVBoxLayout(self)
        self.layout.setAlignment(Qt.AlignTop)
        self.layout.setSpacing(8)
        self.layout.setContentsMargins(10, 10, 10, 10)

        self.spent_label = QLabel("Потрачено: 0.00 pуб.")
        self.spent_label.setStyleSheet(f"font-size: 14px; color: white;")
        self.spent_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self.layout.addWidget(self.spent_label)

        self.drops_label = QLabel("Стоимость дропа: 0.00 pуб.")
        self.drops_label.setStyleSheet(f"font-size: 14px; color: #88ff88;")
        self.drops_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self.layout.addWidget(self.drops_label)

        self.profit_label = QLabel("Профит: 0.00 pуб.")
        self.profit_label.setStyleSheet(f"font-size: 14px; color: white;")
        self.profit_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self.layout.addWidget(self.profit_label)

        self.cases_label = QLabel("Открыто кейсов: 0")
        self.cases_label.setStyleSheet(f"font-size: 14px; color: white;")
        self.cases_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self.layout.addWidget(self.cases_label)

    def set_scale(self, scale):
        w, h = int(self.base_size[0] * scale), int(self.base_size[1] * scale)
        self.setMinimumSize(w, h)
        self.resize(w, h)
        font = self.spent_label.font()
        font.setPointSize(int(10 * scale))

        self.spent_label.setFont(font)
        self.spent_label.setMinimumHeight(int(20 * scale))

        self.drops_label.setFont(font)
        self.drops_label.setMinimumHeight(int(20 * scale))

        self.profit_label.setFont(font)
        self.profit_label.setMinimumHeight(int(20 * scale))

        self.cases_label.setFont(font)
        self.cases_label.setMinimumHeight(int(20 * scale))

    def update_stats(self, data: dict):
        spent, drops, cases = data.get("spent", 0.0), data.get("drops", 0.0), data.get("cases", 0)
        profit = drops - spent
        self.spent_label.setText(f"Потрачено: {spent:.2f} pуб.")
        self.drops_label.setText(f"Стоимость дропа: {drops:.2f} pуб.")
        self.cases_label.setText(f"Открыто кейсов: {cases}")
        profit_color = "#ff8888;" if profit < 0 else "#88ff88;"
        self.profit_label.setText(f"Профит: {profit:.2f} pуб.")
        self.profit_label.setStyleSheet(f"font-size: 14px; color: {profit_color}")


class HUD(QWidget):
    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_QuitOnClose, True)
        self.is_visible = True
        self.best_item_data = {"price_float": 0.0}  # Начальное лучшее значение - 0
        self.panels = {}
        self.setup_panels()
        self.worker = Worker(settings)
        self.worker.newItem.connect(self.update_item_panels)
        self.worker.statsUpdated.connect(self.update_stats_panel)
        self.worker.start()

    def setup_panels(self):
        scale = self.settings.get("scale", 1.0)
        screen_rect = QApplication.screens()[self.settings.get("screen_index", 0)].geometry()
        sw, sh = screen_rect.width(), screen_rect.height()
        margin = int(20 * scale)

        stats_panel = StatsPanel(self)
        stats_panel.set_scale(scale)
        stats_panel.move(margin, margin)
        self.panels["stats"] = stats_panel

        best_item_panel = ItemPanel(self)
        best_item_panel.base_size = (240, 285)
        best_item_panel.set_title("Лучший дроп")
        best_item_panel.set_scale(scale)
        best_item_panel.move(margin, stats_panel.y() + stats_panel.height() + margin)
        self.panels["best_item"] = best_item_panel

        last_item_panel = ItemPanel(self)
        last_item_panel.set_title("Последний дроп")
        last_item_panel.set_scale(scale)
        last_item_panel.move(sw - last_item_panel.width() - margin, margin)
        self.panels["last_item"] = last_item_panel

    def update_item_panels(self, data: dict):
        self.panels["last_item"].update_info(data)

        if data["price_float"] > self.best_item_data["price_float"]:
            self.best_item_data = data
            self.panels["best_item"].update_info(data)

    def update_stats_panel(self, data: dict):
        self.panels["stats"].update_stats(data)

    def toggle_visibility(self):
        self.is_visible = not self.is_visible
        self.setVisible(self.is_visible)

    def closeEvent(self, event):
        self.worker.stop()
        self.worker.wait()
        event.accept()


def main():
    app = QApplication(sys.argv)
    settings_dialog = AdvancedSettingsDialog()
    if settings_dialog.exec_() == QDialog.Accepted:
        settings = settings_dialog.get_settings()
        if not settings.get("steamid") or not settings.get("sessionid") or not settings.get("steamloginsecure"):
            print("Не все данные введены. Запуск отменен.")
            sys.exit()

        screen_index = settings.get("screen_index", 0)
        screen_rect = QApplication.screens()[screen_index].geometry()
        hud = HUD(settings)
        hud.setGeometry(screen_rect)
        hud.show()

        listener = None

        def on_press(key):
            if key == TOGGLE_KEY:
                hud.toggle_visibility()

        try:
            listener = keyboard.Listener(on_press=on_press)
            listener.start()
            print("HUD запущен. Нажмите ` (Ё), чтобы скрыть/показать.")
            sys.exit(app.exec_())
        finally:
            if listener:
                listener.stop()
            hud.close()
    else:
        print("Запуск отменен пользователем.")
        sys.exit()


if __name__ == '__main__':
    main()
