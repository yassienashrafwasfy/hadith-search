import sqlite3

import pytest

from quran import loader
from quran.types import Place

TANZIL = (
    "# comment\n1|1|بِسْمِ ٱللَّهِ ٱلرَّحْمَٰنِ ٱلرَّحِيمِ\n1|2|ٱلْحَمْدُ لِلَّهِ\n\n2|1|الٓمٓ\n"
)
XML = """<quran-data><suras>
<sura index="1" type="Meccan"/><sura index="2" type="Medinan"/></suras>
<juzs><juz index="1" sura="1" aya="1"/><juz index="2" sura="2" aya="1"/></juzs></quran-data>"""


def test_parse_tanzil_is_byte_exact(tmp_path):
    f = tmp_path / "q.txt"
    f.write_bytes(TANZIL.encode())
    ayat = loader.parse_tanzil(f)
    assert [(a.surah, a.ayah) for a in ayat] == [(1, 1), (1, 2), (2, 1)]
    assert ayat[0].text == "بِسْمِ ٱللَّهِ ٱلرَّحْمَٰنِ ٱلرَّحِيمِ"
    assert ayat[0].text.encode() == "بِسْمِ ٱللَّهِ ٱلرَّحْمَٰنِ ٱلرَّحِيمِ".encode()


def test_parse_tanzil_crlf_and_bad_lines(tmp_path):
    f = tmp_path / "q.txt"
    f.write_bytes(b"1|1|a\r\n")
    assert loader.parse_tanzil(f)[0].text == "a"
    f.write_bytes(b"x|1|a\n")
    with pytest.raises(loader.LoaderError):
        loader.parse_tanzil(f)
    f.write_bytes(b"1|1\n")
    with pytest.raises(loader.LoaderError):
        loader.parse_tanzil(f)


def test_metadata(tmp_path):
    f = tmp_path / "m.xml"
    f.write_text(XML)
    meta = loader.parse_metadata(f)
    assert meta.place_of(1) is Place.MECCAN and meta.place_of(2) is Place.MEDINAN
    assert meta.place_of(9) is None
    assert meta.juz_of(1, 7) == 1 and meta.juz_of(2, 1) == 2


def _tafsir_db(path, rows):
    con = sqlite3.connect(path)
    con.execute("create table tafsir (sura integer, aya integer, text text)")
    con.executemany("insert into tafsir values (?,?,?)", rows)
    con.commit()
    con.close()


def test_parse_tafsir(tmp_path):
    db = tmp_path / "t.db"
    _tafsir_db(db, [(1, 2, "ب"), (1, 1, "أ"), (1, 3, "")])
    got = loader.parse_tafsir(db)
    assert got == [loader.RawTafsir(1, 1, "أ"), loader.RawTafsir(1, 2, "ب")]
    with pytest.raises(loader.LoaderError):
        loader.parse_tafsir(db, table="nope")


def test_rows_carry_metadata(tmp_path):
    m = tmp_path / "m.xml"
    m.write_text(XML)
    meta = loader.parse_metadata(m)
    rows = loader.quran_rows([loader.RawAyah(2, 1, "x")], meta)
    assert rows[0]["juz"] == 2 and rows[0]["place"] == "medinan"
    assert rows[0]["search_text"] == "x"
    assert rows[0]["level"] == "ayah" and rows[0]["ayah_from"] == rows[0]["ayah_to"] == 1
    t = loader.tafsir_rows([loader.RawTafsir(1, 1, "y")], "Ibn Kathir")
    assert t[0]["author"] == "Ibn Kathir" and t[0]["juz"] is None


def test_insert_rows_batches():
    calls = []

    class S:
        def execute(self, stmt, rows):
            calls.append(len(rows))

    from sqlalchemy import Column, Integer, MetaData, Table

    t = Table("x", MetaData(), Column("id", Integer, primary_key=True))
    assert loader.insert_rows(S(), t, [{}] * 5, batch=2) == 5
    assert calls == [2, 2, 1]


def test_write_licenses_keeps_tanzil_notice(tmp_path):
    path = loader.write_licenses(tmp_path / "d", loader.default_licenses(True))
    text = path.read_text("utf-8")
    assert "tanzil.net" in text and "CHANGING IT IS" in text and "tafsir.net" in text


def test_load_into_postgres(_quran_empty_session, _quran_sources, tmp_path):
    from sqlalchemy import select

    from quran.db import create_source
    from quran.models import node_table

    session = _quran_empty_session
    create_source(session, _quran_sources["quran"])
    (tmp_path / "q.txt").write_bytes(TANZIL.encode())
    (tmp_path / "m.xml").write_text(XML)
    table = node_table("quran")
    n = loader.load_quran(session, table, tmp_path / "q.txt", tmp_path / "m.xml")
    session.commit()
    assert n == 3
    rows = session.execute(select(table).order_by(table.c.surah, table.c.ayah_from)).all()
    assert rows[0].text == "بِسْمِ ٱللَّهِ ٱلرَّحْمَٰنِ ٱلرَّحِيمِ"
    assert (rows[2].surah, rows[2].juz, rows[2].place) == (2, 2, "medinan")


def test_load_tafsir_takes_the_sqlite_table_name_apart_from_the_node_table(tmp_path, monkeypatch):
    import sqlite3

    db = tmp_path / "t.db"
    con = sqlite3.connect(db)
    con.execute("create table tafsir_x (sura integer, aya integer, tafsir text)")
    con.execute("insert into tafsir_x values (1, 1, 'ا')")
    con.commit()
    con.close()
    seen = {}
    monkeypatch.setattr(
        loader,
        "insert_rows",
        lambda session, table, rows: seen.update(t=table, r=rows) or len(rows),
    )

    count = loader.load_tafsir(None, "nodes", db, "A", sqlite_table="tafsir_x", text_col="tafsir")

    assert count == 1 and seen["t"] == "nodes" and seen["r"][0]["text"] == "ا"
