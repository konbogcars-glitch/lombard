# Program do lombardu

Pierwsza wersja aplikacji webowej do obsługi lombardu:

- wspólna kartoteka klientów dla punktów Busko-Zdrój, Chmielnik i Pińczów,
- tworzenie umów pożyczki lombardowej z automatycznymi wyliczeniami,
- archiwum umów z filtrami,
- dodawanie zdjęć przedmiotu zabezpieczenia,
- generowanie PDF umowy na podstawie dostarczonego wzoru,
- edycja szablonu treści umowy w aplikacji lub import z pliku TXT/MD z automatycznymi polami, np. `{client_name}`, `{loan_amount}` i `{due_date}`,
- korekta aktywnych i przeterminowanych umów przed rozliczeniem, z ponownym przeliczeniem terminów oraz kwot,
- rozliczenie spłaty albo sprzedaży zastawu, z ewidencją CSV dla księgowej, filtrem punktu i okresu oraz zbiorczym oznaczaniem wysłanych umów,
- zapis kontaktu do księgowej i przygotowanie gotowego e-maila z opisem paczki do wysłania,
- wybór aktywnego punktu pracy w nagłówku, z możliwością przełączenia na wspólny widok wszystkich lokalizacji,
- paczka ZIP dla księgowej zawierająca ewidencję CSV oraz PDF-y spłaconych umów gotowe do wysłania,
- archiwum paczek księgowych pozwalające ponownie pobrać dokładny CSV/ZIP wysłany do księgowej,
- automatyczne oznaczanie umów po terminie oraz blokady ponownego księgowania.

## Uruchomienie

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
flask --app app run
```

Po uruchomieniu otwórz `http://127.0.0.1:5000`.

## Logowanie i punkty

Przy pierwszym uruchomieniu aplikacja zakłada konta startowe:

- `admin` / `admin123` - pełny dostęp do wszystkich punktów i ustawień,
- `busko`, `chmielnik`, `pinczow` / `lombard123` - dostęp do przypisanego punktu.

Przed utworzeniem produkcyjnej bazy ustaw własne hasła zmiennymi środowiskowymi:

```bash
export LOMBARD_ADMIN_PASSWORD="mocne-haslo-administratora"
export LOMBARD_BRANCH_PASSWORD="mocne-haslo-punktow"
```

Administrator może przełączać widok między wszystkimi lokalizacjami, a konto punktu
pracuje wyłącznie na swoim oddziale. Kartoteka klientów pozostaje wspólna.

## Dane aplikacji

Domyślnie aplikacja zapisuje bazę SQLite i zdjęcia w katalogu `instance/`:

- `instance/lombard.sqlite3` - baza danych,
- `instance/uploads/` - zdjęcia do umów.

Katalog `instance/` nie powinien być commitowany do repozytorium.

## Testy

```bash
python3 -m unittest discover -s tests
```

## Zakres MVP

To jest działający fundament systemu. Przed produkcyjnym wdrożeniem warto dodać:

- kopie zapasowe bazy i zdjęć,
- szyfrowanie/ochronę danych osobowych zgodnie z RODO,
- pełną integrację SMTP lub API poczty do automatycznego wysyłania ewidencji do księgowej z poziomu serwera.
