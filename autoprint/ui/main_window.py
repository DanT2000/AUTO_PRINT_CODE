"""Главное окно: библиотека образцов, вкладки, панель управления печатью, трей."""
from __future__ import annotations

import time
from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import QByteArray, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
                               QProgressBar, QPushButton, QSpinBox, QSplitter, QSystemTrayIcon, QTabWidget,
                               QToolButton, QVBoxLayout, QWidget)

from .. import APP_NAME, __version__
from ..guard import InputGuard
from ..hotkeys import HotkeyManager, parse_hotkey
from ..importers import export_ipynb, import_ipynb, import_markdown
from ..sounds import KeySoundPlayer
from ..storage import PROFILES, Settings, Template, TemplateStore
from ..typer import COUNTDOWN, FINISHED, IDLE, PAUSED, RUNNING, TypingEngine
from .blocks import typing_slice
from .settings_dialog import HOTKEYS, SettingsDialog
from .template_view import TemplateView

STATE_COLORS = {IDLE: "#3b82f6", COUNTDOWN: "#a855f7", RUNNING: "#2ea043", PAUSED: "#d29922",
                FINISHED: "#3b82f6"}
STATE_TEXT = {IDLE: "Готов", COUNTDOWN: "Отсчёт…", RUNNING: "Печатает", PAUSED: "Пауза",
              FINISHED: "Готово ✓"}


def make_icon(color: str) -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor("#1e2229"))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(2, 2, 60, 60, 12, 12)
    p.setBrush(QColor(color))
    p.drawRoundedRect(2, 50, 60, 12, 6, 6)
    p.setPen(QColor(color))
    f = QFont("Consolas", 22)
    f.setBold(True)
    p.setFont(f)
    p.drawText(pm.rect().adjusted(0, -8, 0, -8), Qt.AlignmentFlag.AlignCenter, "</>")
    p.end()
    return QIcon(pm)


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings, store: TemplateStore) -> None:
        super().__init__()
        self.settings = settings
        self.store = store
        self.icons = {st: make_icon(c) for st, c in STATE_COLORS.items()}
        self.setWindowIcon(self.icons[IDLE])
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1200, 780)

        self.engine = TypingEngine(settings)
        self.player = KeySoundPlayer(settings.sound_style, settings.sound_volume, settings.sound_enabled)
        self.engine.sound.connect(self.player.play)
        self.engine.state_changed.connect(self._on_state)
        self.engine.progress.connect(self._on_progress)
        self.engine.message.connect(self._notify)
        self.engine.countdown.connect(self._on_countdown)
        self.engine.finished.connect(self._on_finished)
        self.job: dict | None = None   # {tid, bid, base, text}

        self.guard = InputGuard()
        self.guard.tripped.connect(self._on_guard)

        self.hotkeys = HotkeyManager()
        self.hotkeys.triggered.connect(self._on_hotkey)
        self.hotkeys.failed.connect(lambda t: self._notify(
            f"Не удалось занять хоткеи: {t} (заняты другой программой?). Смените их в настройках.", True))

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(700)
        self._save_timer.timeout.connect(self._save_store)

        self._build_ui()
        self._build_tray()
        self._apply_settings()
        self._restore_session()

    # ================================================================ UI
    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 6, 8, 4)
        root.setSpacing(6)

        # ---- панель управления
        bar = QHBoxLayout()
        self.btn_toggle = QPushButton("▶ Старт")
        self.btn_toggle.setMinimumWidth(130)
        self.btn_toggle.clicked.connect(lambda: self.cmd_toggle(from_button=True))
        self.btn_restart = QPushButton("⟲ Сначала")
        self.btn_restart.clicked.connect(lambda: self.cmd_restart(from_button=True))
        self.btn_stop = QPushButton("■ Стоп")
        self.btn_stop.clicked.connect(self.cmd_stop)
        for b in (self.btn_toggle, self.btn_restart, self.btn_stop):
            bar.addWidget(b)
        bar.addSpacing(12)
        bar.addWidget(QLabel("Окно:"))
        self.profile = QComboBox()
        for k, v in PROFILES.items():
            self.profile.addItem(v, k)
        self.profile.currentIndexChanged.connect(self._on_profile)
        bar.addWidget(self.profile)
        bar.addWidget(QLabel("Скорость:"))
        self.cpm = QSpinBox()
        self.cpm.setRange(30, 3000)
        self.cpm.setSingleStep(20)
        self.cpm.setSuffix(" симв/мин")
        self.cpm.valueChanged.connect(self._on_cpm)
        bar.addWidget(self.cpm)
        self.btn_sound = QToolButton()
        self.btn_sound.setCheckable(True)
        self.btn_sound.setToolTip("Звук клавиш")
        self.btn_sound.toggled.connect(self._on_sound_toggle)
        bar.addWidget(self.btn_sound)
        bar.addStretch(1)
        btn_settings = QPushButton("⚙ Настройки")
        btn_settings.clicked.connect(self.open_settings)
        bar.addWidget(btn_settings)
        root.addLayout(bar)

        # ---- строка состояния печати
        st = QHBoxLayout()
        self.state_lbl = QLabel()
        self.state_lbl.setMinimumWidth(110)
        self.armed_lbl = QLabel()
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(260)
        self.progress.setTextVisible(True)
        self.hint_lbl = QLabel()
        self.hint_lbl.setStyleSheet("color: gray;")
        st.addWidget(self.state_lbl)
        st.addWidget(self.armed_lbl, 1)
        st.addWidget(self.progress)
        st.addWidget(self.hint_lbl)
        root.addLayout(st)

        # ---- библиотека + вкладки
        self.split = QSplitter(Qt.Orientation.Horizontal)
        lib = QWidget()
        ll = QVBoxLayout(lib)
        ll.setContentsMargins(0, 0, 4, 0)
        ll.addWidget(QLabel("<b>Образцы</b>"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter_library)
        ll.addWidget(self.search)
        self.library = QListWidget()
        self.library.itemClicked.connect(lambda it: self.open_template(it.data(Qt.ItemDataRole.UserRole)))
        self.library.itemChanged.connect(self._on_library_renamed)
        self.library.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.library.customContextMenuRequested.connect(self._library_menu)
        ll.addWidget(self.library, 1)
        row1 = QHBoxLayout()
        b_new = QPushButton("＋ Новый")
        b_new.clicked.connect(self.new_template)
        b_imp = QPushButton("⇩ Импорт…")
        b_imp.setToolTip("Импорт из .ipynb (Jupyter), .md или .json")
        b_imp.clicked.connect(self.import_files)
        row1.addWidget(b_new)
        row1.addWidget(b_imp)
        ll.addLayout(row1)
        self.split.addWidget(lib)

        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.setDocumentMode(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.tabs.tabBar().tabBarDoubleClicked.connect(self._rename_tab)
        self.tabs.setCornerWidget(self._tabs_corner(), Qt.Corner.TopRightCorner)
        self.split.addWidget(self.tabs)
        self.split.setStretchFactor(1, 1)
        self.split.setSizes([230, 970])
        root.addWidget(self.split, 1)
        self.setCentralWidget(central)

        # ---- меню
        m = self.menuBar().addMenu("Файл")
        self._act(m, "Новый образец", self.new_template, "Ctrl+N")
        self._act(m, "Импорт (.ipynb, .md, .json)…", self.import_files, "Ctrl+O")
        self._act(m, "Экспорт образца в .ipynb…", lambda: self.export_current("ipynb"))
        self._act(m, "Экспорт образца в .json…", lambda: self.export_current("json"))
        m.addSeparator()
        self._act(m, "Открыть папку с данными", self._open_data_dir)
        self._act(m, "Открыть журнал работы", self._open_log)
        m.addSeparator()
        self._act(m, "Выход", self.quit_app, "Ctrl+Q")
        m = self.menuBar().addMenu("Печать")
        self._act(m, "Старт / пауза", lambda: self.cmd_toggle(from_button=True))
        self._act(m, "Сначала", lambda: self.cmd_restart(from_button=True))
        self._act(m, "Стоп", self.cmd_stop)
        m.addSeparator()
        self._act(m, "Следующий блок кода", lambda: self.cmd_block(+1), "Alt+Down")
        self._act(m, "Предыдущий блок кода", lambda: self.cmd_block(-1), "Alt+Up")
        m.addSeparator()
        self._act(m, "Настройки…", self.open_settings, "Ctrl+,")
        m = self.menuBar().addMenu("Справка")
        self._act(m, "Как пользоваться", self.show_help, "F1")

        QShortcut(QKeySequence("Ctrl+Tab"), self, lambda: self.cmd_next_tab(+1))
        QShortcut(QKeySequence("Ctrl+Shift+Tab"), self, lambda: self.cmd_next_tab(-1))
        QShortcut(QKeySequence("Ctrl+W"), self, lambda: self.close_tab(self.tabs.currentIndex()))
        for i in range(1, 10):
            QShortcut(QKeySequence(f"Ctrl+{i}"), self, lambda i=i: self._goto_tab(i - 1))

    def _tabs_corner(self) -> QWidget:
        b = QToolButton()
        b.setText("☰")
        b.setToolTip("Список открытых вкладок")
        b.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(b)
        menu.aboutToShow.connect(lambda: self._fill_tabs_menu(menu))
        b.setMenu(menu)
        return b

    def _fill_tabs_menu(self, menu: QMenu) -> None:
        menu.clear()
        for i in range(self.tabs.count()):
            a = menu.addAction(f"{i + 1}. {self.tabs.tabText(i)}")
            a.triggered.connect(lambda _=False, i=i: self.tabs.setCurrentIndex(i))

    def _act(self, menu, text, slot, shortcut=None) -> QAction:
        a = QAction(text, self)
        a.triggered.connect(slot)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        menu.addAction(a)
        return a

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(self.icons[IDLE], self)
        menu = QMenu()
        menu.addAction("Показать окно", self._show_window)
        menu.addSeparator()
        menu.addAction("Старт / пауза", lambda: self.cmd_toggle(from_button=False, countdown=3))
        menu.addAction("Стоп", self.cmd_stop)
        menu.addSeparator()
        menu.addAction("Выход", self.quit_app)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda r: self._show_window()
                                    if r == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.setToolTip(APP_NAME)
        self.tray.show()

    # ================================================================ настройки
    def _apply_settings(self) -> None:
        s = self.settings
        self.profile.blockSignals(True)
        self.profile.setCurrentIndex(max(0, self.profile.findData(s.profile)))
        self.profile.blockSignals(False)
        self.cpm.blockSignals(True)
        self.cpm.setValue(s.cpm)
        self.cpm.blockSignals(False)
        self.btn_sound.blockSignals(True)
        self.btn_sound.setChecked(s.sound_enabled)
        self.btn_sound.setText("🔊" if s.sound_enabled else "🔇")
        self.btn_sound.blockSignals(False)
        self.player.enabled = s.sound_enabled
        self.player.set_style(s.sound_style)
        self.player.set_volume(s.sound_volume)
        self.hotkeys.set_bindings({k: getattr(s, k) for k, _ in HOTKEYS})
        self.guard.enabled = s.guard_enabled
        self.guard.set_hotkeys({hk for k, _ in HOTKEYS if (hk := parse_hotkey(getattr(s, k)))})
        hk = s.hotkey_toggle or "—"
        self.hint_lbl.setText(f"{hk} — старт/пауза · {s.hotkey_restart or '—'} — сначала · "
                              f"{s.hotkey_stop or '—'} — стоп")
        on_top = bool(self.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        if on_top != s.always_on_top:
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, s.always_on_top)
            self.show()
        self.engine.rebuild()
        self._on_state(self.engine.state)

    def _save_settings(self) -> None:
        self.settings.save()

    def _on_profile(self) -> None:
        self.settings.profile = self.profile.currentData()
        self.engine.rebuild()
        self._invalidate_job_if_idle()
        self._save_settings()

    def _on_cpm(self, v: int) -> None:
        self.settings.cpm = v   # движок читает скорость на лету
        self._save_settings()

    def _on_sound_toggle(self, on: bool) -> None:
        self.settings.sound_enabled = on
        self.player.enabled = on
        self.btn_sound.setText("🔊" if on else "🔇")
        self._save_settings()

    def open_settings(self) -> None:
        self.hotkeys.set_bindings({})  # иначе QKeySequenceEdit не увидит уже занятые сочетания
        dlg = SettingsDialog(self.settings, self)
        dlg.test_sound.connect(self._test_sound)
        if dlg.exec() and dlg.result_settings:
            new = dlg.result_settings
            for k, v in asdict(new).items():
                setattr(self.settings, k, v)   # тот же объект — его читает движок
            self._invalidate_job_if_idle()
            self._save_settings()
        self.player.set_style(self.settings.sound_style)
        self.player.set_volume(self.settings.sound_volume)
        self._apply_settings()

    def _test_sound(self, style: str, volume: int) -> None:
        self.player.set_style(style)
        self.player.set_volume(volume)
        was = self.player.enabled
        self.player.enabled = True
        for i, kind in enumerate(["key", "key", "key", "space", "key", "key", "enter"]):
            QTimer.singleShot(i * 140, lambda k=kind: self.player.play(k))
        QTimer.singleShot(1100, lambda: setattr(self.player, "enabled", was))

    # ================================================================ библиотека и вкладки
    def _fill_library(self, select_id: str = "") -> None:
        self.library.blockSignals(True)
        self.library.clear()
        for t in self.store.templates:
            n_tasks = len(t.tasks)
            it = QListWidgetItem(t.title)
            it.setToolTip(f"{t.title}\nЗадач: {n_tasks}")
            it.setData(Qt.ItemDataRole.UserRole, t.id)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsEditable)
            self.library.addItem(it)
            if t.id == select_id:
                self.library.setCurrentItem(it)
        self.library.blockSignals(False)
        self._filter_library(self.search.text())

    def _filter_library(self, text: str) -> None:
        q = text.strip().lower()
        for i in range(self.library.count()):
            it = self.library.item(i)
            t = self.store.get(it.data(Qt.ItemDataRole.UserRole))
            hay = (t.title + " " + " ".join(task.title for task in t.tasks)).lower() if t else ""
            it.setHidden(bool(q) and q not in hay)

    def _library_menu(self, pos) -> None:
        it = self.library.itemAt(pos)
        if not it:
            return
        t = self.store.get(it.data(Qt.ItemDataRole.UserRole))
        m = QMenu(self)
        m.addAction("Открыть", lambda: self.open_template(t.id))
        m.addAction("Переименовать", lambda: self.library.editItem(it))
        m.addAction("Дублировать", lambda: self._duplicate(t))
        m.addSeparator()
        m.addAction("Экспорт в .ipynb…", lambda: self.export_template(t, "ipynb"))
        m.addAction("Экспорт в .json…", lambda: self.export_template(t, "json"))
        m.addSeparator()
        m.addAction("Удалить…", lambda: self._delete_template(t))
        m.exec(self.library.mapToGlobal(pos))

    def _on_library_renamed(self, it: QListWidgetItem) -> None:
        t = self.store.get(it.data(Qt.ItemDataRole.UserRole))
        title = it.text().strip()
        if t and title and title != t.title:
            t.title = title
            self._sync_tab_titles()
            self._touch()

    def _rename_tab(self, index: int) -> None:
        view = self.tabs.widget(index)
        if not isinstance(view, TemplateView):
            return
        title, ok = QInputDialog.getText(self, "Переименовать", "Название образца:", text=view.template.title)
        if ok and title.strip():
            view.template.title = title.strip()
            self._sync_tab_titles()
            self._fill_library(view.template.id)
            self._touch()

    def _sync_tab_titles(self) -> None:
        for i in range(self.tabs.count()):
            v = self.tabs.widget(i)
            if isinstance(v, TemplateView):
                self.tabs.setTabText(i, v.template.title)
                self.tabs.setTabToolTip(i, v.template.title)

    def view_for(self, tid: str) -> TemplateView | None:
        for i in range(self.tabs.count()):
            v = self.tabs.widget(i)
            if isinstance(v, TemplateView) and v.template.id == tid:
                return v
        return None

    def current_view(self) -> TemplateView | None:
        v = self.tabs.currentWidget()
        return v if isinstance(v, TemplateView) else None

    def open_template(self, tid: str, activate: bool = True) -> TemplateView | None:
        v = self.view_for(tid)
        if v is None:
            t = self.store.get(tid)
            if not t:
                return None
            v = TemplateView(t)
            v.changed.connect(self._touch)
            v.armed_changed.connect(lambda _bid, v=v: self._on_armed(v))
            self.tabs.addTab(v, t.title)
            self._sync_tab_titles()
        if activate:
            self.tabs.setCurrentWidget(v)
        return v

    def close_tab(self, index: int) -> None:
        v = self.tabs.widget(index)
        if v is None:
            return
        if isinstance(v, TemplateView) and self.job and self.job["tid"] == v.template.id \
                and self.engine.state in (RUNNING, COUNTDOWN, PAUSED):
            self.cmd_stop()
        self.tabs.removeTab(index)
        v.deleteLater()
        self._update_armed_label()

    def _goto_tab(self, i: int) -> None:
        if 0 <= i < self.tabs.count():
            self.tabs.setCurrentIndex(i)

    def _on_tab_changed(self, _i: int) -> None:
        v = self.current_view()
        if v:
            self.library.blockSignals(True)
            for i in range(self.library.count()):
                if self.library.item(i).data(Qt.ItemDataRole.UserRole) == v.template.id:
                    self.library.setCurrentRow(i)
            self.library.blockSignals(False)
        self._update_armed_label()

    def new_template(self) -> None:
        title, ok = QInputDialog.getText(self, "Новый образец", "Название (например, «Занятие 3. Циклы»):")
        if not ok:
            return
        t = self.store.add(title.strip() or "Новый образец")
        self._touch()
        self._fill_library(t.id)
        self.open_template(t.id)

    def _duplicate(self, t: Template) -> None:
        c = self.store.duplicate(t)
        self._touch()
        self._fill_library(c.id)
        self.open_template(c.id)

    def _delete_template(self, t: Template) -> None:
        if QMessageBox.question(self, "Удалить образец",
                                f"Удалить образец «{t.title}» со всеми задачами?\n"
                                "(Резервная копия прошлой версии базы — data/templates.json.bak)") \
                != QMessageBox.StandardButton.Yes:
            return
        v = self.view_for(t.id)
        if v:
            self.close_tab(self.tabs.indexOf(v))
        self.store.remove(t)
        self._touch()
        self._fill_library()

    def import_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Импорт образцов", "",
            "Тетрадки и образцы (*.ipynb *.md *.json);;Jupyter (*.ipynb);;Markdown (*.md);;JSON (*.json)")
        last = None
        for p in paths:
            ext = Path(p).suffix.lower()
            try:
                if ext == ".ipynb":
                    t = import_ipynb(p)
                elif ext == ".md":
                    t = import_markdown(p)
                else:
                    t = TemplateStore.read_template_file(p)
            except Exception as e:
                QMessageBox.warning(self, "Импорт", f"{Path(p).name}: {e}")
                continue
            if not t.tasks:
                QMessageBox.information(self, "Импорт", f"{Path(p).name}: нет ячеек для импорта.")
                continue
            self.store.insert(t)
            last = t
            self._notify(f"Импортировано «{t.title}»: задач — {len(t.tasks)}, "
                         f"блоков кода — {len(t.all_code_blocks())}.")
        if last:
            self._touch()
            self._fill_library(last.id)
            self.open_template(last.id)

    def export_current(self, fmt: str) -> None:
        v = self.current_view()
        if v:
            self.export_template(v.template, fmt)

    def export_template(self, t: Template, fmt: str) -> None:
        safe = "".join(c for c in t.title if c not in '\\/:*?"<>|').strip() or "template"
        flt = "Jupyter (*.ipynb)" if fmt == "ipynb" else "JSON (*.json)"
        path, _ = QFileDialog.getSaveFileName(self, "Экспорт", f"{safe}.{fmt}", flt)
        if not path:
            return
        try:
            if fmt == "ipynb":
                export_ipynb(t, path)
            else:
                TemplateStore.export_template(t, path)
            self._notify(f"Сохранено: {path}")
        except OSError as e:
            QMessageBox.warning(self, "Экспорт", str(e))

    def _open_log(self) -> None:
        import os
        from ..logs import LOG_FILE
        if LOG_FILE.exists():
            os.startfile(LOG_FILE)
        else:
            self._notify("Журнал пока пуст.")

    def _open_data_dir(self) -> None:
        import os
        from ..storage import DATA_DIR
        os.startfile(DATA_DIR)

    # ================================================================ сохранение
    def _touch(self) -> None:
        v = self.current_view()
        if v:
            v.template.updated = time.time()
        self._update_armed_label()
        self._save_timer.start()

    def _save_store(self) -> None:
        try:
            self.store.save()
        except OSError as e:
            self._notify(f"Не удалось сохранить образцы: {e}", True)

    def _restore_session(self) -> None:
        s = self.settings
        if s.window_geometry:
            self.restoreGeometry(QByteArray.fromBase64(s.window_geometry.encode()))
        if s.splitter_state:
            self.split.restoreState(QByteArray.fromBase64(s.splitter_state.encode()))
        self._fill_library(s.current_tab)
        for tid in s.open_tabs:
            self.open_template(tid, activate=False)
        if self.tabs.count() == 0 and self.store.templates:
            self.open_template(self.store.templates[0].id)
        cur = self.view_for(s.current_tab)
        if cur:
            self.tabs.setCurrentWidget(cur)
        self._update_armed_label()

    def _save_session(self) -> None:
        s = self.settings
        s.window_geometry = bytes(self.saveGeometry().toBase64()).decode()
        s.splitter_state = bytes(self.split.saveState().toBase64()).decode()
        s.open_tabs = [self.tabs.widget(i).template.id for i in range(self.tabs.count())
                       if isinstance(self.tabs.widget(i), TemplateView)]
        v = self.current_view()
        s.current_tab = v.template.id if v else ""
        s.save()

    # ================================================================ печать
    def _armed(self):
        """Активный блок текущей вкладки → (view, task, block) или None."""
        v = self.current_view()
        if not v or not v.template.active_block:
            return None
        found = v.template.find_block(v.template.active_block)
        if not found:
            return None
        return v, found[0], found[1]

    def _on_armed(self, v: TemplateView) -> None:
        self._update_armed_label()

    def _update_armed_label(self) -> None:
        a = self._armed()
        if not a:
            self.armed_lbl.setText("<span style='color:gray'>Нет активного блока кода — щёлкните по блоку кода</span>")
            return
        v, task, block = a
        n = [b.id for b in task.code_blocks()].index(block.id) + 1
        text, _ = typing_slice(block.text, block.sel, self.settings.selection_whole_lines)
        part = "выделенные строки" if block.sel else "весь блок"
        lines = text.count("\n") + 1 if text else 0
        self.armed_lbl.setText(f"Печатать: <b>{task.title}</b> · {block.display_title(n)} · {part} "
                               f"({lines} стр., {len(text)} симв.)")

    def _job_for_armed(self) -> dict | None:
        a = self._armed()
        if not a:
            return None
        v, task, block = a
        text, base = typing_slice(block.text, block.sel, self.settings.selection_whole_lines)
        if not text.strip():
            return None
        return {"tid": v.template.id, "bid": block.id, "base": base, "text": text}

    def _same_job(self, job: dict | None) -> bool:
        return bool(job and self.job and all(job[k] == self.job[k] for k in ("tid", "bid", "base", "text")))

    def _load_job(self, job: dict) -> None:
        self._clear_typed()
        self.job = job
        self.engine.load(job["text"], job["bid"])

    def _invalidate_job_if_idle(self) -> None:
        if self.engine.state in (IDLE, FINISHED):
            self.job = None

    def _start_params(self, from_button: bool, countdown: int | None = None) -> tuple[float, int]:
        s = self.settings
        if from_button:
            if s.minimize_on_button_start:
                self.showMinimized()
            return 0.0, s.button_countdown_s if countdown is None else countdown
        return s.hotkey_start_delay_ms / 1000, countdown or 0

    def cmd_toggle(self, from_button: bool = False, countdown: int | None = None) -> None:
        st = self.engine.state
        if st in (RUNNING, COUNTDOWN):
            self.engine.pause()
            return
        job = self._job_for_armed()
        if job is None:
            self._notify("Нечего печатать: выберите (щёлкните) непустой блок кода.", True)
            return
        if st == PAUSED and self._same_job(job):
            self.engine.resume(*self._start_params(from_button, countdown))
            return
        if not self._same_job(job) or st == FINISHED or st == PAUSED:
            if st == PAUSED:
                self._notify("Образец или выделение изменились — печать начнётся с начала.")
            self._load_job(job)
        self.engine.start(*self._start_params(from_button, countdown))

    def cmd_restart(self, from_button: bool = False) -> None:
        job = self._job_for_armed()
        if job is None:
            self._notify("Нечего печатать: выберите (щёлкните) непустой блок кода.", True)
            return
        self._load_job(job)
        self.engine.restart(*self._start_params(from_button))

    def cmd_stop(self) -> None:
        self.engine.stop()
        self._clear_typed()
        self.job = None

    def cmd_block(self, d: int) -> None:
        v = self.current_view()
        if not v:
            return
        blocks = v.template.all_code_blocks()
        if not blocks:
            return
        ids = [b.id for _, b in blocks]
        cur = v.template.active_block
        i = ids.index(cur) if cur in ids else -1
        j = max(0, min(len(ids) - 1, i + d))
        if self.engine.state in (RUNNING, COUNTDOWN, PAUSED):
            self.cmd_stop()
        v.go_to_block(ids[j])
        task, _ = v.template.find_block(ids[j])
        self._notify(f"Активный блок: {task.title} · код {[b.id for b in task.code_blocks()].index(ids[j]) + 1}"
                     f" ({j + 1} из {len(ids)})", tray=not self.isActiveWindow())

    def cmd_next_tab(self, d: int = 1) -> None:
        n = self.tabs.count()
        if n:
            if self.engine.state in (RUNNING, COUNTDOWN):
                self.engine.pause()
            self.tabs.setCurrentIndex((self.tabs.currentIndex() + d) % n)
            v = self.current_view()
            if v and not self.isActiveWindow():
                self._notify(f"Образец: {v.template.title}", tray=True)

    def _on_hotkey(self, action: str) -> None:
        if action == "hotkey_toggle":
            self.cmd_toggle()
        elif action == "hotkey_restart":
            self.cmd_restart()
        elif action == "hotkey_stop":
            self.cmd_stop()
        elif action == "hotkey_next_block":
            self.cmd_block(+1)
        elif action == "hotkey_prev_block":
            self.cmd_block(-1)
        elif action == "hotkey_next_tab":
            self.cmd_next_tab(+1)

    # ---- сигналы движка
    def _on_guard(self, kind: str) -> None:
        if self.engine.state == RUNNING:
            self.engine.pause("нажата клавиша" if kind == "key" else "клик мышью")
            self._notify("Пауза: вы нажали клавишу" if kind == "key" else "Пауза: вы кликнули мышью", tray=True)

    def _on_state(self, st: str) -> None:
        if st == RUNNING and self.settings.guard_enabled:
            self.guard.arm()
        else:
            self.guard.disarm()
        color = STATE_COLORS.get(st, "#888")
        self.state_lbl.setText(f"<b style='color:{color}'>● {STATE_TEXT.get(st, st)}</b>")
        self.btn_toggle.setText({RUNNING: "⏸ Пауза", COUNTDOWN: "⏸ Пауза",
                                 PAUSED: "▶ Продолжить"}.get(st, "▶ Старт"))
        icon = self.icons.get(st, self.icons[IDLE])
        self.tray.setIcon(icon)
        self.setWindowIcon(icon)
        self.tray.setToolTip(f"{APP_NAME}: {STATE_TEXT.get(st, st)}")
        self.setWindowTitle(f"{APP_NAME} {__version__} — {STATE_TEXT.get(st, st)}")

    def _on_progress(self, pos: int, total: int) -> None:
        self.progress.setMaximum(max(1, total))
        self.progress.setValue(pos)
        if self.job:
            v = self.view_for(self.job["tid"])
            if v:
                base = self.job["base"]
                v.set_typed(self.job["bid"], base, base + pos)

    def _clear_typed(self) -> None:
        if self.job:
            v = self.view_for(self.job["tid"])
            if v:
                v.set_typed("", 0, 0)
        self.progress.setValue(0)

    def _on_countdown(self, n: int) -> None:
        if n:
            self.statusBar().showMessage(f"Старт через {n}… Поставьте курсор в нужное окно.", 1500)
            self.tray.setToolTip(f"{APP_NAME}: старт через {n}")

    def _on_finished(self) -> None:
        self.statusBar().showMessage("Набор завершён.", 4000)
        if self.settings.auto_advance:
            v = self.view_for(self.job["tid"]) if self.job else None
            if v and v is self.current_view():
                ids = [b.id for _, b in v.template.all_code_blocks()]
                if self.job["bid"] in ids and ids.index(self.job["bid"]) + 1 < len(ids):
                    QTimer.singleShot(400, lambda: self.cmd_block(+1))

    def _notify(self, text: str, tray: bool = False) -> None:
        self.statusBar().showMessage(text, 7000)
        if tray or not self.isActiveWindow():
            self.tray.showMessage(APP_NAME, text, self.icons[PAUSED], 3000)

    # ================================================================ прочее
    def _show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def show_help(self) -> None:
        s = self.settings
        QMessageBox.information(self, "Как пользоваться", f"""\
<h3>Быстрый старт</h3>
<ol>
<li>Слева выберите образец (или <b>Импорт</b> из .ipynb). Образец содержит задачи, задача — условие и блоки кода.</li>
<li>Щёлкните по блоку кода — он станет <b>активным</b> (зелёная рамка).
Выделите строки, если нужно напечатать только их.</li>
<li>Перейдите в целевое окно (Блокнот, VS Code, браузер, Jupyter), поставьте курсор.</li>
<li>Нажмите <b>{s.hotkey_toggle}</b> — начнётся набор. Ещё раз — пауза, ещё раз — продолжение.</li>
</ol>
<p><b>{s.hotkey_restart or '—'}</b> — начать сначала · <b>{s.hotkey_stop or '—'}</b> — стоп ·
<b>{s.hotkey_next_block or '—'}</b> / <b>{s.hotkey_prev_block or '—'}</b> — следующий / предыдущий блок кода.</p>
<p>Профиль <b>IDE</b> убирает автоотступы и автозакрытые скобки редактора.
Для Блокнота выберите профиль <b>Блокнот</b>.</p>
<p>Если окно сменилось во время печати — набор встанет на паузу.</p>""")

    def closeEvent(self, e) -> None:
        self.engine.stop()
        self._save_timer.stop()
        self._save_store()
        self._save_session()
        self.hotkeys.shutdown()
        self.guard.shutdown()
        self.tray.hide()
        super().closeEvent(e)

    def quit_app(self) -> None:
        self.close()
        QApplication.quit()
