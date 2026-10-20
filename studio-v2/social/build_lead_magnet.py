import argparse
import html
import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

ROOT = Path(__file__).resolve().parents[1]
GUIDES = ("00_showrunner.md", "01_attention.md", "02_retention.md", "03_narration.md")


def clean(text):
    for source, target in (("\u2014", "-"), ("\u2013", "-"), ("\u2011", "-"), ("\u2192", "->"),
                           ("\u201c", '"'), ("\u201d", '"'), ("\u00ab", '"'), ("\u00bb", '"')):
        text = text.replace(source, target)
    text = html.escape(text.replace(chr(96), ""))
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<i>\1</i>", text)
    return text


def build_pdf(destination, guides_dir=None):
    guides_dir = Path(guides_dir or ROOT / "creative-guides")
    texts = [(name, (guides_dir / name).read_text(encoding="utf-8")) for name in GUIDES]
    if any(not content.strip() for _, content in texts):
        raise ValueError("Une des quatre bibles est vide.")
    regular = Path("C:/Windows/Fonts/arial.ttf")
    bold = Path("C:/Windows/Fonts/arialbd.ttf")
    if regular.exists() and bold.exists():
        pdfmetrics.registerFont(TTFont("Studio", str(regular)))
        pdfmetrics.registerFont(TTFont("StudioBold", str(bold)))
        pdfmetrics.registerFontFamily("Studio", normal="Studio", bold="StudioBold", italic="Studio", boldItalic="StudioBold")
        font, strong = "Studio", "StudioBold"
    else:
        font, strong = "Helvetica", "Helvetica-Bold"
    ink, blue = colors.HexColor("#183d50"), colors.HexColor("#244e68")
    body = ParagraphStyle("StudioBody", fontName=font, fontSize=10, leading=14.2,
                          textColor=ink, spaceAfter=6)
    heading = ParagraphStyle("StudioHeading", parent=body, fontName=strong, fontSize=23,
                             leading=28, spaceAfter=16, keepWithNext=True)
    subheading = ParagraphStyle("StudioSub", parent=body, fontName=strong, fontSize=13,
                                leading=17, spaceBefore=12, spaceAfter=7, keepWithNext=True)
    bullet = ParagraphStyle("StudioBullet", parent=body, leftIndent=12, firstLineIndent=0,
                            bulletIndent=0, spaceAfter=6)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(destination), pagesize=(210 * mm, 297 * mm),
        rightMargin=23 * mm, leftMargin=23 * mm, topMargin=20 * mm, bottomMargin=21 * mm,
        title="Les quatre bibles Studio IA", author="AvenirIA",
    )
    story = [
        Spacer(1, 22 * mm), Paragraph("AvenirIA / Studio IA", subheading),
        Spacer(1, 14 * mm),
        Paragraph("Une histoire qui tient.<br/>Avant le premier rendu.", ParagraphStyle(
            "Cover", parent=heading, fontSize=34, leading=40,
        )),
        Spacer(1, 12 * mm),
        Paragraph("Les quatre bibles de production", subheading),
        Paragraph("Showrunner, attention, rétention, narration.", body),
        Spacer(1, 12 * mm),
        Paragraph("Un cadre pour écrire, préparer les références, contrôler les prises et documenter "
                  "les coûts. À adapter à ton histoire et à ton matériel.", body),
        Spacer(1, 20 * mm),
        Paragraph("Édition gratuite - 20 octobre 2026", body),
        Paragraph("Aucun résultat de vues ou de revenus n'est garanti.", body),
    ]
    for _, text in texts:
        story.append(PageBreak())
        paragraph = []

        def flush():
            if paragraph:
                story.append(Paragraph(clean(" ".join(paragraph)), body))
                paragraph.clear()

        for line in text.splitlines():
            line = line.strip()
            if not line:
                flush()
            elif line.startswith("# "):
                flush()
                story.append(Paragraph(clean(line[2:]), heading))
            elif line.startswith("## "):
                flush()
                story.append(Paragraph(clean(line[3:]), subheading))
            elif line.startswith("- "):
                flush()
                story.append(Paragraph(clean(line[2:]), bullet, bulletText="-"))
            elif re.match(r"^\d+\.\s", line):
                flush()
                number, content = line.split(". ", 1)
                story.append(Paragraph(clean(content), bullet, bulletText=number + "."))
            else:
                paragraph.append(line)
        flush()

    def footer(canvas, document):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#ccd7dd"))
        canvas.line(23 * mm, 17 * mm, 187 * mm, 17 * mm)
        canvas.setFillColor(blue)
        canvas.setFont(font, 8)
        canvas.drawString(23 * mm, 12 * mm, "AvenirIA - Les quatre bibles Studio IA")
        canvas.drawRightString(187 * mm, 12 * mm, str(document.page))
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return destination


def main():
    parser = argparse.ArgumentParser(description="Assembler les quatre bibles en PDF.")
    parser.add_argument("--output", type=Path, default=ROOT / "web" / "funnel" / "bible-studio-ia.pdf")
    args = parser.parse_args()
    print(build_pdf(args.output))


if __name__ == "__main__":
    main()
