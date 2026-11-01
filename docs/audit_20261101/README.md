# إعادة تشغيل فحص ١ نوفمبر ٢٠٢٦

النسخة المختبرة: `e15fce3adf94645172b10bc55f2a7fbbd780366f`.
التقرير: [تقرير الفحص](../AGGRESSIVE_AUDIT_20261101.md).

بيئة التنفيذ: لينكس، بايثون ٣، نود، وبلاي رايت 1.62.1 مع Chromium headless shell 1234. لم تتغير اعتماديات تشغيل البرنامج. تحتاج اختبارات المتصفح إلى تثبيت متصفح بلاي رايت في بيئة التطوير أو ضبط `PLAYWRIGHT_BROWSERS_PATH`.

تشغّل الأوامر من جذر المستودع:

```sh
python tools/ci_tests.py
npm test --prefix cloud
node tests/ui/audit_ui.js
sh tests/ui/run.sh /tmp/sm-existing-ui
python docs/audit_20261101/stress_domain.py /tmp/sm-domain.json
python docs/audit_20261101/cloud_roundtrip.py /tmp/sm-cloud.json
```

المجسّات المستقلة تسجل مشاهدات؛ راجع `passed` والقيم الفعلية، ولا تعتمد على حالة خروج العملية وحدها بوصفها بوابة قبول للإصلاح.

لتشغيل فحص الصفحات على **قاعدة تدريب مؤقتة جديدة**، لا تستخدم قاعدة محل أو منفذ خدمة حية:

```sh
python -m server.app --training --data /tmp/sm-audit-pages-unique --port 8893
# من نافذة أخرى؛ أنشئ مجلد النتائج أولًا
node docs/audit_20261101/browser_audit.js http://127.0.0.1:8893 /tmp/sm-audit-browser
```

اختبار HTTP يحتاج قاعدة تدريب جديدة منفصلة؛ فهو ينشئ أصنافًا ووردية وعمليات بيع:

```sh
python -m server.app --training --data /tmp/sm-audit-http-unique --port 8894
node docs/audit_20261101/browser_http.js http://127.0.0.1:8894 /tmp/sm-audit-http.json
```

أوقف الخادمين بعد الانتهاء. أسماء البيانات فريدة في كل تشغيل. اختبار النسخ يطلق الخدمة محليًا باستخدام تجهيز الاختبار الموجود بالمشروع؛ لا يحتاج حسابًا سحابيًا حقيقيًا. بيانات النتائج وهمية، ولا تشمل لقطات الشاشة في ملف التقرير القابل للتنزيل.

## الأدلة المرفقة

- `domain.json`: ١٢ سيناريو مستقلًا؛ أربع مشاهدات غير ناجحة تمثل ثلاث مشكلات منطقية.
- `browser-results.json`: الصفحات والأدوار والمشاهدات والتفاعلات.
- `http-browser.json`: التنافس وإعادة الإرسال والعرض والتخزين وإخفاء صفحة المخزون.
- `cloud-roundtrip.json`: الاستعادة ومقارنة الجداول.
- `baseline-python.log` و`baseline-cloud.log` و`existing-ui.log` و`static-ui.log`: مخرجات الحزم الموجودة بالمشروع.

أسماء الصور داخل النتائج هي أسماء لقطات أُنتجت وقت التنفيذ؛ لا يُزعم أنها مرفقة بهذه الحزمة النصية. يمكن إعادة إنتاجها بالأمر السابق. تتضمن اللقطات أقنعة لحقول كلمات المرور.
