from __future__ import annotations

import io
from pathlib import Path

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


def _checkbox(checked: bool) -> str:
    return "[X]" if checked else "[ ]"


def build_contract_pdf(contract: dict, photos: list[dict], upload_root: Path) -> io.BytesIO:
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

    client_address = ", ".join(
        part
        for part in [
            contract["street_address"],
            contract.get("postal_code"),
            contract["client_city"],
        ]
        if part
    )
    collateral_value = format_money(contract["collateral_value_cents"])
    valuation_basis = contract.get("valuation_basis") or (
        "informacji o ofertach sprzedaży podobnych rzeczy dostępnych w sieci Internet"
    )

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
            f"adres zamieszkania: {client_address}, Nr PESEL: {contract.get('pesel') or ''},",
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

    clauses = [
        "1. Przedmiotem umowy jest konsumencka pożyczka lombardowa („Pożyczka”), "
        "której Pożyczkodawca udziela Pożyczkobiorcy na warunkach określonych w niniejszej umowie.",
        f"2. Kwota Pożyczki: {format_money(contract['loan_amount_cents'])} "
        f"(słownie: {money_to_words(contract['loan_amount_cents'])}). Ustalona została na podstawie wniosku "
        "Pożyczkobiorcy oraz wartości przedmiotu zabezpieczenia lombardowego i wypłacona gotówką w dniu podpisania umowy.",
        f"3. Całkowity koszt konsumenckiej pożyczki lombardowej („Całkowity koszt Pożyczki”): "
        f"na który składają się 0 zł odsetki, {format_money(contract['commission_amount_cents'])} prowizja za udzielenie pożyczki.",
        f"4. Całkowita kwota do spłaty: {format_money(contract['total_repayment_cents'])} "
        f"(słownie: {money_to_words(contract['total_repayment_cents'])}) stanowi sumę kwoty pożyczki wraz z całkowitym kosztem pożyczki.",
        f"5. Okres na jaki zawierana jest umowa wynosi {contract['term_days']} dni, przy czym pierwszym dniem jest dzień podpisania umowy. "
        f"Termin zapłaty Całkowitej kwoty do spłaty upływa z końcem dnia {contract['due_date']}.",
        "6. Zapłata całkowitej kwoty do spłaty nastąpi gotówką w lokalu, w którym umowa została zawarta.",
        "7. Pożyczkobiorca uprawniony jest do zapłaty Całkowitej kwoty do spłaty lub jej części przed terminem wskazanym w pkt. 5 umowy. "
        "W przypadku wcześniejszej spłaty całkowity koszt pożyczki ulega proporcjonalnemu obniżeniu o koszty przypadające za skrócony okres.",
        f"8. W przypadku braku zapłaty w terminie Pożyczkobiorca może w ciągu kolejnych 30 dni, tj. do dnia {contract['additional_period_end']}, "
        "zapłacić niezapłaconą część Całkowitej kwoty do spłaty, powiększoną maksymalnie o 20% pozostającej niezapłaconej części. "
        f"Należność naliczana będzie w wysokości 1% dziennie, tj. {format_money(contract['daily_increase_cents'])}, przez okres pierwszych 20 dni.",
        "9. Zapłata Całkowitej kwoty do spłaty powoduje wygaśnięcie zabezpieczenia lombardowego i obowiązek zwrotu Pożyczkobiorcy przedmiotu zabezpieczenia lombardowego.",
        "10. Zabezpieczenie pożyczki lombardowej: Pożyczkobiorca zobowiązuje się do przeniesienia własności przedmiotu zabezpieczenia lombardowego "
        "na Pożyczkodawcę w przypadku braku zapłaty całkowitej kwoty do spłaty w terminie. Przeniesienie własności następuje pod warunkiem zawieszającym, "
        "którym jest niedokonanie przez Pożyczkobiorcę całkowitej spłaty pożyczki oraz jej kosztów.",
        f"11. Przedmiot zabezpieczenia lombardowego: {contract['collateral_type']} {contract['collateral_description']}, "
        f"którego zdjęcie dołączono do niniejszej umowy, o wartości szacunkowej {collateral_value} "
        f"(słownie: {money_to_words(contract['collateral_value_cents'])}) ustalonej na podstawie {valuation_basis}.",
        "Pożyczkobiorca oświadcza, że przedmiot zabezpieczenia jest wolny od wad prawnych, stanowi jego własność, nie pochodzi z przestępstwa, "
        "nie jest objęty postępowaniem egzekucyjnym, nie jest przedmiotem zastawu ani przewłaszczenia, a rozporządzenie nim nie podlega ograniczeniom.",
        "12. Pożyczkobiorca zatrzymuje zabezpieczone rzeczy w swoim władaniu w charakterze biorącego w użyczenie. "
        "Może używać ich wyłącznie zgodnie z właściwościami i przeznaczeniem, w sposób wykluczający utratę wartości ponad normalne zużycie.",
        "13. W przypadku niezapłacenia całości lub części Całkowitej kwoty do spłaty w ustalonym terminie i upływu dodatkowych 30 dni, "
        "przedmiot zabezpieczenia lombardowego zostanie przekazany do sprzedaży przez Pożyczkodawcę celem zaspokojenia wierzytelności.",
        "14. Sprzedaż przedmiotu zabezpieczenia lombardowego odbędzie się w trybie:<br/>"
        f"{_checkbox(contract['sale_mode'] == 'direct')} Przy pożyczkach do kwoty 500,00 zł - sprzedaży bezpośredniej<br/>"
        f"{_checkbox(contract['sale_mode'] == 'auction')} Przy pożyczkach od kwoty 500,01 zł - aukcji elektronicznej lub sprzedaży bezpośredniej po dwóch nieskutecznych aukcjach elektronicznych.",
        "15. Nadwyżka stanowiąca różnicę pomiędzy kwotą uzyskaną ze sprzedaży przedmiotu zabezpieczenia a niezapłaconą częścią całkowitej kwoty do spłaty, "
        "pomniejszona o 20% nadwyżki, zostanie zwrócona Pożyczkobiorcy w terminie 7 dni od otrzymania środków.",
        "16. Reklamacje można składać pisemnie w lokalu, ustnie do protokołu lub elektronicznie na adres lombard7@vp.pl. "
        "Reklamacja powinna zawierać dane składającego reklamację, opis nieprawidłowości oraz żądanie określonego zachowania.",
        "17. Podmiotem uprawnionym do prowadzenia postępowania w sprawie pozasądowego rozwiązywania sporów konsumenckich jest Rzecznik Finansowy (https://rf.gov.pl/).",
        "18. Zgodnie z RODO administratorem danych osobowych jest Lombard Paweł Kobierski Spółka z o.o. Dane będą przetwarzane w celu realizacji umowy "
        "i przechowywane przez okres wymagany przepisami prawa. Pożyczkobiorcy przysługują prawa dostępu, sprostowania, usunięcia lub ograniczenia przetwarzania danych.",
        "19. Zmiana umowy wymaga formy pisemnej pod rygorem nieważności. Pożyczkobiorca wyraża zgodę na przeniesienie praw i obowiązków Pożyczkodawcy wynikających z niniejszej umowy na osoby trzecie.",
    ]
    story.extend(_p(clause, normal) for clause in clauses)

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
            story.append(_p(photo.get("caption") or photo["original_filename"], normal))
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
