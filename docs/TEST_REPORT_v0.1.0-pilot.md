# تقرير اختبار «السوبر ماركت» — النسخة v0.1.0-pilot

**تاريخ الاختبار:** السبت 31 أكتوبر 2026 (بتوقيت القاهرة)
**المُختبِر:** Grok Bot — بطلب من أ. محمد فوزي
**ما تم اختباره:** الكود المنشور بنفس الوسم `v0.1.0-pilot` (commit `7de0dc0`)، شغّلته من المصدر ببيانات مؤقتة منفصلة (`SM_CLOUD_URL=off`)، وفتحت كل الصفحات في متصفح حقيقي (Chromium) بالعربي والإنجليزي، وجرّبت الأدوار الخمسة. ما غيّرتش أي سطر كود، ومفيش أي حاجة اتبعتت أو اتنشرت.
**الصور:** في مجلد `/workspace/super-market-test-report-assets/` (باقي لقطات الشاشة الكاملة في `/workspace/smtest/shots/`).

---

## الخلاصة في سطور

> **البرنامج قوي من جوّه.** الحسابات والفلوس والمخزون والصلاحيات اتجربت بعنف (ضغط مزدوج، بيع من أكتر من جهاز في نفس اللحظة، أرقام غلط، حروف غريبة، محاولات اختراق بسيطة) ومفيش حاجة وقعت ولا فلوس ضاعت ولا مخزون اتحسب غلط. الـ 267 اختبار الداخلية نجحوا كلهم.
> **المشاكل اللي لقيتها** أغلبها في «السهولة» وفي رسايل وشاشات صغيرة، مش في الحسابات.

| الخطورة | العدد | معناها |
|---|---|---|
| حرجة (Critical) | **0** | فلوس بتضيع أو بيانات بتتلف |
| عالية (High) | **0** | شغل بيقف أو غلط في الحساب |
| متوسطة (Medium) | **11** | بتلخبط الموظف أو بتعمل مخاطرة صغيرة |
| بسيطة (Low) | **13** | تفاصيل وتجميل |

### أهم 5 حاجات أنصح بإصلاحها قبل ما نسلّم للمحل
1. **كلمة `null` بتظهر في شاشة البيع** كل ما الكاشير يمسح باركود مش مسجّل (M1).
2. **كلمات المرور الضعيفة مقبولة** (مثلاً `12345678`) (M3).
3. **السعر ممكن يتحط صفر أو يتساب فاضي من غير تحذير** (M4).
4. **شاشة «المخزون» ظاهرة لمسؤول الجرد وبتديه رسالة خطأ حمرا** وجنبها كلمة «لا توجد دفعات» (M2).
5. **شاشة الإعدادات وإضافة صنف مليانة مصطلحات صعبة** («في الألف»، «بادئة»، «رمز الصنف»، «مسببات الحساسية بحروف لاتينية») — ده بيكسر قاعدة «الأسهل أولاً» (M6, M7).

---

## اللي ما قدرتش أختبره (بصراحة)

لأن الاختبار اتعمل على جهاز لينكس من غير ويندوز:
- **نافذة البرنامج الحقيقية** (`msedge --app`) — اختبرت نفس الصفحات في Chromium، فاحتمال فرق صغير في الشكل أو في مربعات الكتابة الأصلية (`prompt`) في Edge.
- **المُثبّت نفسه (Setup.exe) والبرنامج المثبّت فعلاً (SuperMarket.exe)** — اتأكدت إن الملف سليم (SHA-256 مطابق) لكن ما قدرتش أفتحه من جوّه ولا أنزّله فعلاً؛ أداة فك Inno Setup اللي عندي ما بتقرأش النسخة 6.7.0. بدل كده قرأت سكربت البناء وملف `.iss`.
- **الطابعة، قارئ الباركود الحقيقي، الميزان** — جرّبت إدخال الباركود بالكتابة السريعة (30 مسح متتالي) لكن مش جهاز فعلي.
- **الفلاشة والنسخة الخارجية الحقيقية** على ويندوز (اختُبرت في اختبارات الكود الداخلية فقط).
- **السحابة (Cloud)** — اتقفلت عمداً بـ `SM_CLOUD_URL=off`.
- **ساعة الجهاز الغلط فعلياً** — اتراجعت من الكود (انظر الملحق) مش بتغيير ساعة الجهاز.

---

## المشاكل — مرتبة بالأهم

### 🟠 متوسطة

#### M1 — كلمة `null` بتظهر في شاشة البيع بعد رسالة الباركود
- **اللي بيحصل:** الكاشير يمسح باركود مش مسجّل → تظهر رسالة حمرا «باركود غير مسجَّل. اربطه بصنف من شاشة الأصناف.» وجنبها كلمة **null** بالإنجليزي.
- **ازاي تكرّرها:** سجّل دخول كاشير → افتح وردية → «البيع» → اكتب `6229999999995` (أو أي رقم مش مسجّل، حتى `123`) → Enter.
- **ليه مهم:** بيحصل في كل باركود جديد، والكاشير يحس إن البرنامج «بايظ».
- **صورة:** `sell-scan-errors.png`

#### M2 — مسؤول الجرد يشوف «المخزون» ويطلع له خطأ + شاشة فاضية مضلِّلة
- **اللي بيحصل:** المستخدم بدور «مسؤول الجرد» القائمة بتعرض له «المخزون والدفعات». يدوس عليها → رسالة حمرا «ليست لديك صلاحية…» وتحتها شاشة فاضية مكتوب فيها «لا توجد دفعات» — وده غلط (فيه دفعات، هو بس مش مسموح له).
- **ازاي تكرّرها:** أنشئ مستخدم بدور «مسؤول الجرد» → سجّل دخوله → «المخزون والدفعات».
- **الحل المقترح:** إمّا نخفي الصفحة عنه، أو نعرض له قراءة بدون التكلفة.
- **صورة:** `ar-d-cnt-stock.png`

#### M3 — أي كلمة مرور من 8 حروف مقبولة (حتى `12345678`)
- **اللي بيحصل:** القاعدة الوحيدة هي الطول (8 إلى 128). مفيش فحص للكلمات الشائعة أو المتكررة. وشاشة أول تشغيل بتنصح «اكتبها على ورقة».
- **ازاي تكرّرها:** «الإعدادات» → إضافة مستخدم → امسح كلمة المرور المقترحة واكتب `12345678` → إضافة. أو غيّر كلمة المرور بعد الدخول بنفس القيمة.
- **ملحوظة إيجابية:** البرنامج بيقترح كلمة عشوائية جاهزة للموظف الجديد (حلو جداً) — المشكلة بس إن حد يقدر يغيّرها لحاجة ضعيفة.

#### M4 — السعر صفر أو «فاضي» بيتقبل بدون تحذير
- **(أ) تغيير السعر:** «الأصناف» → اختار صنف → «تغيير السعر» → اكتب `0` → «تم الحفظ». الصنف يتباع بـ 0 جنيه. (الحروف والسالب والأرقام الضخمة بيترفضوا ✔، والأرقام العربية `٢٥` والفاصلة `20,5` بتشتغل ✔.)
- **(ب) صنف جديد:** اكتب في خانة السعر `abc` → «تم الحفظ» والصنف بيتحفظ **من غير سعر** (بيظهر شرطة «—») وأول ما الكاشير يبيعه هيطلع خطأ. البرنامج ما نبّهش صاحب المحل وقت الحفظ إن السعر اتتجاهل.
- **الحل المقترح:** رسالة «السعر صفر، متأكد؟» ورفض النص الغلط بدل ما يتجاهله.

#### M5 — رسالة الإعدادات الغلط ما بتقولش أنهي خانة + اسم المحل ممكن يتحفظ فاضي
- **اللي بيحصل:** أي خانة غلط في «الإعدادات» → «قيمة الإعداد غير صحيحة. لم تُحفظ العملية.» من غير ما تقول أنهي واحدة (فيه 12 خانة).
- **كمان:** اسم المحل لو اتمسح وحفظت → «تم الحفظ» وشاشة الدخول بتظهر من غير اسم محل.
- **ازاي تكرّرها:** «الإعدادات» → اكتب `abc` في «عدد أيام التنبيه» → «حفظ». أو امسح «اسم المحل» → «حفظ».
- **(مفيد:)** القيم المرفوضة صح: ساعة بداية اليوم 9 / −1 / 4.5، خصم الكاشير 5000، أيام التنبيه سالب أو 99999.

#### M6 — «صنف جديد»: 12 خانة وبعضها صعب
- **اللي بيحصل:** لازم صاحب المحل يخترع «رمز صنف» بنفسه (لو سابه فاضي: «الرمز غير صحيح» من غير شرح)، وفيه «مسببات الحساسية (بحروف لاتينية صغيرة يفصل بينها فاصلة)» و«نسبة اللحم الموثقة (في الألف)». رسالة نسبة اللحم الغلط طويلة ومعقدة.
- **كمان:** رمز الصنف بيقبل مسافات وإيموجي (`B K 1 😀`) واسم صنف 40 إيموجي.
- **ازاي تكرّرها:** «الأصناف» → «صنف جديد».
- **الحل المقترح:** رمز تلقائي، وإخفاء الخانات المتقدمة تحت «خيارات إضافية».
- **صورة:** `product-dialog.png`

#### M7 — صفحة «الإعدادات» و«المساعدة» تقيلة على غير الفني
- **الإعدادات:** فيها «خصم الكاشير (في الألف)»، «ملصقات الميزان: البادئة، بداية رمز الصنف، طول القيمة…»، «معاملة الصور المختلفة الطول لباركود…»، و«فاقد التقطيع (في الألف)». مفيش قيم جاهزة تتختار بضغطة.
- **المساعدة:** حائط نص طويل بلغة رسمية («المتطلبات قبل البدء»، «التصحيح الآمن»، «النتيجة عند النجاح»).
- **الحل المقترح:** «إعدادات سريعة» (4–5 خانات) والباقي «متقدم»، وصفحة مساعدة بجمل قصيرة وصور.
- **صور:** `full-ar-owner-settings.png`, `help-page.png`

#### M8 — الكاشير يقدر يدخل كمية ضخمة أو أكتر من المخزون، والخطأ بيطلع متأخر
- **وزن ضخم:** ادخل 99,999 كجم دجاج بالوزن اليدوي → بيتحط في السلة، الإجمالي «29,999,700.00» ويكسر تصميم الشاشة (الأرقام فوق بعض ولوحة الدفع بتتقص من الشمال). الخطأ «كمية غير صحيحة» بيطلع بس بعد ما يدوس «الدفع».
- **أكتر من المخزون:** ضيف كمية أكبر من الموجود → افتح الدفع → اكتب الفلوس → «تأكيد الدفع» → وقتها بس: «الكمية المتاحة للبيع لا تكفي» (والزبون مستني).
- **الحل المقترح:** تنبيه فوري في السلة.
- **صور:** `pay-huge-weight.png`, `pay-over-stock-result.png`

#### M9 — عشر عمليات بتستعمل مربع كتابة قديم (`prompt`) بدل شاشة البرنامج
- **اللي بيحصل:** تغيير السعر، سبب إيقاف مستخدم، إلغاء الجرد، سبب الجرد من المالك، عكس دفعة، إقفال مطالبة أو فاتورة للموردين، سحب/رفع سحب منتج، إلغاء الجرد… كلها بتفتح مربع متصفح عادي من غير عنوان واضح ولا شكل البرنامج. لو الموظف داس «إلغاء» أو سابه فاضي، **ما بيحصلش حاجة وما بتظهرش أي رسالة**.
- **ازاي تكرّرها:** «الأصناف» → صنف → «تغيير السعر».
- **ملحوظة:** ما اختبرتش السلوك الحقيقي في نافذة `msedge --app`.

#### M10 — الدروس: مقفولة، والعداد مش مظبوط
- **اللي بيحصل:** المالك شايف 23 درس لكن العداد الصغير بيقول «0/19». وكل الدروس تقريباً (21 من 23) زرها «ابدأ» مقفول بحجة «بعد درس سابق» — يعني المالك ما يقدرش يروح لدرس معيّن مباشرة.
- **كمان:** «1 دقائق» (المفروض «دقيقة»).
- **ازاي تكرّرها:** «المساعدة» → «افتح الدليل».
- **صورة:** `guide-open.png`

#### M11 — «المساعدة» بتقول شغّل `start-training.bat`… ويمكن الملف مش موجود في النسخة المثبّتة
- **اللي بيحصل:** النص «للتدريب شغّل الملف start-training.bat الموجود داخل مجلد البرنامج». سكربت بناء المُثبّت بينسخ بس ملفات `launcher.dist` والـ docs، ومفيش `.bat`؛ والمُثبّت بيعمل اختصار في قائمة ابدأ اسمه «السوبر ماركت - تدريب».
- **الحالة:** **غير مؤكد** — ما قدرتش أفتح المُثبّت. يستاهل تتأكد منه على جهاز ويندوز.

### 🟡 بسيطة

| # | المشكلة | ازاي تكرّرها |
|---|---|---|
| L1 | لو الجلسة خلصت (أو اتمسحت)، الكاشير بيتنقل لشاشة الدخول **من غير أي رسالة** «انتهت الجلسة» وبيضيع اللي كان بيعمله. | سجّل دخول → امسح الكوكيز → امسح باركود. صورة `expired-session.png` |
| L2 | رسالة «غير موجود» بتقول «غير موجود **على السحابة**» حتى والسحابة مقفولة. (`ar.js:1237`) | أي عنصر مش موجود |
| L3 | رسالة تأخير الدخول بتقول «انتظر دقيقة أو أكثر» والقفل الفعلي 30 ثانية. | 5 كلمات مرور غلط. صورة `login-locked.png` |
| L4 | أول تشغيل: لو الفورم فاضي بيقول «اسم المستخدم غير صحيح» بس (من غير ذكر اسم المحل ولا الباقي). كمان نفس الفورم بيقبل اسم مستخدم عربي بمسافة ("محمد فوزي") وده شغّال، لكن صعب على الموظف يكتبه. | أول تشغيل → «بدء الاستخدام» فاضي. صورة `firstrun-2.png` |
| L5 | مفيش قفل تلقائي لو الجهاز اتساب؛ القفل يدوي بس، والجلسة 14 ساعة. | سجّل دخول وسيبه |
| L6 | أرقام ضخمة بتتقبل: استلام كمية 1,000,000,000,000، فلوس العميل 10¹⁵ قرش، عدّ الدرج 10¹⁵. (محمية من التلاعب بس مفيش سقف منطقي). | API / الاستلام |
| L7 | إقفال اليوم الساعة 8:30م ثم البيع بعده بيتسجل على «بكرة» (تصميم متعمد، لكن ممكن يلخبط محل بيقفل بدري ويكمل). | «إقفال اليوم» ثم بيع |
| L8 | الخانات في الاستلام والجرد من غير ربط (label) بالخانة نفسها — مشكلة وصول للقارئات الصوتية. | شاشة «استلام البضاعة» |
| L9 | شاشة الجرد بتعرض رقم الدفعة `L000003` للموظف. مش مفهوم لغير الفني. | `count-sheet.png` |
| L10 | شاشة «التقطيع» كثيفة على الجزار (3 كروت ومعها خانتين فاقد). | `full-ar-owner-prepare.png` |
| L11 | طلب HEAD/OPTIONS للسيرفر بيرجّع صفحة خطأ إنجليزي عادية (501). غير ظاهرة للمستخدم. | `curl -I localhost:8803` |
| L12 | شريط أصفر دائم في كل الصفحات «لا توجد نسخة خارج جهاز المحل…» قد يتحول لضوضاء (لكنه مفيد). | أي صفحة قبل تعيين نسخة خارجية |
| L13 | وقت التسجيل مخزّن كنص بإزاحة (+02:00/+03:00)؛ لحظة تغيير التوقيت الصيفي المقارنة النصية قد تخطئ ساعة (نظري، من الكود). | ملحق |

---

## اللي اتجرّب وطلع **سليم** ✔

- **الفلوس:** مفتاح عدم التكرار (ضغط مزدوج أو 8 طلبات متوازية بنفس المفتاح = بيع واحد). 12 محاولة بيع في نفس اللحظة على مخزون محدود = نجح 3 واترفض 9 «مخزون غير كافٍ» (مفيش بيع زيادة). التلاعب في الإجمالي أو الهاش مرفوض.
- **الأمان:** الدخول بدون الـ header أو الكوكي مرفوض، الصلاحيات لكل دور (403 صح)، حقن SQL و`<script>` والإيموجي لا بتكسر ولا بتتنفّذ (مظهرها نص عادي)، أسماء ضخمة مرفوضة، مسارات التخطّي (`../`) مرفوضة.
- **تسجيل الدخول:** 5 محاولات غلط → قفل مؤقت برسالة واضحة، والحساب غير مقفل لبقية الأجهزة.
- **أول تشغيل:** الفورم بيرفض كلمة قصيرة وكلمتين مش متطابقتين برسالة بالعربي.
- **الأرقام العربية والفاصلة** (`٢٥`، `20,5`) بتتفهم في الأسعار والكميات.
- **الإنجليزي والعربي** كلاهما بدون نصوص مكسورة، وما فيش عرض زائد على الشاشة في الـ 14 صفحة (فحصت 1280×800 بالعربي والإنجليزي، وصور الموبايل اتصوّرت لكن ما قسّتش الفيض فيها بدقة).
- **إقفال الوردية:** بيعرض الفرق ويطلب تأكيد. **إقفال اليوم:** بيرفض لو فيه وردية مفتوحة، وما بيتكررش.
- **المرتجعات:** بتحتاج موافقة مدير حسب القواعد (مبلغ كبير، فاتورة قديمة، كاشير تاني، نقدي «غيّرت رأيي»).
- **السيرفر:** صفر `Traceback` في كل سجلات الاختبار.

---

# ملحق تقني (English) — for Claude

**Environment.** Tag `v0.1.0-pilot` (7de0dc0), fresh clone, Python 3.13, `python -m server.app --data <tmp> --port 88xx`, `SM_CLOUD_URL=off`. Playwright (Chromium) Python driving 14 hash routes × roles (owner/manager/cashier/counter/butcher) × ar/en × desktop/mobile; ~188 screenshots in `/workspace/smtest/shots/`. API abuse scripts a1–a9 in `/workspace/smtest/t/`. Release asset sha256 verified; installer contents not extractable (innoextract 1.9 vs Inno 6.7.0). `tools/ci_tests.py`: 267 tests OK. No code modified.

**Findings with code pointers**

| ID | Sev | Detail |
|---|---|---|
| M1 | Med | `web/js/views/sell.js` ~374–375: `status.append(badge, r.seen ? h('code', …) : null)` — `Node.append(null)` appends the text "null". Fix: build an array and filter nulls (or `SM.put`). Triggered when scan result has `ok:false` and no `seen`. |
| M2 | Med | Counter role has a nav entry for `#stock` but the data call is refused (403-type) → `err.forbidden` toast + empty state `lots.none`. UI should hide route or distinguish "forbidden" from "empty". |
| M3 | Med | `server/auth.py: check_password_rules` — only 8 ≤ len ≤ 128. `POST /api/account/password` with `12345678` → 200. Consider blocklist/min-variety; setup help text suggests writing the password on paper. |
| M4 | Med | `web/js/views/admin.js:33–37` price prompt: `SM.parseMoney('0')` → 0 accepted, `/api/prices` accepts 0 (quote returns total 0, unit_price 0). `admin.js:119`: `price: f.price.value ? SM.parseMoney(f.price.value) : undefined` — an unparsable string yields `null` → product saved with `price=null`; quote → 400 `no_price`. Confirmed: codes Z2 (price 0), Z3 (price None). |
| M5 | Med | `/api/settings` returns one generic `bad_setting`/`err.*` for any field. Empty `shop_name` accepted. 3000-char name rejected, `<img onerror>` saved but rendered escaped. `return_limit`=0 and `return_days`=0 accepted (everything needs approval). |
| M6 | Med | `admin.js` ~105–125 new-product dialog: 11 text fields + checkbox; `code` required (empty → "الرمز غير صحيح"); code accepts spaces/emoji (`"B K 1 😀"` saved). Meat share permille validated 0–1000, message references "مجهَّز أو مكوّن". |
| M7 | Med | Settings has 12 numeric/text fields + scale-label block with technical jargon; Help page prose in formal MSA. |
| M8 | Med | Cart accepts manual weight up to 99,999 kg (UI) and qty > stock; only `POST /api/sales` rejects (`bad_quantity` / `insufficient_stock`). Layout overflow in `.pay-panel` with 8-digit totals. `/api/quote` validates but the UI only shows errors in the payment path. |
| M9 | Med | 17 native `prompt/confirm/alert` calls (10 are `prompt`): `parties.js:29,63,75`, `admin.js:34,380`, `ops.js:168,175,294,539,549`. Cancel/empty → silent no-op (`if (!reason) return;`). Not tested inside `msedge --app` (prompts may behave differently / be blocked in some kiosk flags). |
| M10 | Med | Guide: 23 start buttons for owner (21 `disabled`, "after a previous lesson") vs pill `0/19`. i18n string `N دقائق` with N=1. |
| M11 | Med (unverified) | `web/js/i18n/ar.js:645`, `:913` reference `start-training.bat` / `start-training-reset.bat`; `installer/super-market.iss` only copies `build\launcher.dist\*` and `tools/build_windows.py` copies docs only; `.bat` files are in `tools/build_portable.py APP_FILES` (portable build only). Installed build uses Start-menu shortcut (`{autoprograms}\Training`, `SuperMarket.exe training`). Verify on Windows. |
| L1 | Low | 401 on any POST → login screen without "session expired" message (hash stays `#sell`); in-memory cart lost. `SESSION_HOURS = 14`; no idle lock (`SM.lockNow` manual only, `app.js:90`). |
| L2 | Low | `i18n/ar.js:1237` & `en.js:1237` `err.not_found` = "…not in the cloud". |
| L3 | Low | Login throttle: 5 failures → 429 for ~30 s on loopback; message says "a minute or more". |
| L4 | Low | `/api/setup`: empty fields → `bad_username` toast only. |
| L6 | Low | No upper bound: receipt qty 10¹², `cash_tendered` 10¹⁵ (change 999999999996000), shift `counted` 10¹⁵. |
| L7 | Low | `db.py: business_day_for` – after `day_closes`, new work belongs to next day even though wall-clock date is the same. By design (commented), flag for UX. |
| L8–L10, L12 | Low | a11y/density/jargon (labels not bound with `for`/`id`; lot numbers in count sheet). |
| L11 | Low | `BaseHTTPRequestHandler` default 501 HTML for HEAD/OPTIONS. |
| L13 | Low | `core.py now_iso()` stores `isoformat()` with local offset; `auth.py` compares `expires_at < now_iso()` as strings → one-hour skew around Cairo DST changes (Egypt observes DST). Also `SHOP_TZ=None` fallback when `tzdata` is missing on Windows (documented in ENVIRONMENT_REPORT.md) — confirm tzdata is bundled in the Nuitka build. |

**Clock oddities (code review).** `db.py clock_behind()` / `business_day_for()` refuse all postings when `today() < MAX(day_closes.day)` and the UI shows a dedicated clock screen (`app.js clockScreen`). Forward clock jumps are not detected (sessions would expire early; day boundaries shift).

**Verified OK (API).** Idempotency keys (same key ×8 concurrent → one sale; same key + different payload rejected), concurrent oversell (3 ok / 9 `insufficient_stock`), quote tamper (`expected_total`, `quote_hash`), negative/zero/float/10⁹ qty, price override ignored, CSRF header, per-role 403 matrix, SQLi in login/search/period/party, traversal 404, huge names `bad_name`, oversized body closed (client BrokenPipe, no server traceback), `MAX_BODY=1,000,000`.

**Test artefacts.** Scripts `/workspace/smtest/t/{c.py,seed.py,a1–a9.py,ui.py,p1–p31.py}`, outputs `/workspace/smtest/out-a*.txt`, `ci_tests.log`, `srv-*.log`.
