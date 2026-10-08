"""Окно «Сообщить об ошибке»: описание от пользователя + отчёт (версии, система, журнал) без личных данных.

Отчёт собирает report.py. Пользователь видит в предпросмотре всё, что уйдёт автору, и может:
сохранить .zip (отчёт и журнал целиком), скопировать отчёт в буфер или открыть новое сообщение
на GitHub (краткий текст в ссылке, файл .zip прикладывается вручную).

    python -m autoprint.ui.report_dialog [dark|light]
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QByteArray, QStandardPaths, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (QApplication, QDialog, QFileDialog, QHBoxLayout, QPlainTextEdit, QPushButton,
                               QSizePolicy, QVBoxLayout, QWidget)

from .. import report
from ..logs import LOG_DIR
from ..storage import Settings
from . import icons
from .theme import theme
from .widgets import (Card, Note, Row, Switch, Toast, accent_button, button, group_title, hsep, label,
                      page_header)

# значок «жучок» (Lucide bug, ISC) — для кнопки в заголовке окна: IconButton("bug", …)
BUG_ICON = ('<path d="m8 2 1.88 1.88"/><path d="M14.12 3.88 16 2"/><path d="M9 7.13v-1a3.003 3.003 0 1 1 6 0v1"/>'
            '<path d="M12 20c-3.3 0-6-2.7-6-6v-3a4 4 0 0 1 4-4h4a4 4 0 0 1 4 4v3c0 3.3-2.7 6-6 6"/>'
            '<path d="M12 20v-9"/><path d="M6.53 9C4.6 8.8 3 7.1 3 5"/><path d="M6 13H2"/>'
            '<path d="M3 21c0-2.1 1.7-3.9 3.8-4"/><path d="M20.97 5c0 2.1-1.6 3.8-3.5 4"/><path d="M22 13h-4"/>'
            '<path d="M17.2 17c2.1.1 3.8 1.9 3.8 4"/>')
icons.PATHS.setdefault("bug", BUG_ICON)

PLACEHOLDER = ("Например: открыл образец из Jupyter, в VS Code нажал Ctrl+F9 — ожидал, что код напечатается "
               "с отступами, а строки съехали вправо. Повторяется каждый раз.")
HOW_TO_SEND = ("<b>Как отправить.</b> «Сохранить отчёт…» — получится файл .zip: отчёт и журнал целиком. "
               "«Открыть на GitHub» — в браузере откроется новое сообщение с кратким описанием: перетащите в него "
               "файл отчёта. Нет аккаунта на GitHub — пришлите файл автору любым удобным способом.")


def open_log_folder() -> None:
    """Открыть папку с журналом работы (data/logs) в Проводнике."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(LOG_DIR)))


def _desktop() -> Path:
    d = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DesktopLocation)
    return Path(d) if d else Path.home()


class ReportDialog(QDialog):
    """«Сообщить об ошибке». settings — текущие настройки программы (None — прочитать из settings.json)."""

    def __init__(self, parent=None, settings: Settings | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Сообщить об ошибке")
        self.setModal(True)
        self.settings = settings
        self.saved_path: Path | None = None
        self.report = report.collect(include_settings=True, settings=settings)

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 22, 28, 18)
        root.setSpacing(0)
        self.body = QWidget()
        lay = QVBoxLayout(self.body)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(page_header("Сообщить об ошибке", "Опишите, что произошло. К сообщению приложатся версия "
                                  "программы, сведения о системе и журнал работы — без текста образцов и того, "
                                  "что печатается."))
        lay.addSpacing(16)

        c = Card()
        self.desc = QPlainTextEdit()
        self.desc.setObjectName("reportText")
        self.desc.setPlaceholderText(PLACEHOLDER)
        self.desc.setTabChangesFocus(True)
        self.desc.setFixedHeight(88)
        self.desc.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        c.add(Row("pencil", "Что случилось?", "Что вы делали, чего ожидали и что произошло вместо этого",
                  [self.desc], stack=True))
        self.with_settings = Switch(checked=True)
        self.with_settings.toggled.connect(self._recollect)
        c.add(Row("sliders", "Приложить настройки (без образцов и личных данных)",
                  "Скорость, режимы, горячие клавиши, экраны и названия звуковых устройств", [self.with_settings]))
        crash = report.last_crash(self.report)
        if crash:
            when = self.report["crashes"][0]["time"]
            last = report.crash_summary(crash)
            if len(last) > 150:
                last = last[:149] + "…"
            c.add(Row("bug", "В журнале есть ошибка — она попадёт в отчёт", f"{when} · {last}"))
        log_btn = button("Папка с журналом", "folder")
        log_btn.clicked.connect(open_log_folder)
        c.add(Row("file-text", "Журнал работы", f"В отчёт — последние {report.LOG_TAIL_LINES} строк, "
                  "в файл .zip — журнал целиком", [log_btn]))
        lay.addWidget(c)

        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(group_title("Что будет в отчёте"))
        head.addStretch(1)
        hint = label("Папка пользователя → %USERPROFILE%, имя → <user>", "reportHint")
        hint.setTextFormat(Qt.TextFormat.PlainText)
        hint.setContentsMargins(0, 18, 0, 6)
        head.addWidget(hint)
        lay.addLayout(head)
        self.preview = QPlainTextEdit()
        self.preview.setObjectName("reportPreview")
        self.preview.setReadOnly(True)
        self.preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.preview.setMinimumHeight(120)
        lay.addWidget(self.preview, 1)
        lay.addSpacing(12)
        lay.addWidget(Note(HOW_TO_SEND, "help"))
        root.addWidget(self.body, 1)

        root.addSpacing(14)
        root.addWidget(hsep())
        root.addSpacing(14)
        nav = QHBoxLayout()
        nav.setSpacing(8)
        close = button("Закрыть", quiet=True)
        close.clicked.connect(self.reject)
        nav.addWidget(close)
        nav.addStretch(1)
        copy = button("Скопировать", "copy")
        copy.setToolTip("Отчёт целиком — в буфер обмена, для письма или сообщения")
        copy.clicked.connect(self.copy_report)
        nav.addWidget(copy)
        save = button("Сохранить отчёт…", "export")
        save.setToolTip("Файл .zip: отчёт и журнал работы целиком")
        save.clicked.connect(self.save_report)
        nav.addWidget(save)
        gh = accent_button("Открыть на GitHub", "external")
        gh.setToolTip("Новое сообщение об ошибке на GitHub; файл отчёта приложите к нему")
        gh.clicked.connect(self.open_github)
        nav.addWidget(gh)
        root.addLayout(nav)
        for b in self.findChildren(QPushButton):   # Enter в поле описания — новая строка, а не кнопка
            b.setAutoDefault(False)
            b.setDefault(False)

        self.toast = Toast(self.body)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._refresh_preview)
        self.desc.textChanged.connect(self._timer.start)
        self._restyle()
        theme.changed.connect(self._restyle)
        self._refresh_preview()
        self._fit_screen()
        self.desc.setFocus()

    # ------------------------------------------------------------ оформление
    def _restyle(self) -> None:
        """Поле описания и предпросмотр: в общей таблице стилей их нет (у QPlainTextEdit — только редактор кода)."""
        t = theme
        self.setStyleSheet(f"""
QPlainTextEdit#reportText {{ background: {t.css('bg')}; border: 1px solid {t.css('stroke_strong')};
    border-radius: 4px; padding: 4px 6px; selection-background-color: {t.css('accent')};
    selection-color: {t.css('on_accent')}; }}
QPlainTextEdit#reportText:focus {{ border-color: {t.css('accent')}; }}
QLabel#reportHint {{ color: {t.css('faint')}; font-size: 12px; }}
QPlainTextEdit#reportPreview {{ background: {t.css('editor_bg')}; border: 1px solid {t.css('stroke')};
    border-radius: 5px; padding: 4px 6px; color: {t.css('dim')}; font-family: "{t.mono_family}";
    font-size: 11px; selection-background-color: {t.css('editor_sel')}; selection-color: {t.css('text')}; }}
QPlainTextEdit#reportPreview::corner {{ background: transparent; border: none; }}
""")

    def _fit_screen(self) -> None:
        scr = (self.parentWidget().screen() if self.parentWidget() else None) or QGuiApplication.primaryScreen()
        avail = scr.availableGeometry() if scr else None
        w, h = 760, 800
        if avail is not None:
            w, h = min(w, avail.width() - 40), min(h, avail.height() - 60)
        self.setMinimumSize(min(620, w), min(600, h))
        self.resize(w, h)

    # ------------------------------------------------------------ отчёт
    def _sync(self) -> None:
        self.report["description"] = report.sanitize(self.desc.toPlainText().strip())

    def _refresh_preview(self) -> None:
        self._sync()
        bar = self.preview.verticalScrollBar()
        pos = bar.value()
        self.preview.setPlainText(report.to_markdown(self.report))
        bar.setValue(pos)

    def _recollect(self, on: bool) -> None:
        self.report = report.collect(self.desc.toPlainText(), include_settings=on, settings=self.settings)
        self._refresh_preview()

    def markdown(self) -> str:
        self._sync()
        return report.to_markdown(self.report)

    # ------------------------------------------------------------ действия
    def copy_report(self) -> None:
        QGuiApplication.clipboard().setText(self.markdown())
        self.toast.show_message("Отчёт скопирован — вставьте его в письмо или сообщение (Ctrl+V).")

    def save_report(self) -> Path | None:
        start = self.saved_path or _desktop() / report.default_zip_name()
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить отчёт", str(start), "Архив ZIP (*.zip)")
        if not path:
            return None
        if not path.lower().endswith(".zip"):
            path += ".zip"
        return self.save_to(Path(path))

    def save_to(self, path: Path) -> Path | None:
        self._sync()
        try:
            self.saved_path = report.save_zip(self.report, path)
        except OSError as e:
            self.toast.show_message(f"Не удалось сохранить отчёт: {e}", warn=True)
            return None
        self.toast.show_message(f"Отчёт сохранён: {self.saved_path.name}. Приложите его к сообщению на GitHub.")
        return self.saved_path

    def open_github(self) -> None:
        if self.saved_path is None or not self.saved_path.exists():
            self.save_report()   # файл нужен, чтобы приложить его к сообщению; отмена — откроем и без него
        self._sync()
        url = report.issue_url(self.report, report.default_title(self.report), self.saved_path)
        if not QDesktopServices.openUrl(QUrl.fromEncoded(QByteArray(url.encode("ascii")))):
            QGuiApplication.clipboard().setText(url)
            self.toast.show_message("Не удалось открыть браузер — ссылка скопирована, вставьте её в адресную строку.",
                                    warn=True)
            return
        if self.saved_path is not None:
            self.toast.show_message(f"В браузере открылось новое сообщение — перетащите в него файл "
                                    f"«{self.saved_path.name}» (он в папке {self.saved_path.parent.name}).", ms=9000)
        else:
            self.toast.show_message("В браузере открылось новое сообщение. Сохраните отчёт и приложите файл "
                                    "к сообщению — в нём журнал работы.", ms=9000)


_current: ReportDialog | None = None


def show_report_dialog(parent=None, settings: Settings | None = None) -> None:
    """Открыть окно (модально). Если оно уже открыто — только поднять его: ошибка может повторяться."""
    global _current
    if _current is not None:
        _current.raise_()
        _current.activateWindow()
        return
    _current = ReportDialog(parent, settings)
    try:
        _current.exec()
    finally:
        _current.deleteLater()
        _current = None


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    mode = next((a for a in sys.argv[1:] if a in ("dark", "light", "system")), "system")
    theme.setup(app, mode)
    try:
        from .frameless import NativeFrameStyler
        styler = NativeFrameStyler(app)   # системная рамка — в цветах темы
        app.installEventFilter(styler)
    except Exception:
        styler = None
    show_report_dialog()
    return 0


if __name__ == "__main__":
    sys.exit(main())
