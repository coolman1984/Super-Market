"""Documented connection families, not claims of physical certification.

No vendor SDK, device probing or barcode-programming commands are sent.
Model names identify setup paths; actual settings come from a sticker or the
device's configured interface. References: docs/EGYPT_HARDWARE.md.
"""

PROFILES = (
    ("zebra", "scanner", "زيبرا: إل إس ٢٢٠٨، دي إس ٢٢٠٨، دي إس ٩٣٠٨", "Zebra LS2208 / DS2208 / DS9308", "keyboard"),
    ("honeywell", "scanner", "هني ويل: فوياجر ١٤٧٠ و١٤٧٢، زينون", "Honeywell Voyager 1470g / 1472g / Xenon", "keyboard"),
    ("datalogic", "scanner", "داتالوجيك: كويك سكان، ماجلان", "Datalogic QuickScan / Magellan", "keyboard"),
    ("newland", "scanner", "نيولاند: إتش آر ٢٢، إتش آر ٣٢، بي إس ٨٠٦٠", "Newland HR22 / HR32 / BS8060", "keyboard"),
    ("generic_hid", "scanner", "ماسح آخر يكتب مثل لوحة المفاتيح", "Other keyboard-wedge scanner", "keyboard"),
    ("rongta", "label", "رونجتا: آر إل إس ١٠٠٠ و١١٠٠", "Rongta RLS1000 / RLS1100", "label"),
    ("cas", "label", "كاس: سي إل ٣٠٠٠ و٥٠٠٠", "CAS CL3000 / CL5000", "label"),
    ("upos", "label", "يو بوس: ٢٠٠٠ و٥٠٠٠", "U.POS 2000 / UP-5000", "label"),
    ("digi", "label", "ديجي: إس إم ١٠٠", "DIGI SM-100", "label"),
    ("aclas", "label", "أكلاس: إل إس ٢", "Aclas LS2", "label"),
    ("generic_label", "label", "ميزان آخر يطبع ملصق باركود", "Other barcode-label scale", "label"),
    ("text_serial", "live", "ميزان يرسل الوزن كتابة عبر الكابل", "Text-output serial scale", "serial"),
)


def profiles():
    return [dict(id=id_, family=family, name_ar=ar, name_en=en, mode=mode,
                 hardware_verified=False) for id_, family, ar, en, mode in PROFILES]
