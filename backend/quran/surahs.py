"""Surah names, so a question such as "تفسير سورة الإخلاص" becomes the filter surah = 112.

The names are plain facts (the traditional chapter titles). A name is only recognised after the
word "سورة", so ordinary words that are also surah names ("الحديد", "النور") stay ordinary.
"""

import re

SURAH_NAMES = (
    "الفاتحة البقرة آل_عمران النساء المائدة الأنعام الأعراف الأنفال التوبة يونس هود يوسف الرعد "
    "إبراهيم الحجر النحل الإسراء الكهف مريم طه الأنبياء الحج المؤمنون النور الفرقان الشعراء النمل "
    "القصص العنكبوت الروم لقمان السجدة الأحزاب سبأ فاطر يس الصافات ص الزمر غافر فصلت الشورى "
    "الزخرف الدخان الجاثية الأحقاف محمد الفتح الحجرات ق الذاريات الطور النجم القمر الرحمن "
    "الواقعة الحديد المجادلة الحشر الممتحنة الصف الجمعة المنافقون التغابن الطلاق التحريم الملك "
    "القلم الحاقة المعارج نوح الجن المزمل المدثر القيامة الإنسان المرسلات النبأ النازعات عبس "
    "التكوير الانفطار المطففين الانشقاق البروج الطارق الأعلى الغاشية الفجر البلد الشمس الليل "
    "الضحى الشرح التين العلق القدر البينة الزلزلة العاديات القارعة التكاثر العصر الهمزة الفيل "
    "قريش الماعون الكوثر الكافرون النصر المسد الإخلاص الفلق الناس"
).split()

_MARKS = re.compile("[ـً-ٰٟ]")
_ALEF = str.maketrans("أإآٱ", "اااا")


def _plain(text: str) -> str:
    return _MARKS.sub("", text).translate(_ALEF).replace("_", " ")


# longest names first so "آل عمران" is tried before shorter ones
_PATTERNS = sorted(
    ((_plain(name), number) for number, name in enumerate(SURAH_NAMES, start=1)),
    key=lambda item: -len(item[0]),
)


def find_surah(question: str) -> tuple[int, str] | None:
    """(number, the question without the "سورة <name>" phrase), or None when no surah is named."""
    text = _plain(question)
    for name, number in _PATTERNS:
        pattern = re.compile(
            rf"(?<!\w)[لب]?سورة\s+(?:ال)?{re.escape(name.removeprefix('ال'))}(?!\w)"
        )
        match = pattern.search(text)
        if match:
            return number, (text[: match.start()] + " " + text[match.end() :]).strip()
    return None
