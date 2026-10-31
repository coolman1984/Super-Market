# الإصدار: من الوسم إلى ملف التثبيت (للمطور، لا للعميل)

الهدف: ملف واحد `SuperMarket-Setup-<الإصدار>.exe` يثبّته صاحب المحل ويفتحه ويبيع؛ ويتصل بنسخه السحابي وحده.

## مرة واحدة (إعداد المستودع)

1. **سر المستودع `SM_ENROLL_TOKEN`** (إلزامي): رمز البناء للإصدار (`<الإصدار>.<توقيع>`، انظر [CLOUD_SYNC.md](CLOUD_SYNC.md)). الأمر: `gh secret set SM_ENROLL_TOKEN < ملف-الرمز`. بدونه يفشل بناء العميل عمدًا (حتى لا يصدر ملف لا يتصل).
2. **توقيع رقمي** (إلزامي لإصدار العميل، وشراؤه قرار بتكلفة): السران `SM_SIGN_PFX_BASE64` (شهادة pfx بترميز base64) و`SM_SIGN_PFX_PASSWORD`.

**SIG-02:** إصدار العميل يطلب سرّي التوقيع. غيابهما، أو مثبّت بلا توقيع Authenticode صالح، يفشل سير `release.yml` عمدًا ولا يُرفع ملف بلا توقيع. بعد التوقيع ينجح التحقق فقط إذا نجح `signtool verify /pa` وكانت حالة `Get-AuthenticodeSignature` هي `Valid`. تجربة `workflow_dispatch` تبني نفس ملف العميل، فتخضع لنفس الفشل. ملف CI-TEST في `windows-portable.yml` (اسمه `…CI-TEST-NOT-FOR-CUSTOMERS`) ليس ملف عميل ولم يتغيّر.

**SIG-02 (English):** A commercial customer build requires the signing secrets `SM_SIGN_PFX_BASE64` and `SM_SIGN_PFX_PASSWORD`. Missing secrets, or an installer that is not Authenticode-signed, fails the workflow on purpose. `workflow_dispatch` produces the same customer-named installer, so it fails closed too. The CI-TEST workflow (`windows-portable.yml`) is unchanged.

## كل إصدار

1. ارفع `VERSION` في `server/version.py` (مثل `0.1.0-pilot`) وادمج في `main`.
2. `git tag v<الإصدار> && git push origin v<الإصدار>` — يشغّل `.github/workflows/release.yml`:
   الاختبارات كلها ← اختبارات الـWorker ← فحص الواجهة ← بناء المثبّت (commercial، برمز البناء) ← رحلة البرنامج المبني ← توقيع ثم تحقق Authenticode (يفشل إن غابت الأسرار أو لم تكن الحالة `Valid`) ← `SHA256SUMS.txt` ← تثبيت صامت ورحلة على البرنامج المثبّت وإزالته مع بقاء البيانات ← **مسودة** إصدار GitHub بالملف والبصمات.
3. جرّب الملف على جهاز نظيف، ثم انشر المسودة من صفحة Releases (قرار المالك).
4. `workflow_dispatch` يشغّل تجربة بلا وسم ولا إصدار (الملف يُرفع كـartifact فقط)، ويفشل هو أيضًا إن غاب التوقيع لأن الملف باسم العميل.

## إذا تسرّب الرمز أو أُسيء استعماله

`python tools/syncctl.py admin revoke-build --build v0.1.0-pilot` ثم أصدر نسخة جديدة برمز جديد. المحلات القائمة تبقى تعمل (أجهزتها مسجلة)؛ الإلغاء يمنع تسجيل محلات جديدة بذلك الرمز فقط.
