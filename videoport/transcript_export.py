"""Stream transcripts to text, subtitle and document formats."""

import csv
from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZipFile, ZIP_DEFLATED

from .optional import optional_module

EXPORT_FORMATS = {"txt", "csv", "srt", "vtt", "docx", "pdf"}
PDF_WHEELS = (
    ("https://files.pythonhosted.org/packages/d7/ed/1b16fb66a37e1b9e41370b93b2c614c7da761a5b2a41dc333f7af4639962/"
     "pillow-13.0.0-cp313-cp313-win_amd64.whl",
     "9dda0788a9932776a75d8bcc020076edc308ce2508b51f6739a45e3158b3fc42", 7284777),
    ("https://files.pythonhosted.org/packages/0e/ee/5f7a31ab05cf817e0cc70ae6df51a1a4fda188c899790a3131a24dd78d18/"
     "reportlab-4.4.6-py3-none-any.whl",
     "c7c31d5c815bae7c76fc17f64ffc417e68992901acddb24504296cc39b065424", 1954259),
)
FONTS = Path(__file__).resolve().parent.parent / "docs" / "fonts"
DOCX_RELS = """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>"""
DOCX_TYPES = """<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>"""


def timestamp(seconds, separator="."):
    minutes, millis = divmod(round(seconds * 1000), 60000)
    hours, minutes = divmod(minutes, 60)
    seconds, millis = divmod(millis, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02}{separator}{millis:03}"


def speaker(row, names):
    return (names.get(row["speaker"]) or row["speaker"]) if names is not None else ""


def reading_lines(rows, names, timestamps):
    for row in rows:
        label = speaker(row, names)
        at = "[" + timestamp(row["start"]).split(".")[0] + "] " if timestamps else ""
        yield at + (label + ": " if label else "") + row["text"]


def headings_xml(text):
    text = "".join(char for char in text if ord(char) >= 32 or char in "\t\n\r")
    return "<w:p><w:r><w:t xml:space=\"preserve\">" + escape(text) + "</w:t></w:r></w:p>"


def write_docx(path, title, lines):
    with ZipFile(path, "w", ZIP_DEFLATED) as document:
        document.writestr("[Content_Types].xml", DOCX_TYPES)
        document.writestr("_rels/.rels", DOCX_RELS)
        with document.open("word/document.xml", mode="w", force_zip64=True) as xml:
            xml.write(b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>')
            xml.write(headings_xml(title).encode())
            for line in lines:
                for part in line.splitlines():
                    xml.write(headings_xml(part).encode())
            xml.write(b"</w:body></w:document>")


def write_pdf(path, title, lines, language):
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import simpleSplit

    family = "VideoportText"
    if language in {"zh", "yue", "ja", "ko"}:
        family = {"zh": "STSong-Light", "yue": "MSung-Light", "ja": "HeiseiKakuGo-W5", "ko": "HYGoThic-Medium"}[language]
        if family not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(UnicodeCIDFont(family))
    elif family not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(family, str(FONTS / "Manrope.ttf")))
    doc = canvas.Canvas(str(path), pagesize=(595, 842), pageCompression=1)
    doc.setTitle(title)
    y = 798
    for text in (title, ""):
        doc.setFont(family, 14)
        doc.drawString(44, y, text[:90])
        y -= 19
    for entry in lines:
        for part in entry.splitlines() or [""]:
            for line in simpleSplit(part, family, 11, 505) or [""]:
                if y < 44:
                    doc.showPage()
                    y = 798
                doc.setFont(family, 11)
                doc.drawString(44, y, line)
                y -= 15
        y -= 5
    doc.save()


def prepare_pdf(cache, progress):
    optional_module("PIL", cache, "pdf-libraries-v1", PDF_WHEELS, "Загрузка компонентов PDF", progress)
    optional_module("reportlab", cache, "pdf-libraries-v1", PDF_WHEELS, "Загрузка компонентов PDF", progress)


def write_export(path, rows, title, names, timestamps, language="auto"):
    kind = path.suffix[1:]
    if kind == "docx":
        return write_docx(path, title, reading_lines(rows, names, timestamps))
    if kind == "pdf":
        return write_pdf(path, title, reading_lines(rows, names, timestamps), language)
    with path.open("w", encoding="utf-8-sig" if kind == "csv" else "utf-8", newline="") as output:
        if kind == "csv":
            writer = csv.writer(output)
            writer.writerow(["Начало", "Конец", "Говорящий", "Текст"])
            for row in rows:
                safe = lambda value: "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value
                writer.writerow([timestamp(row["start"]), timestamp(row["end"]), safe(speaker(row, names)), safe(row["text"])])
        elif kind == "txt":
            for line in reading_lines(rows, names, timestamps):
                output.write(line + "\n")
        else:
            if kind == "vtt":
                output.write("WEBVTT\n\n")
            separator = "," if kind == "srt" else "."
            index = 0
            for row in rows:
                text = row["text"].strip().replace("-->", "→")
                if not text:
                    continue
                text = "\n".join(line for line in text.splitlines() if line.strip())
                if kind == "vtt":
                    text = escape(text)
                else:
                    text = text.replace("<", "‹").replace(">", "›")
                label = speaker(row, names).replace("\n", " ").replace("-->", "→")
                if label:
                    clean_label = escape(label) if kind == "vtt" else label.replace("<", "‹").replace(">", "›")
                    text = clean_label + ": " + text
                index += 1
                timing = timestamp(row["start"], separator) + " --> " + timestamp(row["end"], separator)
                output.write(f"{index}\n{timing}\n{text}\n\n")
