"""⚙ Настройки → Нейросеть: какая нейросеть разбирает код в занятие (как «Улучшение текста» в PasteTalk).

Основной провайдер и запасной — каждый со своими адресом, ключом и моделью (они хранятся по провайдеру:
переключились туда-обратно — ничего не стёрлось). «Проверить» — короткий запрос, видно время и ответ.
"""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLineEdit, QWidget

from .. import ai
from .widgets import Card, Note, Pill, Row, Switch, button, group_title, label, spin

NONE = ""


class _Bg(QObject):
    """Результат фоновой работы — в поток окна."""
    models = Signal(list, str)    # список, ошибка
    checked = Signal(bool, str)   # удалось, текст


class ProviderBox:
    """Строки одного провайдера в карточке: выбор, адрес, ключ, модель, проверка. which — ai_provider | ai_backup_provider."""

    def __init__(self, win, card: Card, which: str, allow_none: bool) -> None:
        self.win, self.s, self.which = win, win.s, which
        self.combo = QComboBox()
        if allow_none:
            self.combo.addItem("Не подключена — только «Скопировать промпт»", NONE)
        for p in ai.PROVIDERS:
            self.combo.addItem(p.name, p.id)
        self.combo.setMinimumWidth(300)
        self.hint = label("", "rowSub", wrap=True)
        self.row_provider = card.add(Row("sparkles" if which == "ai_provider" else "refresh",
                                         "Нейросеть" if which == "ai_provider" else "Запасная", "",
                                         [self.combo]))
        hint_row = QWidget()
        hl = QHBoxLayout(hint_row)
        hl.setContentsMargins(48, 0, 16, 12)
        hl.addWidget(self.hint)
        self.row_hint = card.add(hint_row)
        self.shown = True
        self.url = QLineEdit()
        self.url.setMinimumWidth(300)
        self.row_url = card.add(Row("external", "Адрес сервера", "OpenAI-совместимый: …/v1", [self.url]))
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setMinimumWidth(300)
        self.key.setPlaceholderText("ключ доступа")
        self.row_key = card.add(Row("lock", "Ключ доступа", "Хранится только на этом компьютере, зашифрован Windows; "
                                    "в отчёт об ошибке не попадает", [self.key]))
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.setMinimumWidth(220)
        self.refresh_btn = button("Обновить", "refresh", quiet=True)
        self.refresh_btn.setToolTip("Спросить у сервера список моделей")
        self.refresh_btn.clicked.connect(self._load_models)
        self.row_model = card.add(Row("cpu", "Модель", "", [self.model, self.refresh_btn]))
        self.pill = Pill("")
        self.pill.hide()
        self.check_btn = button("Проверить", "play")
        self.check_btn.clicked.connect(self._check)
        self.check_info = label("", "rowSub", wrap=True)
        self.row_check = card.add(Row("check", "Проверка связи", "Короткий запрос — видно, отвечает ли и как быстро",
                                      [self.pill, self.check_btn]))
        info_row = QWidget()
        il = QHBoxLayout(info_row)
        il.setContentsMargins(48, 0, 16, 12)
        il.addWidget(self.check_info)
        self.row_check_info = card.add(info_row)
        self.bg = _Bg()
        self.bg.models.connect(self._models_loaded)
        self.bg.checked.connect(self._checked)
        self._loading = False
        self.combo.currentIndexChanged.connect(self._provider_changed)
        self.url.editingFinished.connect(self._save_url)
        self.key.editingFinished.connect(self._save_key)
        self.model.currentTextChanged.connect(self._save_model)
        self.load()

    # ------------------------------------------------------------ значения
    def pid(self) -> str:
        return self.combo.currentData() or NONE

    def load(self) -> None:
        self._loading = True
        cur = getattr(self.s, self.which)
        i = self.combo.findData(cur)
        self.combo.setCurrentIndex(max(0, i))
        self._fill()
        self._loading = False

    def set_shown(self, on: bool) -> None:
        """Запасная выключена — её строки не показываются."""
        self.shown = on
        self._fill()

    def _fill(self) -> None:
        p = ai.provider(self.pid())
        self.row_provider.setVisible(self.shown)
        self.row_hint.setVisible(self.shown)
        if not self.shown:
            for row in (self.row_url, self.row_key, self.row_model, self.row_check, self.row_check_info):
                row.setVisible(False)
            return
        on = p is not None
        self.hint.setText(p.hint if p else "Без подключения можно разбирать код через свой чат: в окне импорта "
                          "«Скопировать промпт», вставить его в ChatGPT или Claude, а ответ — обратно.")
        for row in (self.row_url, self.row_key):
            row.setVisible(on and p.kind == "openai")
        self.row_key.setVisible(on and p.kind == "openai")
        self.row_model.setVisible(on)
        self.row_check.setVisible(on)
        self.row_check_info.setVisible(on and bool(self.check_info.text()))
        self.pill.hide()
        if not on:
            return
        c = ai.config(self.s, p.id)
        was = self._loading
        self._loading = True
        self.url.setText(c.url)
        self.url.setPlaceholderText(p.url or "https://…/v1")
        self.key.setText(c.key)
        self.key.setPlaceholderText("ключ доступа" + ("" if p.needs_key else " (если сервер просит)"))
        self.model.clear()
        models = list(p.models)
        if c.model and c.model not in models:
            models.insert(0, c.model)
        self.model.addItems(models)
        self.model.setCurrentText(c.model)
        self.refresh_btn.setVisible(p.kind == "openai")
        self._loading = was
        why = ai.problem(c)
        self.check_info.setText(why or "")
        self.row_check_info.setVisible(bool(why))

    def _provider_changed(self) -> None:
        self.check_info.setText("")
        self._fill()
        if not self._loading:
            self.win._set(self.which, self.pid())

    def _save_url(self) -> None:
        p = self.pid()
        if p and not self._loading:
            self.s.ai_urls[p] = self.url.text().strip()
            self.win.changed.emit("ai")
            self._fill_problem()

    def _save_key(self) -> None:
        p = self.pid()
        if p and not self._loading:
            self.s.ai_keys[p] = ai.protect(self.key.text().strip())
            self.win.changed.emit("ai")
            self._fill_problem()

    def _save_model(self, text: str) -> None:
        p = self.pid()
        if p and not self._loading:
            self.s.ai_models[p] = text.strip()
            self.win.changed.emit("ai")

    def _fill_problem(self) -> None:
        c = ai.config(self.s, self.pid())
        why = ai.problem(c) if c else None
        self.check_info.setText(why or "")
        self.row_check_info.setVisible(bool(why))

    # ------------------------------------------------------------ фоном: модели и проверка
    def _load_models(self) -> None:
        self._save_url()
        self._save_key()
        c = ai.config(self.s, self.pid())
        if c is None:
            return
        self.refresh_btn.setEnabled(False)
        self.refresh_btn.setText("Загружаю…")

        def work() -> None:
            try:
                self.bg.models.emit(ai.list_models(c), "")
            except ai.AIError as e:
                self.bg.models.emit([], str(e))
        threading.Thread(target=work, name="ai-models", daemon=True).start()

    def _models_loaded(self, models: list, err: str) -> None:
        self.refresh_btn.setEnabled(True)
        self.refresh_btn.setText(" Обновить")
        if err:
            self.check_info.setText(f"Список моделей не получен: {err}")
            self.row_check_info.setVisible(True)
            return
        cur = self.model.currentText()
        self._loading = True
        self.model.clear()
        self.model.addItems(models)
        self.model.setCurrentText(cur or (models[0] if models else ""))   # выбранная — остаётся
        self._loading = False
        self._save_model(self.model.currentText())
        self.check_info.setText(f"Моделей на сервере: {len(models)}.")
        self.row_check_info.setVisible(True)

    def _check(self) -> None:
        self._save_url()
        self._save_key()
        self._save_model(self.model.currentText())
        c = ai.config(self.s, self.pid())
        if c is None:
            return
        self.check_btn.setEnabled(False)
        self.pill.setText("проверяю…")
        self.pill.set_tone("")
        self.pill.show()

        def work() -> None:
            try:
                ms, sample = ai.check(c)
                self.bg.checked.emit(True, f"{ms} мс|{c.provider.name} ответил: «{sample}»")
            except ai.AIError as e:
                self.bg.checked.emit(False, str(e))
        threading.Thread(target=work, name="ai-check", daemon=True).start()

    def _checked(self, ok: bool, text: str) -> None:
        self.check_btn.setEnabled(True)
        if ok:
            ms, info = text.split("|", 1)
            self.pill.setText(ms)
            self.pill.set_tone("ok")
            self.check_info.setText(info)
        else:
            self.pill.setText("не вышло")
            self.pill.set_tone("warn")
            self.check_info.setText(text)
        self.row_check_info.setVisible(True)


def build_page(win) -> QWidget:
    """Страница «Нейросеть» окна настроек (win — SettingsWindow)."""
    page, lay = win._page("Нейросеть", "Разбирает ваш код в занятие: комментарии-подсказки для преподавателя, "
                                       "шаги или части с пояснениями (Импорт → «С помощью нейросети»).")
    lay.addWidget(group_title("Откуда брать модель"))
    c = Card()
    win.ai_main = ProviderBox(win, c, "ai_provider", allow_none=True)
    lay.addWidget(c)

    lay.addWidget(group_title("Запасная"))
    c2 = Card()
    sw = Switch(checked=win.s.ai_backup_enabled)
    sw.toggled.connect(lambda on: (win._set("ai_backup_enabled", on), box.set_shown(on)))
    c2.add(Row("refresh", "Пробовать запасную", "Основная не ответила (сервер лёг, кончился лимит) — тот же запрос "
               "уйдёт запасной. Лучше другая, чем основная: если лёг сервер, второй ключ к нему не поможет.", [sw]))
    box = ProviderBox(win, c2, "ai_backup_provider", allow_none=False)
    box.set_shown(win.s.ai_backup_enabled)   # выключена — её строки не загромождают страницу
    win.ai_backup = box
    lay.addWidget(c2)

    lay.addWidget(group_title("Ожидание"))
    c3 = Card()
    wait = spin(0, 900, win.s.ai_timeout_s, " с", step=30)
    wait.setSpecialValueText("по длине кода")
    wait.valueChanged.connect(lambda v: win._set("ai_timeout_s", v))
    c3.add(Row("clock", "Сколько ждать ответ", "«По длине кода» — от минуты до пяти; агентам по подписке — "
               "не меньше трёх минут", [wait]))
    lay.addWidget(c3)
    lay.addSpacing(12)
    lay.addWidget(Note("Код уходит на сервер выбранной нейросети. LM Studio и Ollama работают на этом компьютере — "
                       "с ними код никуда не уходит. Claude и ChatGPT по подписке — через установленные программы "
                       "Claude Code и Codex, ключ не нужен.", "lock"))

    def reload() -> None:
        win.ai_main.load()
        win.ai_backup.load()
        sw.setChecked(win.s.ai_backup_enabled)
        wait.setValue(win.s.ai_timeout_s)
    win._loaders.append(reload)
    win._finish(lay)
    return page
