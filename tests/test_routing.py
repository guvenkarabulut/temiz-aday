import pytest

from app.routing import mask_pii, parse_reply


def test_mask_pii():
    t = mask_pii("Ali Veli ali@x.com 0532 123 45 67 TC 12345678901 https://linkedin.com/in/ali")
    assert "ali@x.com" not in t and "0532" not in t and "12345678901" not in t and "linkedin" not in t


def test_parse_reply_validates():
    assert parse_reply('Tamam: {"puan": 82, "ozet": "İyi."}') == (82, "İyi.")
    for bad in ["metin", '{"puan": 120, "ozet": "x"}', '{"puan": "a", "ozet": "x"}', '{"ozet": "x"}']:
        with pytest.raises(Exception):
            parse_reply(bad)


def test_mask_name_heuristic():
    from app.routing import mask_name
    assert mask_name("Ayşe Yılmaz\nBackend mühendisi, Go ve PostgreSQL").startswith("[ad]\n")
    assert "Mehmet" not in mask_name("Ad Soyad: Mehmet Kaya\nGo geliştirici")
    # unvan satırı ad sanılmamalı
    assert mask_name("Kıdemli Backend Mühendisi\nGo").startswith("Kıdemli Backend Mühendisi")
    # yalnızca ilk dolu satır
    assert "Veli Demir" in mask_name("Backend geliştirici\nReferans: Veli Demir")
