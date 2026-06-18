# Program do lombardu

Pierwsza wersja aplikacji webowej do obsługi lombardu:

- wspólna kartoteka klientów dla punktów Busko-Zdrój, Chmielnik i Pińczów,
- tworzenie umów pożyczki lombardowej z automatycznymi wyliczeniami,
- archiwum umów z filtrami,
- dodawanie zdjęć przedmiotu zabezpieczenia,
- generowanie PDF umowy na podstawie dostarczonego wzoru,
- rozliczenie spłaty i ewidencja CSV dla księgowej z filtrem punktu oraz zbiorczym oznaczaniem wysłanych umów,
- wybór aktywnego punktu pracy w nagłówku, z możliwością przełączenia na wspólny widok wszystkich lokalizacji,
- paczka ZIP dla księgowej zawierająca ewidencję CSV oraz PDF-y spłaconych umów gotowe do wysłania,
- automatyczne oznaczanie umów po terminie oraz blokady ponownego księgowania.

## Uruchomienie

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
flask --app app run
```

Po uruchomieniu otwórz `http://127.0.0.1:5000`.

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

- logowanie i uprawnienia pracowników dla każdego punktu,
- kopie zapasowe bazy i zdjęć,
- szyfrowanie/ochronę danych osobowych zgodnie z RODO,
- możliwość edycji danych klienta i korekt umowy,
- integrację mailową lub automatyczne wysyłanie ewidencji do księgowej.
