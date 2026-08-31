#!/usr/bin/env python3
"""
Image Grid Composer
===================

A small desktop app for arranging images into a grid, captioning them, and
exporting the result as a single image or as LaTeX.

Features
--------
* Add images by drag-and-drop, by file dialog, or by pasting from the
  clipboard (Ctrl+V) -- e.g. straight from a screenshot tool or a browser.
* Reorder (drag in the list, or the Up/Down buttons) and remove images.
* A caption per image (optional) and one caption for the whole collection
  (optional).
* Column count is auto-suggested from the number of images but you can
  override it. If the last row is not full you choose whether it sits
  left, centre or right.
* Spacing, outer margin, background colour and font sizes are adjustable.
* Live preview of exactly what will be exported.
* Export as PNG/JPG to a folder, copy the composed image to the clipboard,
  or export a .tex file plus a folder of the images.

Install
-------
    pip install PyQt6 Pillow

Run
---
    python image_grid_composer.py
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont

from PyQt6.QtCore import QSize, Qt, QTimer
from PyQt6.QtGui import (
    QAction,
    QColor,
    QGuiApplication,
    QIcon,
    QImage,
    QKeySequence,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff", ".webp"}

try:  # Pillow >= 9.1
    RESAMPLE = Image.Resampling.LANCZOS
except AttributeError:  # pragma: no cover - older Pillow
    RESAMPLE = Image.LANCZOS


# --------------------------------------------------------------------------
# Model
# --------------------------------------------------------------------------


@dataclass
class ImageItem:
    """One picture in the collection."""

    image: Image.Image
    caption: str = ""
    name: str = "image"


@dataclass
class Layout:
    """Everything that controls how the grid is drawn."""

    columns: int = 2
    last_row_align: str = "center"  # "left" | "center" | "right"
    cell_width: int = 700           # px, width of one column in the export
    gap: int = 24                   # px between cells
    margin: int = 40                # px around the whole thing
    background: Tuple[int, int, int] = (255, 255, 255)
    transparent: bool = False
    caption_size: int = 30          # px font size for per-image captions
    title_size: int = 40            # px font size for the overall caption
    text_colour: Tuple[int, int, int] = (25, 25, 25)
    title: str = ""


# --------------------------------------------------------------------------
# Font helpers
# --------------------------------------------------------------------------

_FONT_CANDIDATES = [
    "arial.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/Library/Fonts/Arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "DejaVuSans.ttf",
]

_BOLD_CANDIDATES = [
    "arialbd.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "DejaVuSans-Bold.ttf",
]

_font_cache: dict = {}


def get_font(size: int, bold: bool = False):
    key = (size, bold)
    if key in _font_cache:
        return _font_cache[key]
    size = max(6, int(size))
    for path in (_BOLD_CANDIDATES if bold else _FONT_CANDIDATES):
        try:
            font = ImageFont.truetype(path, size)
            _font_cache[key] = font
            return font
        except Exception:
            continue
    font = ImageFont.load_default()
    _font_cache[key] = font
    return font


def _text_width(draw: ImageDraw.ImageDraw, text: str, font) -> float:
    if not text:
        return 0.0
    return draw.textlength(text, font=font)


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> List[str]:
    """Greedy word wrap that also honours explicit newlines."""
    lines: List[str] = []
    for paragraph in text.split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = words[0]
        for word in words[1:]:
            trial = current + " " + word
            if _text_width(draw, trial, font) <= max_width:
                current = trial
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


def line_height(font, size: int) -> int:
    try:
        ascent, descent = font.getmetrics()
        return int((ascent + descent) * 1.25)
    except Exception:
        return int(size * 1.45)


# --------------------------------------------------------------------------
# Composition
# --------------------------------------------------------------------------


def suggest_columns(n: int) -> int:
    """A sensible default column count for n images."""
    if n <= 1:
        return 1
    if n <= 4:
        return 2
    if n <= 9:
        return 3
    if n <= 16:
        return 4
    return 5


def chunk_rows(items: Sequence, columns: int) -> List[List]:
    columns = max(1, columns)
    return [list(items[i:i + columns]) for i in range(0, len(items), columns)]


def compose(items: Sequence[ImageItem], layout: Layout, scale: float = 1.0) -> Image.Image:
    """Render the grid to a Pillow image.

    ``scale`` shrinks everything uniformly -- used for the live preview so we
    do not have to build a 5000px canvas on every keystroke.
    """
    if not items:
        return Image.new("RGBA", (600, 300), (0, 0, 0, 0))

    cols = max(1, int(layout.columns))
    cell_w = max(40, int(layout.cell_width * scale))
    gap = max(0, int(round(layout.gap * scale)))
    margin = max(0, int(round(layout.margin * scale)))
    cap_font = get_font(max(7, int(layout.caption_size * scale)))
    title_font = get_font(max(8, int(layout.title_size * scale)), bold=True)

    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    cap_lh = line_height(cap_font, int(layout.caption_size * scale))
    title_lh = line_height(title_font, int(layout.title_size * scale))
    cap_pad = max(2, int(round(8 * scale)))

    # --- measure every cell -------------------------------------------------
    prepared = []  # (resized image, caption lines, image height, cell height)
    for item in items:
        img = item.image
        w, h = img.size
        new_h = max(1, int(round(h * (cell_w / float(w)))))
        resized = img.resize((cell_w, new_h), RESAMPLE)
        lines = wrap_text(probe, item.caption, cap_font, cell_w) if item.caption.strip() else []
        cap_h = (cap_pad + len(lines) * cap_lh) if lines else 0
        prepared.append((resized, lines, new_h, new_h + cap_h))

    rows = chunk_rows(prepared, cols)
    row_heights = [max(cell[3] for cell in row) for row in rows]

    grid_w = cols * cell_w + (cols - 1) * gap
    total_w = grid_w + margin * 2
    total_h = margin * 2 + sum(row_heights) + gap * (len(rows) - 1)

    title_lines: List[str] = []
    if layout.title.strip():
        title_lines = wrap_text(probe, layout.title, title_font, grid_w)
        total_h += gap + len(title_lines) * title_lh

    if layout.transparent:
        canvas = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 0))
    else:
        canvas = Image.new("RGBA", (total_w, total_h), tuple(layout.background) + (255,))
    draw = ImageDraw.Draw(canvas)

    # --- draw ---------------------------------------------------------------
    y = margin
    for row, row_h in zip(rows, row_heights):
        n = len(row)
        content_w = n * cell_w + (n - 1) * gap
        slack = grid_w - content_w
        if n == cols or layout.last_row_align == "left":
            x0 = margin
        elif layout.last_row_align == "right":
            x0 = margin + slack
        else:
            x0 = margin + slack // 2

        x = x0
        for resized, lines, img_h, _cell_h in row:
            canvas.paste(resized, (x, y), resized if resized.mode == "RGBA" else None)
            if lines:
                ty = y + img_h + cap_pad
                for line in lines:
                    tw = _text_width(draw, line, cap_font)
                    draw.text(
                        (x + (cell_w - tw) / 2, ty),
                        line,
                        font=cap_font,
                        fill=tuple(layout.text_colour),
                    )
                    ty += cap_lh
            x += cell_w + gap
        y += row_h + gap

    if title_lines:
        ty = y
        for line in title_lines:
            tw = _text_width(draw, line, title_font)
            draw.text(
                (margin + (grid_w - tw) / 2, ty),
                line,
                font=title_font,
                fill=tuple(layout.text_colour),
            )
            ty += title_lh

    return canvas


# --------------------------------------------------------------------------
# LaTeX export
# --------------------------------------------------------------------------


def latex_escape(text: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(ch, ch) for ch in text)


def safe_stem(text: str, fallback: str) -> str:
    keep = [c if (c.isalnum() or c in "-_") else "_" for c in text.strip()]
    stem = "".join(keep).strip("_")
    return stem or fallback


def build_latex(
    items: Sequence[ImageItem],
    layout: Layout,
    image_names: Sequence[str],
    image_dir_name: str,
    standalone: bool = False,
) -> str:
    """Produce LaTeX that reproduces the same grid."""
    cols = max(1, int(layout.columns))
    # leave a small horizontal gutter between columns
    gutter = 0.02
    frac = (1.0 - gutter * (cols - 1)) / cols

    rows = chunk_rows(list(zip(items, image_names)), cols)

    body: List[str] = []
    body.append(r"\begin{figure}[htbp]")
    body.append(r"  \centering")

    align_letter = {"left": "l", "center": "c", "right": "r"}.get(
        layout.last_row_align, "c"
    )

    for r_index, row in enumerate(rows):
        n = len(row)
        incomplete = n < cols
        indent = "    " if incomplete else "  "
        pieces: List[str] = []

        if incomplete:
            # A short row is boxed to the full text width so the alignment
            # (left / centre / right) is exact rather than glue-dependent.
            pieces.append(r"  \makebox[\textwidth][%s]{%%" % align_letter)

        for i, (item, fname) in enumerate(row):
            sub = []
            sub.append(indent + r"\begin{subfigure}[t]{%.4f\textwidth}" % frac)
            sub.append(indent + r"  \centering")
            sub.append(
                indent + r"  \includegraphics[width=\linewidth]{%s/%s}"
                % (image_dir_name, fname)
            )
            if item.caption.strip():
                sub.append(indent + r"  \caption{%s}" % latex_escape(item.caption.strip()))
            sub.append(indent + r"\end{subfigure}%")
            pieces.append("\n".join(sub))
            if i < n - 1:
                if incomplete:
                    pieces.append(indent + r"\hspace{%.4f\textwidth}%%" % gutter)
                else:
                    pieces.append(indent + r"\hfill%")

        if incomplete:
            pieces.append(r"  }")

        body.append("\n".join(pieces))
        if r_index < len(rows) - 1:
            body.append(r"  \\[1.5ex]")
        elif layout.title.strip():
            body.append(r"  \par\medskip")

    if layout.title.strip():
        body.append(r"  \caption{%s}" % latex_escape(layout.title.strip()))
    body.append(r"  \label{fig:image-grid}")
    body.append(r"\end{figure}")

    figure = "\n".join(body)

    if not standalone:
        header = (
            "% Requires in your preamble:\n"
            "%   \\usepackage{graphicx}\n"
            "%   \\usepackage{subcaption}\n"
            "% Images live in the '"
            + image_dir_name
            + "' folder next to this file.\n\n"
        )
        return header + figure + "\n"

    return (
        "\\documentclass{article}\n"
        "\\usepackage{graphicx}\n"
        "\\usepackage{subcaption}\n"
        "\\usepackage[margin=2cm]{geometry}\n"
        "\\begin{document}\n\n"
        + figure
        + "\n\n\\end{document}\n"
    )


# --------------------------------------------------------------------------
# Qt <-> Pillow helpers
# --------------------------------------------------------------------------


def pil_to_qimage(img: Image.Image) -> QImage:
    rgba = img.convert("RGBA")
    data = rgba.tobytes("raw", "RGBA")
    qimg = QImage(data, rgba.width, rgba.height, rgba.width * 4, QImage.Format.Format_RGBA8888)
    return qimg.copy()


def qimage_to_pil(qimg: QImage) -> Image.Image:
    qimg = qimg.convertToFormat(QImage.Format.Format_RGBA8888)
    width, height = qimg.width(), qimg.height()
    ptr = qimg.constBits()
    ptr.setsize(height * qimg.bytesPerLine())
    buf = bytes(ptr)
    # rows may be padded, so slice each row to width*4
    stride = qimg.bytesPerLine()
    rows = b"".join(buf[y * stride: y * stride + width * 4] for y in range(height))
    return Image.frombytes("RGBA", (width, height), rows)


# --------------------------------------------------------------------------
# Widgets
# --------------------------------------------------------------------------


class DropListWidget(QListWidget):
    """List of images: accepts dropped files and supports internal reordering."""

    def __init__(self, on_files, parent=None):
        super().__init__(parent)
        self._on_files = on_files
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setIconSize(QSize(72, 72))

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            paths = []
            for url in event.mimeData().urls():
                path = url.toLocalFile()
                if path and os.path.splitext(path)[1].lower() in IMAGE_EXTS:
                    paths.append(path)
            if paths:
                self._on_files(paths)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)
            # Let Qt finish removing the dragged row before we read the order.
            QTimer.singleShot(0, self.parent_reordered)

    def parent_reordered(self):
        window = self.window()
        if hasattr(window, "sync_order_from_list"):
            window.sync_order_from_list()


class PreviewArea(QScrollArea):
    """Scrollable preview that also accepts dropped image files."""

    def __init__(self, on_files, parent=None):
        super().__init__(parent)
        self._on_files = on_files
        self.setAcceptDrops(True)
        self.setWidgetResizable(True)
        self.label = QLabel("Drop images here, click “Add images…”, or press Ctrl+V")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setStyleSheet("color:#777; padding:40px;")
        self.setWidget(self.label)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = []
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path and os.path.splitext(path)[1].lower() in IMAGE_EXTS:
                paths.append(path)
        if paths:
            self._on_files(paths)
        event.acceptProposedAction()


# --------------------------------------------------------------------------
# Main window
# --------------------------------------------------------------------------


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Image Grid Composer")
        self.resize(1240, 820)

        self.items: List[ImageItem] = []
        self.layout_opts = Layout()
        self._columns_touched = False      # has the user overridden the suggestion?
        self._updating_ui = False

        self._build_ui()
        self._build_actions()
        self.schedule_preview()

    # -- UI construction ----------------------------------------------------

    def _build_ui(self):
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ---------------- left panel ----------------
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(8, 8, 8, 8)

        self.list = DropListWidget(self.add_paths)
        self.list.currentRowChanged.connect(self.on_selection_changed)
        self.list.model().rowsMoved.connect(lambda *a: self.sync_order_from_list())
        lv.addWidget(QLabel("<b>Images</b>  <span style='color:#777'>(drag to reorder)</span>"))
        lv.addWidget(self.list, 1)

        btn_row = QHBoxLayout()
        self.btn_add = QPushButton("Add images…")
        self.btn_paste = QPushButton("Paste (Ctrl+V)")
        self.btn_up = QPushButton("↑")
        self.btn_down = QPushButton("↓")
        self.btn_del = QPushButton("Remove")
        for b in (self.btn_add, self.btn_paste, self.btn_up, self.btn_down, self.btn_del):
            btn_row.addWidget(b)
        self.btn_up.setFixedWidth(34)
        self.btn_down.setFixedWidth(34)
        self.btn_add.clicked.connect(self.browse_for_images)
        self.btn_paste.clicked.connect(self.paste_from_clipboard)
        self.btn_up.clicked.connect(lambda: self.move_selected(-1))
        self.btn_down.clicked.connect(lambda: self.move_selected(1))
        self.btn_del.clicked.connect(self.remove_selected)
        lv.addLayout(btn_row)

        cap_box = QGroupBox("Caption for the selected image")
        cap_v = QVBoxLayout(cap_box)
        self.caption_edit = QLineEdit()
        self.caption_edit.setPlaceholderText("Leave blank for no caption")
        self.caption_edit.textEdited.connect(self.on_caption_edited)
        self.caption_edit.setEnabled(False)
        cap_v.addWidget(self.caption_edit)
        lv.addWidget(cap_box)

        title_box = QGroupBox("Caption for the whole collection")
        title_v = QVBoxLayout(title_box)
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("Leave blank for no overall caption")
        self.title_edit.textEdited.connect(self.on_title_edited)
        title_v.addWidget(self.title_edit)
        lv.addWidget(title_box)

        # ---------------- middle: preview ----------------
        middle = QWidget()
        mv = QVBoxLayout(middle)
        mv.setContentsMargins(8, 8, 8, 8)
        self.preview_header = QLabel("<b>Preview</b>")
        mv.addWidget(self.preview_header)
        self.preview = PreviewArea(self.add_paths)
        mv.addWidget(self.preview, 1)

        export_row = QHBoxLayout()
        self.btn_save = QPushButton("Save image…")
        self.btn_copy = QPushButton("Copy image to clipboard")
        self.btn_latex = QPushButton("Export LaTeX…")
        for b in (self.btn_save, self.btn_copy, self.btn_latex):
            export_row.addWidget(b)
        self.btn_save.clicked.connect(self.export_image)
        self.btn_copy.clicked.connect(self.copy_to_clipboard)
        self.btn_latex.clicked.connect(self.export_latex)
        mv.addLayout(export_row)

        # ---------------- right: settings ----------------
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(8, 8, 8, 8)

        grid_box = QGroupBox("Grid")
        gf = QFormLayout(grid_box)
        self.spin_cols = QSpinBox()
        self.spin_cols.setRange(1, 12)
        self.spin_cols.setValue(2)
        self.spin_cols.valueChanged.connect(self.on_columns_changed)
        gf.addRow("Columns", self.spin_cols)

        self.combo_align = QComboBox()
        self.combo_align.addItems(["Left", "Centre", "Right"])
        self.combo_align.setCurrentIndex(1)
        self.combo_align.currentIndexChanged.connect(self.on_align_changed)
        gf.addRow("Last row", self.combo_align)

        self.btn_auto = QPushButton("Re-suggest columns")
        self.btn_auto.clicked.connect(self.auto_columns)
        gf.addRow("", self.btn_auto)
        rv.addWidget(grid_box)

        space_box = QGroupBox("Spacing & background")
        sf = QFormLayout(space_box)
        self.spin_cell = QSpinBox()
        self.spin_cell.setRange(100, 4000)
        self.spin_cell.setSingleStep(50)
        self.spin_cell.setValue(self.layout_opts.cell_width)
        self.spin_cell.setSuffix(" px")
        self.spin_cell.valueChanged.connect(self.on_layout_number_changed)
        sf.addRow("Column width", self.spin_cell)

        self.spin_gap = QSpinBox()
        self.spin_gap.setRange(0, 400)
        self.spin_gap.setValue(self.layout_opts.gap)
        self.spin_gap.setSuffix(" px")
        self.spin_gap.valueChanged.connect(self.on_layout_number_changed)
        sf.addRow("Gap", self.spin_gap)

        self.spin_margin = QSpinBox()
        self.spin_margin.setRange(0, 500)
        self.spin_margin.setValue(self.layout_opts.margin)
        self.spin_margin.setSuffix(" px")
        self.spin_margin.valueChanged.connect(self.on_layout_number_changed)
        sf.addRow("Outer margin", self.spin_margin)

        self.btn_bg = QPushButton("Background colour…")
        self.btn_bg.clicked.connect(self.pick_background)
        sf.addRow("", self.btn_bg)

        self.chk_transparent = QCheckBox("Transparent background (PNG)")
        self.chk_transparent.stateChanged.connect(self.on_transparent_changed)
        sf.addRow("", self.chk_transparent)
        rv.addWidget(space_box)

        text_box = QGroupBox("Text")
        tf = QFormLayout(text_box)
        self.spin_cap_size = QSpinBox()
        self.spin_cap_size.setRange(8, 200)
        self.spin_cap_size.setValue(self.layout_opts.caption_size)
        self.spin_cap_size.setSuffix(" px")
        self.spin_cap_size.valueChanged.connect(self.on_layout_number_changed)
        tf.addRow("Caption size", self.spin_cap_size)

        self.spin_title_size = QSpinBox()
        self.spin_title_size.setRange(8, 300)
        self.spin_title_size.setValue(self.layout_opts.title_size)
        self.spin_title_size.setSuffix(" px")
        self.spin_title_size.valueChanged.connect(self.on_layout_number_changed)
        tf.addRow("Overall caption size", self.spin_title_size)

        self.btn_text_colour = QPushButton("Text colour…")
        self.btn_text_colour.clicked.connect(self.pick_text_colour)
        tf.addRow("", self.btn_text_colour)
        rv.addWidget(text_box)

        latex_box = QGroupBox("LaTeX export")
        lf = QVBoxLayout(latex_box)
        self.chk_standalone = QCheckBox("Write a full compilable document")
        lf.addWidget(self.chk_standalone)
        lf.addWidget(QLabel(
            "<span style='color:#777'>Otherwise you get just the figure "
            "environment to paste in.<br>Needs <tt>graphicx</tt> and "
            "<tt>subcaption</tt>.</span>"
        ))
        rv.addWidget(latex_box)

        rv.addStretch(1)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color:#2a6;")
        rv.addWidget(self.status)

        splitter.addWidget(left)
        splitter.addWidget(middle)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 5)
        splitter.setStretchFactor(2, 2)
        self.setCentralWidget(splitter)

        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(180)
        self._preview_timer.timeout.connect(self.render_preview)

    def _build_actions(self):
        paste = QAction("Paste", self)
        paste.setShortcut(QKeySequence.StandardKey.Paste)
        paste.triggered.connect(self.paste_from_clipboard)
        self.addAction(paste)

        open_act = QAction("Open", self)
        open_act.setShortcut(QKeySequence.StandardKey.Open)
        open_act.triggered.connect(self.browse_for_images)
        self.addAction(open_act)

        save_act = QAction("Save", self)
        save_act.setShortcut(QKeySequence.StandardKey.Save)
        save_act.triggered.connect(self.export_image)
        self.addAction(save_act)

        copy_act = QAction("Copy", self)
        copy_act.setShortcut(QKeySequence.StandardKey.Copy)
        copy_act.triggered.connect(self.copy_to_clipboard)
        self.addAction(copy_act)

    # -- adding / removing images ------------------------------------------

    def add_paths(self, paths: Sequence[str]):
        added = 0
        for path in paths:
            try:
                img = Image.open(path)
                img.load()
                img = img.convert("RGBA")
            except Exception as exc:  # noqa: BLE001
                self.set_status(f"Could not open {os.path.basename(path)}: {exc}", error=True)
                continue
            name = os.path.splitext(os.path.basename(path))[0]
            self.items.append(ImageItem(image=img, caption="", name=name))
            added += 1
        if added:
            self.refresh_list()
            self.maybe_auto_columns()
            self.set_status(f"Added {added} image{'s' if added != 1 else ''}.")
            self.schedule_preview()

    def paste_from_clipboard(self):
        clipboard = QGuiApplication.clipboard()
        mime = clipboard.mimeData()

        # If the user is typing in a caption box, Ctrl+V should paste text there.
        focus = QApplication.focusWidget()
        if isinstance(focus, QLineEdit) and mime.hasText() and not mime.hasImage():
            focus.paste()
            return

        if mime.hasImage():
            qimg = clipboard.image()
            if not qimg.isNull():
                pil = qimage_to_pil(qimg)
                self.items.append(
                    ImageItem(image=pil, caption="", name=f"pasted_{len(self.items) + 1}")
                )
                self.refresh_list()
                self.maybe_auto_columns()
                self.set_status("Pasted image from clipboard.")
                self.schedule_preview()
                return

        if mime.hasUrls():
            paths = [
                u.toLocalFile()
                for u in mime.urls()
                if u.toLocalFile() and os.path.splitext(u.toLocalFile())[1].lower() in IMAGE_EXTS
            ]
            if paths:
                self.add_paths(paths)
                return

        self.set_status("Nothing image-like on the clipboard.", error=True)

    def browse_for_images(self):
        patterns = " ".join("*" + e for e in sorted(IMAGE_EXTS))
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Choose images", "", f"Images ({patterns});;All files (*)"
        )
        if paths:
            self.add_paths(paths)

    def remove_selected(self):
        row = self.list.currentRow()
        if row < 0:
            return
        del self.items[row]
        self.refresh_list()
        self.list.setCurrentRow(min(row, len(self.items) - 1))
        self.maybe_auto_columns()
        self.schedule_preview()

    def move_selected(self, delta: int):
        row = self.list.currentRow()
        new = row + delta
        if row < 0 or new < 0 or new >= len(self.items):
            return
        self.items[row], self.items[new] = self.items[new], self.items[row]
        self.refresh_list()
        self.list.setCurrentRow(new)
        self.schedule_preview()

    def sync_order_from_list(self):
        """After an internal drag-reorder, rebuild self.items to match the list."""
        if self._updating_ui:
            return
        order = []
        for i in range(self.list.count()):
            idx = self.list.item(i).data(Qt.ItemDataRole.UserRole)
            if isinstance(idx, int) and 0 <= idx < len(self.items):
                order.append(self.items[idx])
        if len(order) == len(self.items):
            self.items = order
            self.refresh_list()
            self.schedule_preview()

    def refresh_list(self):
        self._updating_ui = True
        current = self.list.currentRow()
        self.list.clear()
        for i, item in enumerate(self.items):
            thumb = item.image.copy()
            thumb.thumbnail((72, 72))
            entry = QListWidgetItem()
            label = item.caption.strip() or item.name
            entry.setText(f"{i + 1}. {label}")
            entry.setIcon(QIcon(QPixmap.fromImage(pil_to_qimage(thumb))))
            entry.setData(Qt.ItemDataRole.UserRole, i)
            self.list.addItem(entry)
        self._updating_ui = False
        if 0 <= current < self.list.count():
            self.list.setCurrentRow(current)
        self.on_selection_changed(self.list.currentRow())

    # -- settings callbacks -------------------------------------------------

    def on_selection_changed(self, row: int):
        if self._updating_ui:
            return
        if 0 <= row < len(self.items):
            self.caption_edit.setEnabled(True)
            self.caption_edit.setText(self.items[row].caption)
        else:
            self.caption_edit.setEnabled(False)
            self.caption_edit.setText("")

    def on_caption_edited(self, text: str):
        row = self.list.currentRow()
        if 0 <= row < len(self.items):
            self.items[row].caption = text
            entry = self.list.item(row)
            if entry is not None:
                entry.setText(f"{row + 1}. {text.strip() or self.items[row].name}")
            self.schedule_preview()

    def on_title_edited(self, text: str):
        self.layout_opts.title = text
        self.schedule_preview()

    def on_columns_changed(self, value: int):
        if not self._updating_ui:
            self._columns_touched = True
        self.layout_opts.columns = value
        self.schedule_preview()

    def on_align_changed(self, index: int):
        self.layout_opts.last_row_align = ["left", "center", "right"][index]
        self.schedule_preview()

    def on_layout_number_changed(self, *_):
        self.layout_opts.cell_width = self.spin_cell.value()
        self.layout_opts.gap = self.spin_gap.value()
        self.layout_opts.margin = self.spin_margin.value()
        self.layout_opts.caption_size = self.spin_cap_size.value()
        self.layout_opts.title_size = self.spin_title_size.value()
        self.schedule_preview()

    def on_transparent_changed(self, *_):
        self.layout_opts.transparent = self.chk_transparent.isChecked()
        self.schedule_preview()

    def pick_background(self):
        initial = QColor(*self.layout_opts.background)
        colour = QColorDialog.getColor(initial, self, "Background colour")
        if colour.isValid():
            self.layout_opts.background = (colour.red(), colour.green(), colour.blue())
            self.chk_transparent.setChecked(False)
            self.schedule_preview()

    def pick_text_colour(self):
        initial = QColor(*self.layout_opts.text_colour)
        colour = QColorDialog.getColor(initial, self, "Text colour")
        if colour.isValid():
            self.layout_opts.text_colour = (colour.red(), colour.green(), colour.blue())
            self.schedule_preview()

    def auto_columns(self):
        self._updating_ui = True
        self.spin_cols.setValue(suggest_columns(len(self.items)))
        self._updating_ui = False
        self._columns_touched = False
        self.layout_opts.columns = self.spin_cols.value()
        self.schedule_preview()

    def maybe_auto_columns(self):
        if not self._columns_touched:
            self._updating_ui = True
            self.spin_cols.setValue(suggest_columns(len(self.items)))
            self._updating_ui = False
            self.layout_opts.columns = self.spin_cols.value()

    # -- preview ------------------------------------------------------------

    def schedule_preview(self):
        self._preview_timer.start()

    def render_preview(self):
        if not self.items:
            self.preview.label.setPixmap(QPixmap())
            self.preview.label.setText("Drop images here, click “Add images…”, or press Ctrl+V")
            self.preview_header.setText("<b>Preview</b>")
            return

        # Render small for speed, then show at whatever size fits.
        target_total_width = 1100
        cols = max(1, self.layout_opts.columns)
        full_width = cols * self.layout_opts.cell_width + self.layout_opts.margin * 2
        scale = min(1.0, target_total_width / float(full_width))
        try:
            preview_img = compose(self.items, self.layout_opts, scale=scale)
        except Exception as exc:  # noqa: BLE001
            self.set_status(f"Preview failed: {exc}", error=True)
            return

        pix = QPixmap.fromImage(pil_to_qimage(preview_img))
        area_w = max(200, self.preview.viewport().width() - 24)
        if pix.width() > area_w:
            pix = pix.scaledToWidth(
                area_w, Qt.TransformationMode.SmoothTransformation
            )
        self.preview.label.setText("")
        self.preview.label.setPixmap(pix)
        self.preview.label.adjustSize()

        rows = (len(self.items) + cols - 1) // cols
        est = compose_size_estimate(self.items, self.layout_opts)
        self.preview_header.setText(
            f"<b>Preview</b>  <span style='color:#777'>{len(self.items)} images · "
            f"{cols} × {rows} grid · export {est[0]}×{est[1]} px</span>"
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.schedule_preview()

    # -- exports ------------------------------------------------------------

    def full_render(self) -> Optional[Image.Image]:
        if not self.items:
            QMessageBox.information(self, "Nothing to export", "Add some images first.")
            return None
        return compose(self.items, self.layout_opts, scale=1.0)

    def export_image(self):
        img = self.full_render()
        if img is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save composed image", "image_grid.png",
            "PNG image (*.png);;JPEG image (*.jpg);;TIFF image (*.tif)"
        )
        if not path:
            return
        ext = os.path.splitext(path)[1].lower()
        try:
            if ext in (".jpg", ".jpeg"):
                flat = Image.new("RGB", img.size, tuple(self.layout_opts.background))
                flat.paste(img, (0, 0), img)
                flat.save(path, quality=95)
            else:
                img.save(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Save failed", str(exc))
            return
        self.set_status(f"Saved {os.path.basename(path)} ({img.width}×{img.height} px).")

    def copy_to_clipboard(self):
        # Ctrl+C inside a caption box should copy the selected text, not the grid.
        focus = QApplication.focusWidget()
        if isinstance(focus, QLineEdit) and focus.hasSelectedText():
            focus.copy()
            return
        img = self.full_render()
        if img is None:
            return
        # Clipboard images do not carry alpha reliably, so flatten first.
        flat = Image.new("RGB", img.size, tuple(self.layout_opts.background))
        flat.paste(img, (0, 0), img)
        QGuiApplication.clipboard().setImage(pil_to_qimage(flat))
        self.set_status("Composed image copied to the clipboard — paste it anywhere.")

    def export_latex(self):
        if not self.items:
            QMessageBox.information(self, "Nothing to export", "Add some images first.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save LaTeX file", "image_grid.tex", "LaTeX file (*.tex)"
        )
        if not path:
            return
        if not path.lower().endswith(".tex"):
            path += ".tex"

        base_dir = os.path.dirname(path)
        stem = os.path.splitext(os.path.basename(path))[0]
        img_dir_name = f"{stem}_images"
        img_dir = os.path.join(base_dir, img_dir_name)
        os.makedirs(img_dir, exist_ok=True)

        names: List[str] = []
        used: set = set()
        try:
            for i, item in enumerate(self.items, start=1):
                stem_i = safe_stem(item.caption or item.name, f"image_{i}")
                candidate = f"{i:02d}_{stem_i}"
                while candidate in used:
                    candidate += "_"
                used.add(candidate)
                fname = candidate + ".png"
                item.image.save(os.path.join(img_dir, fname))
                names.append(fname)

            tex = build_latex(
                self.items,
                self.layout_opts,
                names,
                img_dir_name,
                standalone=self.chk_standalone.isChecked(),
            )
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(tex)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "LaTeX export failed", str(exc))
            return

        self.set_status(
            f"Wrote {os.path.basename(path)} and {len(names)} images into {img_dir_name}/."
        )

    # -- misc ---------------------------------------------------------------

    def set_status(self, text: str, error: bool = False):
        self.status.setStyleSheet("color:#c33;" if error else "color:#2a6;")
        self.status.setText(text)


def compose_size_estimate(items: Sequence[ImageItem], layout: Layout) -> Tuple[int, int]:
    """Exact export dimensions, measured without resizing any pixels."""
    if not items:
        return (0, 0)
    cols = max(1, layout.columns)
    cell_w = max(40, int(layout.cell_width))
    cap_font = get_font(layout.caption_size)
    title_font = get_font(layout.title_size, bold=True)
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    cap_lh = line_height(cap_font, layout.caption_size)
    title_lh = line_height(title_font, layout.title_size)
    cap_pad = max(2, 8)

    cell_heights: List[int] = []
    for item in items:
        w, h = item.image.size
        img_h = max(1, int(round(h * (cell_w / float(w)))))
        if item.caption.strip():
            lines = wrap_text(probe, item.caption, cap_font, cell_w)
            img_h += cap_pad + len(lines) * cap_lh
        cell_heights.append(img_h)

    rows = chunk_rows(cell_heights, cols)
    grid_w = cols * cell_w + (cols - 1) * layout.gap
    width = grid_w + layout.margin * 2
    height = layout.margin * 2 + sum(max(r) for r in rows) + layout.gap * (len(rows) - 1)
    if layout.title.strip():
        title_lines = wrap_text(probe, layout.title, title_font, grid_w)
        height += layout.gap + len(title_lines) * title_lh
    return (width, height)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Image Grid Composer")
    window = MainWindow()
    window.show()
    # Any image paths given on the command line get loaded straight away.
    args = [a for a in sys.argv[1:] if os.path.splitext(a)[1].lower() in IMAGE_EXTS]
    if args:
        window.add_paths(args)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
