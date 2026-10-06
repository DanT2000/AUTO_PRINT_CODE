"""Импорт образцов из Jupyter-тетрадок (.ipynb) и Markdown-файлов (.md).

Файл забирается целиком, как есть: одна задача, каждая ячейка — отдельный блок
в исходном порядке (пустые ячейки пропускаются). Разбить на задачи можно потом вручную.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .storage import BLOCK_CODE, BLOCK_MARKDOWN, Block, Task, Template

_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", re.M)


def _cell_text(cell: dict) -> str:
    src = cell.get("source", "")
    text = "".join(src) if isinstance(src, list) else str(src)
    return text.replace("\r\n", "\n").rstrip("\n")


def _task_title(markdown: str, n: int) -> str:
    m = _HEADING.search(markdown)
    if m:
        title = m.group(1)
    else:
        title = next((ln.strip() for ln in markdown.splitlines() if ln.strip()), "")
    title = re.sub(r"[*_`]", "", title).strip()
    if len(title) > 60:
        title = title[:57] + "…"
    return title or f"Задача {n}"


def _build(title: str, cells: list[tuple], lang: str) -> Template:
    """cells: [(kind, text)] где kind = 'markdown' | 'code'. Одна ячейка = один блок, порядок как в файле."""
    blocks = []
    for kind, text, *meta in cells:
        if not text.strip():
            continue
        m = meta[0] if meta else {}
        blocks.append(Block(BLOCK_MARKDOWN if kind == "markdown" else BLOCK_CODE, text, lang=lang,
                            role=m.get("role", "text"), title=m.get("title", "")))
    if not blocks:
        return Template(title=title, tasks=[])
    md = next((b.text for b in blocks if b.type == BLOCK_MARKDOWN and _HEADING.search(b.text)), "")
    task_title = _task_title(md, 1) if md else title
    return Template(title=title, tasks=[Task(title=task_title, blocks=blocks)])


def _guess_lang(nb: dict) -> str:
    meta = nb.get("metadata", {})
    name = (meta.get("language_info", {}).get("name")
            or meta.get("kernelspec", {}).get("language") or "python")
    return str(name).lower()


def import_ipynb(path: str) -> Template:
    nb = json.loads(Path(path).read_text(encoding="utf-8"))
    if "cells" not in nb:
        raise ValueError("Это не тетрадка Jupyter (нет поля cells). Поддерживается формат nbformat 4.")
    lang = _guess_lang(nb)
    cells = []
    for c in nb["cells"]:
        kind = c.get("cell_type")
        if kind in ("markdown", "code"):
            cells.append((kind, _cell_text(c), c.get("metadata", {}).get("autoprintcode", {})))
        elif kind == "raw":
            cells.append(("markdown", "```\n" + _cell_text(c) + "\n```"))
    return _build(Path(path).stem, cells, lang)


_FENCE = re.compile(r"^```[ \t]*([\w+#.-]*)[^\n]*\n(.*?)^```[ \t]*$", re.M | re.S)


def import_markdown(path: str) -> Template:
    text = Path(path).read_text(encoding="utf-8").replace("\r\n", "\n")
    cells: list[tuple] = []
    pos = 0
    langs = []
    for m in _FENCE.finditer(text):
        cells.append(("markdown", text[pos:m.start()].strip("\n")))
        cells.append(("code", m.group(2).rstrip("\n")))
        langs.append(m.group(1).lower() or "text")
        pos = m.end()
    cells.append(("markdown", text[pos:].strip("\n")))
    t = _build(Path(path).stem, cells, "python")
    # проставить язык каждому блоку кода по его ```-метке
    code_blocks = [b for task in t.tasks for b in task.blocks if b.type == BLOCK_CODE]
    nonempty_langs = [lang for (kind, txt), lang in zip([c for c in cells if c[0] == "code"], langs) if txt.strip()]
    for b, lang in zip(code_blocks, nonempty_langs):
        b.lang = lang
    return t


def export_ipynb(t: Template, path: str) -> None:
    """Образец → тетрадка: условие → markdown-ячейка, блок кода → code-ячейка."""
    def src(text: str) -> list[str]:
        lines = text.split("\n")
        return [ln + "\n" for ln in lines[:-1]] + [lines[-1]]

    cells = []
    lang = "python"
    for task in t.tasks:
        for b in task.blocks:
            if b.type == BLOCK_MARKDOWN:
                cells.append({"cell_type": "markdown", "source": src(b.text),
                              "metadata": {"autoprintcode": {"role": b.role, "title": b.title}}})
            else:
                lang = b.lang or lang
                cells.append({"cell_type": "code", "metadata": {"autoprintcode": {"title": b.title}},
                              "execution_count": None,
                              "outputs": [], "source": src(b.text)})
    nb = {"cells": cells, "nbformat": 4, "nbformat_minor": 5,
          "metadata": {"language_info": {"name": lang}}}
    Path(path).write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8")
