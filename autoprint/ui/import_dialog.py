"""«Импорт занятия»: из файла, вставкой или с помощью нейросети.

- «Из файла» — .ipynb, .md, .py, .json (перетащить или выбрать);
- «Вставить» — код, Markdown с ```-блоками, тетрадка Jupyter или ответ нейросети (JSON): что это — видно сразу;
- «С помощью нейросети» — код или задание с решением → нейросеть раскладывает его в занятие: с комментариями
  для преподавателя, по шагам или по частям. Подключённая нейросеть (⚙ → Нейросеть) отвечает сама; без неё —
  «Скопировать промпт» в свой чат (ChatGPT, Claude…) и вставить ответ обратно.

Результат — список занятий в self.result и стиль разбора в self.result_style (чтобы окно программы включило
подходящий режим печати).
"""
from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QPlainTextEdit,
                               QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget)

from .. import ai, lesson_ai as L
from ..highlighter import LANGUAGES
from ..importers import FILE_EXTS, FILE_FILTER, load_file, parse_pasted
from ..storage import Settings, Template
from .theme import theme
from .widgets import Card, Note, Row, Segmented, Switch, Toast, accent_button, button, group_title, hsep, label, \
    page_header

TAB_FILE, TAB_PASTE, TAB_AI = "file", "paste", "ai"

STYLE_HINTS = {
    L.STYLE_COMMENTS: "Код целиком, в нём комментарии — что сказать перед каждым куском. Печать «По строкам»: "
                      "комментарий — на суфлёре, Enter — следующая строка.",
    L.STYLE_STEPS: "Код с разметкой шагов: что за чем писать, с возвратом наверх (import) и вставкой в середину. "
                   "Печать «По шагам»: прочитали шаг на суфлёре — Ctrl+F9.",
    L.STYLE_PARTS: "Несколько блоков кода, между ними пояснения для учеников. Печать «Целиком»: блок за блоком "
                   "(Ctrl+F12 — следующий).",
}


class _Job(QObject):
    """Запрос к нейросети в фоновом потоке; сигналы приходят в поток окна."""
    status = Signal(str)
    done = Signal(str, str)      # ответ, кто ответил
    failed = Signal(str)


class ImportDialog(QDialog):
    def __init__(self, parent=None, settings: Settings | None = None, tab: str = TAB_FILE,
                 open_ai_settings=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Импорт занятия")
        self.setModal(False)   # из окна можно открыть ⚙ → Нейросеть и вернуться
        self.settings = settings or Settings.load()
        self.open_ai_settings = open_ai_settings      # «Подключить нейросеть…» — открыть ⚙ → Нейросеть
        self.result: list[Template] = []
        self.result_style = ""
        self._paste_parsed: Template | None = None
        self._ai_parsed: L.Parsed | None = None
        self._cancel: threading.Event | None = None
        self.setAcceptDrops(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 22, 28, 18)
        root.setSpacing(0)
        root.addWidget(page_header("Импорт занятия", "Из файла, вставкой из буфера обмена или с помощью нейросети: "
                                   "она разложит код в занятие с подсказками для преподавателя."))
        root.addSpacing(14)
        self.tabs = Segmented([(TAB_FILE, "Из файла"), (TAB_PASTE, "Вставить"), (TAB_AI, "С помощью нейросети")])
        self.tabs.changed.connect(self._show_tab)
        root.addWidget(self.tabs, 0, Qt.AlignmentFlag.AlignLeft)
        # разбор вставленного — с небольшой задержкой после последнего изменения (до страниц: им нужны таймеры)
        self._paste_timer = QTimer(self, singleShot=True, interval=250, timeout=self._parse_paste)
        self._answer_timer = QTimer(self, singleShot=True, interval=250, timeout=self._parse_answer)
        root.addSpacing(14)
        self.stack = QStackedWidget()
        self.pages = {TAB_FILE: self._page_file(), TAB_PASTE: self._page_paste(), TAB_AI: self._page_ai()}
        for p in self.pages.values():
            self.stack.addWidget(p)
        root.addWidget(self.stack, 1)

        root.addSpacing(12)
        root.addWidget(hsep())
        root.addSpacing(12)
        nav = QHBoxLayout()
        close = button("Закрыть", quiet=True)
        close.clicked.connect(self.reject)
        nav.addWidget(close)
        nav.addStretch(1)
        self.status = label("", "importStatus", wrap=True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        nav.addWidget(self.status, 3)
        self.add_btn = accent_button("Добавить занятие", "plus")
        self.add_btn.clicked.connect(self._add)
        nav.addWidget(self.add_btn)
        root.addLayout(nav)
        for b in self.findChildren(QPushButton):   # Enter в полях — новая строка, а не кнопка
            b.setAutoDefault(False)
            b.setDefault(False)

        self.toast = Toast(self.stack)
        self.paste_edit.textChanged.connect(self._paste_timer.start)
        self.answer_edit.textChanged.connect(self._answer_timer.start)
        self._restyle()
        theme.changed.connect(self._restyle)
        self._fit_screen()
        self.tabs.set_value(tab)
        self._show_tab(tab)

    # ------------------------------------------------------------ страницы
    def _page_file(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        drop = QFrame()
        drop.setObjectName("dropZone")
        dl = QVBoxLayout(drop)
        dl.setContentsMargins(24, 34, 24, 34)
        dl.setSpacing(10)
        t = label("Перетащите сюда файлы", "dropTitle")
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dl.addWidget(t)
        s = label(".ipynb (Jupyter) · .md (Markdown) · .py · .json (занятие или ответ нейросети)", "dropSub")
        s.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dl.addWidget(s)
        pick = button("Выбрать файлы…", "import")
        pick.clicked.connect(self._pick_files)
        dl.addWidget(pick, 0, Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(drop)
        lay.addWidget(Note("Тетрадка Jupyter — каждая ячейка становится блоком. Markdown — текст между ```-блоками "
                           "становится пояснениями, сами блоки — кодом. Python — один блок кода, а с разметкой "
                           "«# %%» — по ячейке на блок. Можно выбрать сразу несколько файлов.", "help"))
        lay.addStretch(1)
        return w

    def _page_paste(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.paste_edit = self._editor("Вставьте сюда (Ctrl+V): код, Markdown с ```-блоками, тетрадку Jupyter "
                                       "или ответ нейросети в формате JSON — программа сама поймёт, что это.")
        lay.addWidget(self.paste_edit, 1)
        row = QHBoxLayout()
        row.addWidget(label("Если это просто код, язык:", "importLabel"))
        self.lang_combo = QComboBox()
        self.lang_combo.addItem("определить сам", "")
        for lang in LANGUAGES:
            self.lang_combo.addItem(lang, lang)
        self.lang_combo.currentIndexChanged.connect(self._paste_timer.start)
        row.addWidget(self.lang_combo)
        row.addStretch(1)
        paste = button("Вставить из буфера", "copy")
        paste.clicked.connect(lambda: self.paste_edit.setPlainText(QGuiApplication.clipboard().text()))
        row.addWidget(paste)
        lay.addLayout(row)
        return w

    def _page_ai(self) -> QWidget:
        s = self.settings
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 0, 8, 0)
        lay.setSpacing(0)

        head = QHBoxLayout()
        head.addWidget(group_title("1. Что разобрать"))
        head.addStretch(1)
        from_file = button("Из файла…", "import", quiet=True)
        from_file.clicked.connect(self._material_from_file)
        head.addWidget(from_file)
        lay.addLayout(head)
        self.material = self._editor("Вставьте код — или задание вместе с решением. Нейросеть добавит комментарии "
                                     "для преподавателя и разложит код по шагам или частям.")
        self.material.setMinimumHeight(150)
        self.material.textChanged.connect(self._update_ai_buttons)
        lay.addWidget(self.material)

        lay.addWidget(group_title("2. Как разложить"))
        c = Card()
        self.style_seg = Segmented([(k, v) for k, v in L.STYLES.items()])
        self.style_seg.set_value(s.lesson_style if s.lesson_style in L.STYLES else L.STYLE_STEPS)
        self.style_hint = label("", "rowSub", wrap=True)
        self.style_seg.changed.connect(self._style_changed)
        c.add(Row("layers", "Разбор", "", [self.style_seg]))
        hint_row = QWidget()
        hl = QHBoxLayout(hint_row)
        hl.setContentsMargins(48, 0, 16, 12)
        hl.addWidget(self.style_hint)
        c.add(hint_row)
        self.sw_task = Switch(checked=s.lesson_task)
        c.add(Row("file-text", "Краткое условие задачи", "Блок «Условие» в начале; нет условия — нейросеть "
                  "сформулирует его по коду", [self.sw_task]))
        self.sw_explain = Switch(checked=s.lesson_explain)
        c.add(Row("info", "Пояснения для учеников", "Текстовые блоки: что будем делать (в разборе «По частям» — "
                  "всегда)", [self.sw_explain]))
        self.sw_keep = Switch(checked=s.lesson_keep_code)
        c.add(Row("code", "Не менять код — только комментарии и разметка",
                  "Выключите, если код можно поправить для объяснения", [self.sw_keep]))
        self.detail_seg = Segmented([(L.DETAIL_SHORT, "Коротко"), (L.DETAIL_FULL, "Подробно")])
        self.detail_seg.set_value(s.lesson_detail if s.lesson_detail in (L.DETAIL_SHORT, L.DETAIL_FULL)
                                  else L.DETAIL_SHORT)
        c.add(Row("pencil", "Комментарии", "Коротко — одна строка; подробно — с «зачем»", [self.detail_seg]))
        lay.addWidget(c)

        lay.addWidget(group_title("3. Нейросеть"))
        c2 = Card()
        self.ai_info = label("", "rowSub", wrap=True)
        self.ai_info.setTextFormat(Qt.TextFormat.RichText)
        self.ai_info.linkActivated.connect(lambda _: self._connect_ai())
        self.ask_btn = accent_button("Разобрать нейросетью", "sparkles")
        self.ask_btn.clicked.connect(self._ask)
        self.cancel_btn = button("Отмена", quiet=True)
        self.cancel_btn.clicked.connect(self._cancel_job)
        self.cancel_btn.hide()
        self.copy_btn = button("Скопировать промпт", "copy")
        self.copy_btn.setToolTip("Промпт с вашим кодом — в буфер обмена: вставьте его в свой чат (ChatGPT, Claude…), "
                                 "а ответ — в поле ниже")
        self.copy_btn.clicked.connect(self._copy_prompt)
        c2.add(Row("cpu", "Разобрать", "", [self.copy_btn, self.cancel_btn, self.ask_btn]))
        info_row = QWidget()
        il = QHBoxLayout(info_row)
        il.setContentsMargins(48, 0, 16, 12)
        il.addWidget(self.ai_info)
        c2.add(info_row)
        lay.addWidget(c2)

        lay.addWidget(group_title("4. Ответ нейросети"))
        self.answer_edit = self._editor("Сюда придёт ответ подключённой нейросети. Или вставьте ответ своего чата "
                                        "(Ctrl+V) — JSON из промпта.")
        self.answer_edit.setMinimumHeight(130)
        lay.addWidget(self.answer_edit)
        self.answer_info = label("", "answerInfo", wrap=True)
        self.answer_info.setTextFormat(Qt.TextFormat.PlainText)
        self.answer_info.setContentsMargins(2, 6, 0, 0)
        lay.addWidget(self.answer_info)
        lay.addStretch(1)

        sc = QScrollArea()
        self.ai_scroll = sc
        sc.setObjectName("importScroll")
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.Shape.NoFrame)
        sc.setWidget(inner)
        self._style_changed(self.style_seg.value())
        return sc

    def _editor(self, placeholder: str) -> QPlainTextEdit:
        e = QPlainTextEdit()
        e.setObjectName("importText")
        e.setPlaceholderText(placeholder)
        e.setTabChangesFocus(True)
        e.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        e.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        return e

    # ------------------------------------------------------------ оформление
    def _restyle(self) -> None:
        t = theme
        self.setStyleSheet(f"""
QPlainTextEdit#importText {{ background: {t.css('editor_bg')}; border: 1px solid {t.css('stroke_strong')};
    border-radius: 5px; padding: 6px 8px; font-family: "{t.mono_family}"; font-size: 12px;
    selection-background-color: {t.css('editor_sel')}; selection-color: {t.css('text')}; }}
QPlainTextEdit#importText:focus {{ border-color: {t.css('accent')}; }}
QFrame#dropZone {{ border: 2px dashed {t.css('stroke_strong')}; border-radius: 10px; background: {t.css('panel')}; }}
QFrame#dropZone[hot="true"] {{ border-color: {t.css('accent')}; }}
QLabel#dropTitle {{ font-size: 16px; font-weight: 600; }}
QLabel#dropSub, QLabel#importLabel {{ color: {t.css('dim')}; }}
QLabel#importStatus {{ color: {t.css('dim')}; }}
QLabel#answerInfo {{ color: {t.css('dim')}; }}
QScrollArea#importScroll, QScrollArea#importScroll > QWidget > QWidget {{ background: transparent; }}
""")

    def _fit_screen(self) -> None:
        scr = (self.parentWidget().screen() if self.parentWidget() else None) or QGuiApplication.primaryScreen()
        avail = scr.availableGeometry() if scr else None
        w, h = 860, 860
        if avail is not None:
            w, h = min(w, avail.width() - 40), min(h, avail.height() - 60)
        self.setMinimumSize(min(640, w), min(560, h))
        self.resize(w, h)

    # ------------------------------------------------------------ вкладки и состояние
    def _show_tab(self, key) -> None:
        self.stack.setCurrentWidget(self.pages[key])
        self.add_btn.setVisible(key != TAB_FILE)
        if key == TAB_AI:
            self._update_ai_buttons()
            self._parse_answer()
        elif key == TAB_PASTE:
            self._parse_paste()
            self.paste_edit.setFocus()
        else:
            self.status.setText("")

    def _style_changed(self, key) -> None:
        self.style_hint.setText(STYLE_HINTS.get(key, ""))
        self.sw_explain.setEnabled(key != L.STYLE_PARTS)

    def _options(self) -> L.Options:
        return L.Options(style=self.style_seg.value() or L.STYLE_STEPS, task=self.sw_task.isChecked(),
                         explain=self.sw_explain.isChecked(), keep_code=self.sw_keep.isChecked(),
                         detail=self.detail_seg.value() or L.DETAIL_SHORT)

    def _remember_options(self) -> None:
        o = self._options()
        s = self.settings
        s.lesson_style, s.lesson_task, s.lesson_explain = o.style, o.task, o.explain
        s.lesson_keep_code, s.lesson_detail = o.keep_code, o.detail

    def _update_ai_buttons(self) -> None:
        has = bool(self.material.toPlainText().strip())
        busy = self._cancel is not None
        ok = ai.configured(self.settings)
        self.copy_btn.setEnabled(has and not busy)
        self.ask_btn.setEnabled(has and ok and not busy)
        self.ask_btn.setVisible(not busy)
        self.cancel_btn.setVisible(busy)
        if busy:
            return
        c = ai.config(self.settings)
        if ok:
            self.ai_info.setText(f"Ответит: <b>{c.provider.name}</b>{(' · ' + c.model) if c.model else ''}. "
                                 "<a href='ai'>Сменить…</a>")
        elif c is not None:
            self.ai_info.setText(f"{c.provider.name}: {ai.problem(c) or 'не настроено'} "
                                 "<a href='ai'>Настроить…</a> Или «Скопировать промпт» — в свой чат.")
        else:
            self.ai_info.setText("Нейросеть не подключена. <a href='ai'>Подключить…</a> — или «Скопировать "
                                 "промпт»: вставьте его в свой чат (ChatGPT, Claude…), а ответ — в поле ниже.")

    def _connect_ai(self) -> None:
        if self.open_ai_settings:
            self.open_ai_settings()
            QTimer.singleShot(400, self._update_ai_buttons)

    # ------------------------------------------------------------ из файла
    def _pick_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Импорт занятий", "", FILE_FILTER)
        self._take_files(paths)

    def _take_files(self, paths: list[str]) -> None:
        done, errors = [], []
        for p in paths:
            try:
                t = load_file(p)
            except Exception as e:   # noqa: BLE001 — показать причину по каждому файлу
                errors.append(f"{Path(p).name}: {e}")
                continue
            if not t.blocks:
                errors.append(f"{Path(p).name}: нет блоков для импорта")
                continue
            done.append(t)
        if errors:
            self.toast.show_message("Не импортировано — " + "; ".join(errors)[:400], warn=True, ms=9000)
        if done:
            self.result = done
            self.accept()

    def dragEnterEvent(self, e) -> None:
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._hot(True)

    def dragLeaveEvent(self, e) -> None:
        self._hot(False)

    def dropEvent(self, e) -> None:
        self._hot(False)
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        files = [p for p in paths if Path(p).suffix.lower() in FILE_EXTS]
        if not files:
            self.toast.show_message("Подходят файлы .ipynb, .md, .py и .json.", warn=True)
            return
        self._take_files(files)

    def _hot(self, on: bool) -> None:
        z = self.pages[TAB_FILE].findChild(QFrame, "dropZone")
        if z is not None:
            z.setProperty("hot", on)
            z.style().unpolish(z)
            z.style().polish(z)

    # ------------------------------------------------------------ вставить
    def _parse_paste(self) -> None:
        if self.stack.currentWidget() is not self.pages[TAB_PASTE]:
            return
        text = self.paste_edit.toPlainText()
        self._paste_parsed = None
        if not text.strip():
            self.status.setText("Вставьте текст — здесь будет видно, что получится.")
            self.add_btn.setEnabled(False)
            return
        try:
            t, kind = parse_pasted(text, self.lang_combo.currentData() or "")
        except (ValueError, L.LessonError) as e:
            self.status.setText(str(e))
            self.add_btn.setEnabled(False)
            return
        self._paste_parsed = t
        codes = t.code_blocks()
        self.status.setText(f"Распознано: {kind} · блоков: {len(t.blocks)}, из них кода: {len(codes)}")
        self.add_btn.setEnabled(True)

    # ------------------------------------------------------------ нейросеть
    def _material_from_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Код или задание", "", "Текст и код (*.*)")
        if path:
            try:
                self.material.setPlainText(Path(path).read_text(encoding="utf-8-sig"))
            except (OSError, UnicodeDecodeError) as e:
                self.toast.show_message(f"Не прочитать файл: {e}", warn=True)

    def _copy_prompt(self) -> None:
        self._remember_options()
        QGuiApplication.clipboard().setText(L.build_prompt(self.material.toPlainText(), self._options()))
        self.toast.show_message("Промпт скопирован: вставьте его в чат нейросети (Ctrl+V), а её ответ — в поле "
                                "«Ответ нейросети».", ms=7000)
        self._show_answer()
        self.answer_edit.setFocus()

    def _ask(self) -> None:
        self._remember_options()
        opt = self._options()
        material = self.material.toPlainText()
        self._cancel = threading.Event()
        cancel = self._cancel
        job = _Job(self)
        job.status.connect(self._on_status)
        job.done.connect(self._on_answer)
        job.failed.connect(self._on_failed)
        self._job = job
        self._update_ai_buttons()
        self._on_status("Отправляю…")
        self._show_answer()

        def work() -> None:
            try:
                text, who = ai.ask(self.settings, L.instruction(opt), L.material_message(material), cancel,
                                   on_status=job.status.emit)
                if not cancel.is_set():
                    job.done.emit(text, who)
            except ai.Cancelled:
                pass
            except ai.AIError as e:
                if not cancel.is_set():
                    job.failed.emit(str(e))
            except Exception as e:   # noqa: BLE001 — что угодно в фоне не должно ронять окно
                if not cancel.is_set():
                    job.failed.emit(f"Ошибка: {e}")
        threading.Thread(target=work, name="ai-lesson", daemon=True).start()

    def _cancel_job(self) -> None:
        if self._cancel is not None:
            self._cancel.set()
        self._cancel = None
        self._update_ai_buttons()
        self.answer_info.setText("Отменено.")

    def _on_status(self, text: str) -> None:
        self.answer_info.setText(text + " Это может занять до пары минут.")

    def _on_answer(self, text: str, who: str) -> None:
        self._cancel = None
        self._update_ai_buttons()
        self.answer_edit.setPlainText(text)
        self._parse_answer(who)
        self._show_answer()

    def _on_failed(self, msg: str) -> None:
        self._cancel = None
        self._update_ai_buttons()
        self.answer_info.setText(f"Нейросеть не ответила: {msg}")
        self.toast.show_message("Нейросеть не ответила — можно «Скопировать промпт» и спросить свой чат.", warn=True)

    def _show_answer(self) -> None:
        """Прокрутить к ответу нейросети: там видно, что получится, и замечания."""
        QTimer.singleShot(0, lambda: self.ai_scroll.ensureWidgetVisible(self.answer_info, 0, 40))

    def _parse_answer(self, who: str = "") -> None:
        if self.stack.currentWidget() is not self.pages[TAB_AI]:
            return
        text = self.answer_edit.toPlainText()
        self._ai_parsed = None
        if not text.strip():
            self.status.setText("Когда придёт ответ нейросети, здесь будет видно, что получится.")
            self.add_btn.setEnabled(False)
            if self._cancel is None and not self.answer_info.text().startswith(("Нейросеть не ответила", "Отменено")):
                self.answer_info.setText("")
            return
        try:
            parsed = L.parse_lesson(text)
        except L.LessonError as e:
            self.status.setText("Ответ не разобрать.")
            self.answer_info.setText(str(e))
            self.add_btn.setEnabled(False)
            return
        self._ai_parsed = parsed
        notes = list(parsed.warnings)
        if self.sw_keep.isChecked():
            diff = L.code_changes(self.material.toPlainText(), parsed)
            if diff:
                notes.insert(0, "Внимание: нейросеть изменила код (без учёта комментариев):\n  "
                             + "\n  ".join(diff[:8]))
        head = f"«{parsed.template.title}» — {parsed.summary}" + (f" · ответил {who}" if who else "")
        self.answer_info.setText(head + ("\n" + "\n".join(notes) if notes else ""))
        self.status.setText("Готово — можно добавить занятие." if not notes else "Готово, но проверьте замечания.")
        self.add_btn.setEnabled(True)

    # ------------------------------------------------------------ итог
    def _add(self) -> None:
        key = self.tabs.value()
        if key == TAB_PASTE and self._paste_parsed is not None:
            self.result = [self._paste_parsed]
            t = self._paste_parsed
            self.result_style = L.STYLE_STEPS if any(len(set(b.steps)) > 1 for b in t.code_blocks()) else ""
        elif key == TAB_AI and self._ai_parsed is not None:
            self.result = [self._ai_parsed.template]
            self.result_style = self._options().style
            self._remember_options()
        else:
            return
        self.accept()

    def reject(self) -> None:
        if self._cancel is not None:
            self._cancel.set()
        super().reject()

    def changeEvent(self, e) -> None:
        super().changeEvent(e)
        # вернулись из ⚙ → Нейросеть — показать, какая нейросеть теперь подключена
        if e.type() == e.Type.ActivationChange and self.isActiveWindow() and hasattr(self, "ask_btn"):
            self._update_ai_buttons()
