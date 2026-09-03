"""Portal'in belge kartlari, indirttikleri dosyalarla tutarli olmali.

Faz 9 envanteri su ucurumu buldu: kart "CAS-SRS-004 v4.0, 93 requirements"
diyordu, indirme dugmesi v4.2'yi veriyordu, dosyanin ici 94 requirement
sayiyordu. VCRM karti iki surum geride ve orani yanlisti ("92/93 verified",
dosya "115/115"). Bir degerlendirici karti okuyup dosyayi actiginda tutmayan
sayilar goruyordu -- icerik dogru olsa bile guven orada kayboluyor.

Bu test iki seyi bagliyor:
  * her indirme baglantisinin hedefi gercekten var mi
  * kartta yazan surum/dokuman kimligi, o dosyanin BASLIGINDA da yaziyor mu

Ne yapmiyor: dosyanin icerigini dogrulamiyor. Sadece kartin, linklediginin
kimligi hakkinda yalan soylemedigini kontrol ediyor -- bayatlayabilecek tek
sey bu esleme.

DOCX = zip; word/document.xml govdeyi tutar. python-docx bagimliligi
eklemektense standart kutuphane ile okunuyor.
"""
import os
import re
import zipfile

import pytest

_ROOT = os.environ.get("CAS_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PORTAL = os.path.join(_ROOT, "static", "portal.html")
_DOCS = os.path.join(_ROOT, "static", "docs")


def _portal():
    with open(_PORTAL, encoding="utf-8") as f:
        return f.read()


def _docx_head(path, paragraphs=40):
    """Ilk N paragrafin metni -- belge kimligi baslikta durur."""
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", "replace")
    out = []
    for p in re.findall(r"<w:p[ >].*?</w:p>", xml, re.S):
        runs = re.findall(r"<w:t[^>]*>(.*?)</w:t>", p, re.S)
        if runs:
            out.append(re.sub(r"\s+", " ", "".join(runs)).strip())
        if len(out) >= paragraphs:
            break
    return " ".join(out)


def _linked():
    """Kartlardaki /docs/... indirme baglantilari."""
    return sorted(set(re.findall(r'href="(/docs/[^"]+)"\s+download', _portal())))


def test_portal_links_at_least_five_documents():
    """Beklenen belge sayisi -- bir kart sessizce dusmus olmasin."""
    links = _linked()
    assert len(links) >= 5, "portal yalnizca %d belge sunuyor: %s" % (len(links), links)


@pytest.mark.parametrize("link", _linked())
def test_linked_document_exists(link):
    path = os.path.join(_ROOT, link.lstrip("/").replace("docs/", "static/docs/", 1))
    assert os.path.exists(path), (
        "portal %s baglantisini sunuyor ama dosya yok: %s\n"
        "Belgeyi archive/ altina tasidiysaniz karti da kaldirin." % (link, path))


# kart metninde gecen kimlik -> baglantidaki dosya
_IDENTITY = [
    ("CAS-SRS-004 v4.2", "CAS_SRS_v4.2.docx", "VERSION 4.2"),
    ("CAS-EVD-003 v3.0", "CAS_TRL5_Operational_Evidence_v3.0.docx", "VERSION 3.0"),
    ("CAS-VCRM-002 v2.2", "CAS_VCRM_v2.2.docx", "VERSION 2.2"),
]


@pytest.mark.parametrize("card_text,filename,header", _IDENTITY,
                         ids=[i[1] for i in _IDENTITY])
def test_card_identity_matches_document_header(card_text, filename, header):
    """Kartta yazan surum, dosyanin kendi basliginda da yazmali."""
    assert card_text in _portal(), (
        "kart metni '%s' portal.html'de yok -- kart guncellendiyse bu testi de "
        "guncelleyin" % card_text)
    path = os.path.join(_DOCS, filename)
    if not os.path.exists(path):
        pytest.skip("%s yok" % filename)
    head = _docx_head(path)
    assert header in head, (
        "kart '%s' diyor ama %s basliginda '%s' gecmiyor.\n"
        "Ilk 200 karakter: %s" % (card_text, filename, header, head[:200]))


def test_no_superseded_document_is_linked():
    """archive/ altina tasinmis bir belge portal'da sunulmamali."""
    archived = set()
    for root, _dirs, files in os.walk(os.path.join(_DOCS, "archive")):
        archived.update(files)
    if not archived:
        pytest.skip("archive/ bos")
    linked = {os.path.basename(l) for l in _linked()}
    overlap = sorted(linked & archived)
    assert not overlap, "portal arsivlenmis belge sunuyor: %s" % overlap
