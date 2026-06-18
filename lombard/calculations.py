from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP


GROSZE = Decimal("0.01")


def parse_money(value: str | int | float | Decimal) -> Decimal:
    """Parse a Polish money input such as "1 500,50" into Decimal."""
    if isinstance(value, Decimal):
        amount = value
    else:
        normalized = str(value).strip().replace(" ", "").replace(",", ".")
        amount = Decimal(normalized or "0")
    return amount.quantize(GROSZE, rounding=ROUND_HALF_UP)


def money_to_cents(value: str | int | float | Decimal) -> int:
    return int((parse_money(value) * 100).to_integral_value(rounding=ROUND_HALF_UP))


def cents_to_money(cents: int | None) -> Decimal:
    return (Decimal(cents or 0) / Decimal(100)).quantize(GROSZE)


def format_money(value: int | Decimal | None) -> str:
    amount = cents_to_money(value) if isinstance(value, int) or value is None else parse_money(value)
    return f"{amount:,.2f}".replace(",", " ").replace(".", ",") + " zł"


@dataclass(frozen=True)
class LoanCalculation:
    loan_amount_cents: int
    commission_amount_cents: int
    total_repayment_cents: int
    daily_increase_cents: int
    max_additional_fee_cents: int
    due_date: date
    additional_period_end: date
    sale_mode: str


def calculate_loan(
    *,
    issue_date: date,
    loan_amount_cents: int,
    term_days: int,
    commission_amount_cents: int | None = None,
    commission_rate_percent: Decimal = Decimal("10"),
) -> LoanCalculation:
    if term_days < 1:
        raise ValueError("Okres umowy musi wynosić co najmniej 1 dzień.")
    if loan_amount_cents <= 0:
        raise ValueError("Kwota pożyczki musi być większa od zera.")

    if commission_amount_cents is None:
        commission = (
            cents_to_money(loan_amount_cents)
            * commission_rate_percent
            / Decimal(100)
        ).quantize(GROSZE, rounding=ROUND_HALF_UP)
        commission_amount_cents = money_to_cents(commission)

    total_repayment_cents = loan_amount_cents + commission_amount_cents
    due_date = issue_date + timedelta(days=term_days - 1)
    additional_period_end = due_date + timedelta(days=30)

    total = cents_to_money(total_repayment_cents)
    daily_increase_cents = money_to_cents((total * Decimal("0.01")).quantize(GROSZE))
    max_additional_fee_cents = money_to_cents((total * Decimal("0.20")).quantize(GROSZE))
    sale_mode = "direct" if loan_amount_cents <= 50_000 else "auction"

    return LoanCalculation(
        loan_amount_cents=loan_amount_cents,
        commission_amount_cents=commission_amount_cents,
        total_repayment_cents=total_repayment_cents,
        daily_increase_cents=daily_increase_cents,
        max_additional_fee_cents=max_additional_fee_cents,
        due_date=due_date,
        additional_period_end=additional_period_end,
        sale_mode=sale_mode,
    )


def repayment_amount_on(
    *,
    base_total_cents: int,
    due_date: date,
    payment_date: date,
) -> int:
    if payment_date <= due_date:
        return base_total_cents

    days_after_due = min((payment_date - due_date).days, 20)
    daily = money_to_cents(cents_to_money(base_total_cents) * Decimal("0.01"))
    max_fee = money_to_cents(cents_to_money(base_total_cents) * Decimal("0.20"))
    return base_total_cents + min(days_after_due * daily, max_fee)


ONES = [
    "",
    "jeden",
    "dwa",
    "trzy",
    "cztery",
    "pięć",
    "sześć",
    "siedem",
    "osiem",
    "dziewięć",
]
TEENS = [
    "dziesięć",
    "jedenaście",
    "dwanaście",
    "trzynaście",
    "czternaście",
    "piętnaście",
    "szesnaście",
    "siedemnaście",
    "osiemnaście",
    "dziewiętnaście",
]
TENS = [
    "",
    "",
    "dwadzieścia",
    "trzydzieści",
    "czterdzieści",
    "pięćdziesiąt",
    "sześćdziesiąt",
    "siedemdziesiąt",
    "osiemdziesiąt",
    "dziewięćdziesiąt",
]
HUNDREDS = [
    "",
    "sto",
    "dwieście",
    "trzysta",
    "czterysta",
    "pięćset",
    "sześćset",
    "siedemset",
    "osiemset",
    "dziewięćset",
]
GROUP_FORMS = [
    ("", "", ""),
    ("tysiąc", "tysiące", "tysięcy"),
    ("milion", "miliony", "milionów"),
]


def _form_for_number(number: int, forms: tuple[str, str, str]) -> str:
    singular, plural_2_4, plural_other = forms
    if number == 1:
        return singular
    if 10 <= number % 100 <= 20:
        return plural_other
    if number % 10 in (2, 3, 4):
        return plural_2_4
    return plural_other


def _three_digits_to_words(number: int) -> str:
    words: list[str] = []
    words.append(HUNDREDS[number // 100])
    remainder = number % 100
    if 10 <= remainder <= 19:
        words.append(TEENS[remainder - 10])
    else:
        words.append(TENS[remainder // 10])
        words.append(ONES[remainder % 10])
    return " ".join(word for word in words if word)


def number_to_words(number: int) -> str:
    if number == 0:
        return "zero"
    if number < 0:
        return "minus " + number_to_words(abs(number))

    groups: list[str] = []
    group_index = 0
    while number:
        group_value = number % 1000
        if group_value:
            group_words = _three_digits_to_words(group_value)
            if group_index:
                group_form = _form_for_number(group_value, GROUP_FORMS[group_index])
                if group_value == 1:
                    group_words = group_form
                else:
                    group_words = f"{group_words} {group_form}"
            groups.append(group_words)
        number //= 1000
        group_index += 1

    return " ".join(reversed(groups))


def money_to_words(cents: int) -> str:
    zloty = cents // 100
    grosze = cents % 100
    zloty_form = _form_for_number(zloty, ("złoty", "złote", "złotych"))
    grosze_form = _form_for_number(grosze, ("grosz", "grosze", "groszy"))
    return f"{number_to_words(zloty)} {zloty_form} i {number_to_words(grosze)} {grosze_form}"
