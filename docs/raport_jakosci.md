# Raport jakości ekstrakcji – baseline_reguly

Data: 2026-11-21T12:51:44 · tryb porównania: `strict` · dokumentów: 180

**Micro F1: 0.9931** (F1 z nullami: 0.9939), macro F1 po polach: 0.9672, dokumenty w 100% poprawne: 168/180

| Typ dokumentu | P | R | F1 | F1+null |
|---|---|---|---|---|
| odwolanie | 0.999 | 1.000 | 1.000 | 1.000 |
| pelnomocnictwo | 1.000 | 0.959 | 0.979 | 0.985 |
| przelew | 0.992 | 0.986 | 0.989 | 0.989 |

| Pole | TP | FP | FN | P | R | F1 |
|---|---|---|---|---|---|---|
| `odwolanie:OdwolanieKIO.czynnosc_dzien` | 60 | 0 | 0 | 1.00 | 1.00 | 1.000 |
| `odwolanie:OdwolanieKIO.czynnosc_opis` | 60 | 0 | 0 | 1.00 | 1.00 | 1.000 |
| `odwolanie:OdwolanieKIO.czynnosc_powod_id` | 60 | 0 | 0 | 1.00 | 1.00 | 1.000 |
| `odwolanie:OdwolanieKIO.data` | 45 | 0 | 0 | 1.00 | 1.00 | 1.000 |
| `odwolanie:OdwolanieKIO.miejscowosc` | 45 | 0 | 0 | 1.00 | 1.00 | 1.000 |
| `odwolanie:Odwolujacy[].krs` | 56 | 1 | 0 | 0.98 | 1.00 | 0.991 |
| `odwolanie:Odwolujacy[].nazwa` | 60 | 0 | 0 | 1.00 | 1.00 | 1.000 |
| `odwolanie:Odwolujacy[].nip` | 60 | 0 | 0 | 1.00 | 1.00 | 1.000 |
| `odwolanie:Odwolujacy[].pelnomocnik.imie_nazwisko` | 60 | 0 | 0 | 1.00 | 1.00 | 1.000 |
| `odwolanie:Odwolujacy[].pelnomocnik.mail` | 60 | 0 | 0 | 1.00 | 1.00 | 1.000 |
| `odwolanie:Odwolujacy[].pelnomocnik.nazwa` | 60 | 0 | 0 | 1.00 | 1.00 | 1.000 |
| `odwolanie:Odwolujacy[].przez-pelnomocnika` | 60 | 0 | 0 | 1.00 | 1.00 | 1.000 |
| `odwolanie:Odwolujacy[].regon` | 60 | 0 | 0 | 1.00 | 1.00 | 1.000 |
| `odwolanie:Zamawiajacy[].krs` | 58 | 0 | 0 | 1.00 | 1.00 | 1.000 |
| `odwolanie:Zamawiajacy[].nazwa` | 63 | 0 | 0 | 1.00 | 1.00 | 1.000 |
| `odwolanie:Zamawiajacy[].nip` | 63 | 0 | 0 | 1.00 | 1.00 | 1.000 |
| `odwolanie:Zamawiajacy[].prowadzacy_postepowanie` | 6 | 0 | 0 | 1.00 | 1.00 | 1.000 |
| `odwolanie:Zamawiajacy[].regon` | 63 | 0 | 0 | 1.00 | 1.00 | 1.000 |
| `odwolanie:ZamowieniePubliczne.numer_bzp` | 48 | 0 | 0 | 1.00 | 1.00 | 1.000 |
| `odwolanie:ZamowieniePubliczne.numer_postepowania` | 60 | 0 | 0 | 1.00 | 1.00 | 1.000 |
| `odwolanie:ZamowieniePubliczne.numer_ted` | 47 | 0 | 0 | 1.00 | 1.00 | 1.000 |
| `odwolanie:ZamowieniePubliczne.tytul` | 60 | 0 | 0 | 1.00 | 1.00 | 1.000 |
| `pelnomocnictwo:mocodawca[].data.imie_nazwisko` | 4 | 0 | 0 | 1.00 | 1.00 | 1.000 |
| `pelnomocnictwo:mocodawca[].data.krs` | 62 | 0 | 0 | 1.00 | 1.00 | 1.000 |
| `pelnomocnictwo:mocodawca[].data.nazwa` | 66 | 0 | 0 | 1.00 | 1.00 | 1.000 |
| `pelnomocnictwo:mocodawca[].data.nip` | 66 | 0 | 0 | 1.00 | 1.00 | 1.000 |
| `pelnomocnictwo:mocodawca[].data.typ` | 66 | 0 | 0 | 1.00 | 1.00 | 1.000 |
| `pelnomocnictwo:mocodawca[].podpisujacy[].imie_nazwisko` | 77 | 0 | 18 | 1.00 | 0.81 | 0.895 |
| `pelnomocnictwo:pelnomocnicy[].imie_nazwisko` | 98 | 0 | 0 | 1.00 | 1.00 | 1.000 |
| `pelnomocnictwo:pelnomocnicy[].pesel` | 0 | 0 | 1 | 0.00 | 0.00 | 0.000 |
| `przelew:przelew_bankowy.data_operacji` | 59 | 0 | 1 | 1.00 | 0.98 | 0.992 |
| `przelew:przelew_bankowy.kwota` | 59 | 0 | 1 | 1.00 | 0.98 | 0.992 |
| `przelew:przelew_bankowy.na_rachunek` | 60 | 0 | 0 | 1.00 | 1.00 | 1.000 |
| `przelew:przelew_bankowy.numer_transakcji` | 59 | 1 | 1 | 0.98 | 0.98 | 0.983 |
| `przelew:przelew_bankowy.tytul` | 58 | 2 | 2 | 0.97 | 0.97 | 0.967 |
| `przelew:przelew_bankowy.z_rachunku` | 60 | 0 | 0 | 1.00 | 1.00 | 1.000 |
