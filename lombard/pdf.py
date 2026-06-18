from __future__ import annotations

import io
import re
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .calculations import format_money, money_to_words


COMPANY_LINE = (
    "Lombard Paweł Kobierski Sp. z o.o. z siedzibą w Busko-Zdrój, "
    "ul. Wojska Polskiego 3. NIP: 6551988849, KRS: 0001110328, "
    "REGON: 528867150, RDL: 000153"
)


def _register_fonts() -> tuple[str, str]:
    regular = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    bold = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    if Path(regular).exists() and Path(bold).exists():
        pdfmetrics.registerFont(TTFont("DejaVuSans", regular))
        pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", bold))
        return "DejaVuSans", "DejaVuSans-Bold"
    return "Helvetica", "Helvetica-Bold"


def _p(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(text.replace("\n", "<br/>"), style)


def _plain_p(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(text).replace("\n", "<br/>"), style)


def _checkbox(checked: bool) -> str:
    return "[X]" if checked else "[ ]"


CONTRACT_TEMPLATE_PLACEHOLDERS = [
    ("contract_number", "numer umowy"),
    ("branch_city", "miasto punktu"),
    ("branch_address", "adres punktu"),
    ("issue_date", "data zawarcia umowy"),
    ("client_name", "imię i nazwisko klienta"),
    ("client_address", "adres klienta"),
    ("pesel", "PESEL klienta"),
    ("document_type", "typ dokumentu"),
    ("document_number", "numer dokumentu"),
    ("company_line", "pełne dane pożyczkodawcy"),
    ("loan_amount", "kwota pożyczki"),
    ("loan_amount_words", "kwota pożyczki słownie"),
    ("commission_amount", "prowizja"),
    ("total_repayment", "całkowita kwota do spłaty"),
    ("total_repayment_words", "całkowita kwota do spłaty słownie"),
    ("term_days", "okres umowy w dniach"),
    ("due_date", "termin spłaty"),
    ("additional_period_end", "koniec dodatkowych 30 dni"),
    ("daily_increase", "dzienna opłata po terminie"),
    ("max_additional_fee", "limit opłat po terminie"),
    ("collateral_type", "rodzaj zabezpieczenia"),
    ("collateral_description", "opis zabezpieczenia"),
    ("collateral_value", "wartość zabezpieczenia"),
    ("collateral_value_words", "wartość zabezpieczenia słownie"),
    ("valuation_basis", "podstawa wyceny"),
    ("sale_direct_checkbox", "zaznaczenie sprzedaży bezpośredniej"),
    ("sale_auction_checkbox", "zaznaczenie aukcji/sprzedaży po aukcjach"),
]


DEFAULT_CONTRACT_TEMPLATE = """1. Przedmiotem umowy jest konsumencka pożyczka lombardowa („Pożyczka”), której Pożyczkodawca udziela Pożyczkobiorcy na warunkach określonych w niniejszej umowie.

2. Kwota Pożyczki: {loan_amount} (słownie: {loan_amount_words}). Ustalona została na podstawie wniosku Pożyczkobiorcy oraz wartości przedmiotu zabezpieczenia lombardowego i wypłacona gotówką w dniu podpisania umowy.

3. Całkowity koszt konsumenckiej pożyczki lombardowej („Całkowity koszt Pożyczki”): na który składają się 0 zł odsetki, {commission_amount} prowizja za udzielenie pożyczki.

4. Całkowita kwota do spłaty: {total_repayment} (słownie: {total_repayment_words}) stanowi sumę kwoty pożyczki wraz z całkowitym kosztem pożyczki.

5. Okres na jaki zawierana jest umowa wynosi {term_days} dni, przy czym pierwszym dniem jest dzień podpisania umowy. Termin zapłaty Całkowitej kwoty do spłaty upływa z końcem dnia {due_date}.

6. Zapłata całkowitej kwoty do spłaty nastąpi gotówką w lokalu, w którym umowa została zawarta.

7. Pożyczkobiorca uprawniony jest do zapłaty Całkowitej kwoty do spłaty lub jej części przed terminem wskazanym w pkt. 5 umowy. W przypadku wcześniejszej spłaty całkowity koszt pożyczki ulega proporcjonalnemu obniżeniu o koszty przypadające za skrócony okres.

8. W przypadku braku zapłaty w terminie Pożyczkobiorca może w ciągu kolejnych 30 dni, tj. do dnia {additional_period_end}, zapłacić niezapłaconą część Całkowitej kwoty do spłaty, powiększoną maksymalnie o 20% pozostającej niezapłaconej części. Należność naliczana będzie w wysokości 1% dziennie, tj. {daily_increase}, przez okres pierwszych 20 dni.

9. Zapłata Całkowitej kwoty do spłaty powoduje wygaśnięcie zabezpieczenia lombardowego i obowiązek zwrotu Pożyczkobiorcy przedmiotu zabezpieczenia lombardowego.

10. Zabezpieczenie pożyczki lombardowej: Pożyczkobiorca zobowiązuje się do przeniesienia własności przedmiotu zabezpieczenia lombardowego na Pożyczkodawcę w przypadku braku zapłaty całkowitej kwoty do spłaty w terminie. Przeniesienie własności następuje pod warunkiem zawieszającym, którym jest niedokonanie przez Pożyczkobiorcę całkowitej spłaty pożyczki oraz jej kosztów.

11. Przedmiot zabezpieczenia lombardowego: {collateral_type} {collateral_description}, którego zdjęcie dołączono do niniejszej umowy, o wartości szacunkowej {collateral_value} (słownie: {collateral_value_words}) ustalonej na podstawie {valuation_basis}. Pożyczkobiorca oświadcza, że przedmiot zabezpieczenia jest wolny od wad prawnych, stanowi jego własność, nie pochodzi z przestępstwa, nie jest objęty postępowaniem egzekucyjnym, nie jest przedmiotem zastawu ani przewłaszczenia, a rozporządzenie nim nie podlega ograniczeniom.

12. Pożyczkobiorca zatrzymuje zabezpieczone rzeczy w swoim władaniu w charakterze biorącego w użyczenie. Może używać ich wyłącznie zgodnie z właściwościami i przeznaczeniem, w sposób wykluczający utratę wartości ponad normalne zużycie.

13. W przypadku niezapłacenia całości lub części Całkowitej kwoty do spłaty w ustalonym terminie i upływu dodatkowych 30 dni, przedmiot zabezpieczenia lombardowego zostanie przekazany do sprzedaży przez Pożyczkodawcę celem zaspokojenia wierzytelności.

14. Sprzedaż przedmiotu zabezpieczenia lombardowego odbędzie się w trybie:
{sale_direct_checkbox} Przy pożyczkach do kwoty 500,00 zł - sprzedaży bezpośredniej
{sale_auction_checkbox} Przy pożyczkach od kwoty 500,01 zł - aukcji elektronicznej lub sprzedaży bezpośredniej po dwóch nieskutecznych aukcjach elektronicznych.

15. Nadwyżka stanowiąca różnicę pomiędzy kwotą uzyskaną ze sprzedaży przedmiotu zabezpieczenia a niezapłaconą częścią całkowitej kwoty do spłaty, pomniejszona o 20% nadwyżki, zostanie zwrócona Pożyczkobiorcy w terminie 7 dni od otrzymania środków.

16. Reklamacje można składać pisemnie w lokalu, ustnie do protokołu lub elektronicznie na adres lombard7@vp.pl. Reklamacja powinna zawierać dane składającego reklamację, opis nieprawidłowości oraz żądanie określonego zachowania.

17. Podmiotem uprawnionym do prowadzenia postępowania w sprawie pozasądowego rozwiązywania sporów konsumenckich jest Rzecznik Finansowy (https://rf.gov.pl/).

18. Zgodnie z RODO administratorem danych osobowych jest Lombard Paweł Kobierski Spółka z o.o. Dane będą przetwarzane w celu realizacji umowy i przechowywane przez okres wymagany przepisami prawa. Pożyczkobiorcy przysługują prawa dostępu, sprostowania, usunięcia lub ograniczenia przetwarzania danych.

19. Zmiana umowy wymaga formy pisemnej pod rygorem nieważności. Pożyczkobiorca wyraża zgodę na przeniesienie praw i obowiązków Pożyczkodawcy wynikających z niniejszej umowy na osoby trzecie."""


_PLACEHOLDER_PATTERN = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def _contract_template_context(contract: dict) -> dict[str, str]:
    client_address = ", ".join(
        part
        for part in [
            contract["street_address"],
            contract.get("postal_code"),
            contract["client_city"],
        ]
        if part
    )
    valuation_basis = contract.get("valuation_basis") or (
        "informacji o ofertach sprzedaży podobnych rzeczy dostępnych w sieci Internet"
    )
    return {
        "contract_number": contract["contract_number"],
        "branch_city": contract["branch_city"],
        "branch_address": contract["branch_address"],
        "issue_date": contract["issue_date"],
        "client_name": f"{contract['first_name']} {contract['last_name']}",
        "client_address": client_address,
        "pesel": contract.get("pesel") or "",
        "document_type": contract["document_type"],
        "document_number": contract.get("document_number") or "",
        "company_line": COMPANY_LINE,
        "loan_amount": format_money(contract["loan_amount_cents"]),
        "loan_amount_words": money_to_words(contract["loan_amount_cents"]),
        "commission_amount": format_money(contract["commission_amount_cents"]),
        "total_repayment": format_money(contract["total_repayment_cents"]),
        "total_repayment_words": money_to_words(contract["total_repayment_cents"]),
        "term_days": str(contract["term_days"]),
        "due_date": contract["due_date"],
        "additional_period_end": contract["additional_period_end"],
        "daily_increase": format_money(contract["daily_increase_cents"]),
        "max_additional_fee": format_money(contract["max_additional_fee_cents"]),
        "collateral_type": contract["collateral_type"],
        "collateral_description": contract["collateral_description"],
        "collateral_value": format_money(contract["collateral_value_cents"]),
        "collateral_value_words": money_to_words(contract["collateral_value_cents"]),
        "valuation_basis": valuation_basis,
        "sale_direct_checkbox": _checkbox(contract["sale_mode"] == "direct"),
        "sale_auction_checkbox": _checkbox(contract["sale_mode"] == "auction"),
    }


def render_contract_template(contract: dict, template_text: str | None = None) -> str:
    context = _contract_template_context(contract)
    source = template_text or DEFAULT_CONTRACT_TEMPLATE

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        return context.get(key, match.group(0))

    return _PLACEHOLDER_PATTERN.sub(replace, source)


def _template_paragraphs(text: str) -> list[str]:
    return [
        paragraph.strip()
        for paragraph in re.split(r"\n\s*\n", text.strip())
        if paragraph.strip()
    ]


def build_contract_pdf(
    contract: dict,
    photos: list[dict],
    upload_root: Path,
    *,
    template_text: str | None = None,
) -> io.BytesIO:
    regular_font, bold_font = _register_fonts()
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=1.5 * cm,
        leftMargin=1.5 * cm,
        topMargin=1.2 * cm,
        bottomMargin=1.2 * cm,
        title=f"Umowa {contract['contract_number']}",
    )

    styles = getSampleStyleSheet()
    normal = ParagraphStyle(
        "ContractNormal",
        parent=styles["Normal"],
        fontName=regular_font,
        fontSize=8.7,
        leading=11,
        spaceAfter=4,
    )
    title = ParagraphStyle(
        "ContractTitle",
        parent=normal,
        fontName=bold_font,
        fontSize=12,
        leading=14,
        alignment=TA_CENTER,
        spaceAfter=8,
    )
    small = ParagraphStyle("Small", parent=normal, fontSize=7.5, leading=9)

    context = _contract_template_context(contract)

    story: list = [
        _p("UMOWA KONSUMENCKIEJ POŻYCZKI LOMBARDOWEJ", title),
        _p(f"Nr umowy: <b>{contract['contract_number']}</b>", normal),
        _p(
            f"Zawarta w {contract['branch_city']}, {contract['branch_address']} "
            f"dnia {contract['issue_date']} pomiędzy:",
            normal,
        ),
        _p(
            f"Konsumentem <b>{contract['first_name']} {contract['last_name']}</b>, "
            f"adres zamieszkania: {context['client_address']}, Nr PESEL: {contract.get('pesel') or ''},",
            normal,
        ),
        _p(
            f"legitymującym się: {contract['document_type']}, "
            f"Numer: {contract.get('document_number') or ''}, zwanym dalej Pożyczkobiorcą,",
            normal,
        ),
        _p(f"a {COMPANY_LINE}, zwanym dalej Pożyczkodawcą.", normal),
        Spacer(1, 4),
    ]

    rendered_template = render_contract_template(contract, template_text)
    story.extend(_plain_p(clause, normal) for clause in _template_paragraphs(rendered_template))

    story.extend(
        [
            Spacer(1, 8),
            _p("[ ] Pożyczkobiorca potwierdza odbiór formularza informacyjnego.", normal),
            _p("[ ] Pożyczkobiorca oświadcza, że zna treść formularza informacyjnego i rezygnuje z jego wydruku.", normal),
            _p(f"Kwituję odbiór pożyczki w wysokości {format_money(contract['loan_amount_cents'])}", normal),
            Spacer(1, 18),
            Table(
                [
                    ["........................................................", "........................................................"],
                    ["Podpis Pożyczkobiorcy", "Podpis Pożyczkodawcy"],
                ],
                colWidths=[8 * cm, 8 * cm],
                style=TableStyle(
                    [
                        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                        ("FONTNAME", (0, 0), (-1, -1), regular_font),
                        ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ]
                ),
            ),
            Spacer(1, 10),
            _p(
                f"Całkowita kwota do spłaty w terminie do końca: {contract['due_date']}: "
                f"{format_money(contract['total_repayment_cents'])}",
                small,
            ),
            _p(
                f"Zwiększenie całkowitej kwoty do spłaty za każdy rozpoczęty dzień po {contract['due_date']}: "
                f"{format_money(contract['daily_increase_cents'])}",
                small,
            ),
            _p(
                f"Maksymalna wysokość wszystkich naliczonych opłat: {format_money(contract['max_additional_fee_cents'])}",
                small,
            ),
        ]
    )

    if photos:
        story.append(PageBreak())
        story.append(_p("Załącznik: zdjęcia przedmiotu zabezpieczenia", title))
        for photo in photos:
            path = upload_root / str(contract["id"]) / photo["stored_filename"]
            story.append(_plain_p(photo.get("caption") or photo["original_filename"], normal))
            try:
                story.append(Image(str(path), width=15 * cm, height=10 * cm, kind="proportional"))
            except Exception:
                story.append(_p(f"Nie można osadzić zdjęcia w PDF: {photo['original_filename']}", small))
            story.append(Spacer(1, 8))

    def add_page_number(canvas, doc):
        canvas.saveState()
        canvas.setFont(regular_font, 7)
        canvas.setFillColor(colors.grey)
        canvas.drawRightString(19.5 * cm, 0.7 * cm, f"Strona {doc.page}")
        canvas.restoreState()

    document.build(story, onFirstPage=add_page_number, onLaterPages=add_page_number)
    buffer.seek(0)
    return buffer
