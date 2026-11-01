# الإصدار: من الوسم إلى ملف التثبيت (للمطور، لا للعميل)

الهدف: ملف واحد `SuperMarket-Setup-<الإصدار>.exe` يثبّته صاحب المحل ويفتحه ويبيع؛ ويتصل بنسخه السحابي وحده.

## مرة واحدة (إعداد المستودع)

1. **سر المستودع `SM_ENROLL_TOKEN`** (إلزامي): رمز البناء للإصدار (`<الإصدار>.<توقيع>`، انظر [CLOUD_SYNC.md](CLOUD_SYNC.md)). الأمر: `gh secret set SM_ENROLL_TOKEN < ملف-الرمز`. بدونه يفشل بناء العميل عمدًا (حتى لا يصدر ملف لا يتصل).
2. **توقيع رقمي** (إلزامي لإصدار العميل، وشراؤه قرار بتكلفة): السران `SM_SIGN_PFX_BASE64` (شهادة pfx بترميز base64) و`SM_SIGN_PFX_PASSWORD`.

**SIG-02:** المسار الوحيد بلا توقيع هو نسخة تطابق `N.N.N-pilot` بالضبط (مثل `0.1.2-pilot`). هذا المسار يتخطى التوقيع والتحقق، ومسودته على الوسم تكون `--draft --prerelease` ومكتوب فيها أن الملف بلا توقيع وأن SmartScreen سيحذّر. أي نسخة أخرى إصدار تجاري: تطلب سرّي `SM_SIGN_PFX_BASE64` و`SM_SIGN_PFX_PASSWORD`، وتفشل عمدًا إذا غابا أو لم تكن حالة `Get-AuthenticodeSignature` هي `Valid` بعد `signtool verify /pa`. `0.2.0-pilotx` و`0.2.0-pilot-evil` ليستا هذا المسار. `workflow_dispatch` بلا إصدار GitHub ويتبع نفس القاعدة حسب `VERSION`. ملف CI-TEST في `windows-portable.yml` لم يتغيّر.

**SIG-02 (English):** `N.N.N-pilot` is the only unsigned path. It skips sign and verify, and a tag release is a draft prerelease whose notes say the installer is unsigned and SmartScreen will warn. Every other version is commercial and requires a Valid Authenticode signature; missing secrets fail the workflow on purpose. `workflow_dispatch` follows the same rule from `VERSION` and creates no GitHub Release. The CI-TEST workflow (`windows-portable.yml`) is unchanged.

## كل إصدار

1. ارفع `VERSION` في `server/version.py` (مثل `0.1.0-pilot`) وادمج في `main`.
2. `git tag v<الإصدار> && git push origin v<الإصدار>` — يشغّل `.github/workflows/release.yml`:
   الاختبارات كلها ← اختبارات الـWorker ← فحص الواجهة ← بناء المثبّت (commercial، برمز البناء) ← رحلة البرنامج المبني ← توقيع ثم تحقق Authenticode (يُتخطى فقط لنسخة `N.N.N-pilot`؛ غير ذلك يفشل إن غابت الأسرار أو لم تكن الحالة `Valid`) ← `SHA256SUMS.txt` ← تثبيت صامت ورحلة على البرنامج المثبّت وإزالته مع بقاء البيانات ← **مسودة** إصدار GitHub (ومعها `--prerelease` لمسار pilot فقط).
3. جرّب الملف على جهاز نظيف، ثم انشر المسودة من صفحة Releases (قرار المالك).
4. `workflow_dispatch` يشغّل تجربة بلا وسم ولا إصدار (الملف يُرفع كـartifact فقط). إن كانت `VERSION` هي `N.N.N-pilot` يتخطى التوقيع ويكمّل. أي نسخة أخرى تفشل إن غاب التوقيع.

## إذا تسرّب الرمز أو أُسيء استعماله

`python tools/syncctl.py admin revoke-build --build v0.1.0-pilot` ثم أصدر نسخة جديدة برمز جديد. المحلات القائمة تبقى تعمل (أجهزتها مسجلة)؛ الإلغاء يمنع تسجيل محلات جديدة بذلك الرمز فقط.


## بناء النسخة المرشحة ٠٫١٫٢
يعمل سير الإصدار أيضًا عند تغيير ملف الإصدار أو السير على الفرع الرئيسي وفروع `codex/windows-hardware-*`. يبني المثبت بالرمز المحفوظ في أسرار المستودع، ويختبر تشغيله وتركيبه. بدون وسم لا ينشئ إصدار GitHub؛ يُرفق المثبت وبصمته كأثر بناء. يبقى المسار التجريبي بلا توقيع وفق القاعدة الحالية، ولا تتحول النسخة إلى تجارية بتغيير اسم الملف.
