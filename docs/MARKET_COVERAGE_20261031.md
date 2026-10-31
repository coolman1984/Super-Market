# جدول تغطية الحالات الأربعين — ٣١ أكتوبر ٢٠٢٦

المصدر: مصفوفة الحالات في `docs/EGYPT_MARKET_OPERATING_RESEARCH_20261031.md`.
الاختبارات القديمة الناجحة **لا تُحسب اكتمالًا**؛ كل صف هنا يذكر الدليل الذي يثبته أو يعلن صراحة أنه غير منفذ.

تحديث: الصفوف 2، 3، 5، 6، 9، 14، 26، 38 نُفذت في ترحيل 4 (`server/schema.py` M004) واختُبرت في `tests/test_matrix_open_rows.py` و`tests/ui/rows.js`.

الحالات: **مغطى** = سلوك صحيح منفذ ومختبر. **جزئي** = جزء منفذ ومختبر والباقي مذكور. **غير منفذ** = غير موجود في الإصدار الحالي ولا يُدّعى غير ذلك.

| # | الحالة | الحالة | الدليل (اختبار أو ملف) | المتبقي الحقيقي |
|---|---|---|---|---|
| 1 | استلام جزئي ثم باقي الشحنة | مغطى | `test_chain::test_row01_...`، `test_matrix_open_rows::test_row02_invoice_before_goods_...` (فاتورة مسجلة تتتبع المتبقي) | — |
| 2 | فاتورة قبل الاستلام | مغطى | `test_matrix_open_rows::test_row02_invoice_before_goods_adds_no_stock_and_no_debt_...`، `test_row02_a_product_not_on_the_invoice_...`، `tests/ui/rows.js` | الدَّين يُسجَّل عند الاستلام لا عند الفاتورة (قرار تصميم مكتوب) |
| 3 | فاتورة بعد الاستلام والبيع | مغطى | `test_matrix_open_rows::test_row03_cost_correction_after_partial_sale_...`، `test_row03_a_lower_final_price_never_...`، `tests/ui/rows.js` | نصيب ما دخل في تجهيز يُحسب تكلفة مبيعات اليوم ولا يُوزَّع على الدفعات الناتجة |
| 4 | وحدات مجانية في الصفقة | مغطى | `test_shop::test_free_goods_spread_cost_without_losing_piastres` | — |
| 5 | هدية أو عينة مستقلة | مغطى | `test_matrix_open_rows::test_row05_gift_enters_at_zero_...`، `tests/ui/rows.js` | سياسة التقييم: صفر؛ أي سياسة أخرى تحتاج قرار المالك والمحاسب |
| 6 | خصم مورد لاحق | مغطى | `test_matrix_open_rows::test_row06_rebate_on_a_receipt_...` | — |
| 7 | فرق وزن أو تالف عند الوصول | مغطى | `rejected_qty` في الاستلام (`server/services.py`)، `test_shop::test_r01_...` | — |
| 8 | رد مورد جزئي وإشعار مختلف | مغطى | `test_chain::test_row08_partial_supplier_return_and_a_different_credit_note` | — |
| 9 | تغير عدد القطع بالكرتونة | مغطى | `test_matrix_open_rows::test_row09_carton_size_change_keeps_old_receipts_...`، `tests/ui/rows.js` | — |
| 10 | نفس الباركود لتعبئة أخرى | مغطى | `barcode_taken` في `add_product`، `test_audit::test_r15_packaging_must_belong_to_the_product`، `test_scanner::test_similar_gtin_is_offered_not_merged` | — |
| 11 | لحم بعظم ومخرجات ثانوية | مغطى | `test_shop::test_p01_p02_p08_cutting_mass_cost_and_variance_approval`، `test_domain::test_production_mass_packaging_cost_and_full_genealogy` | — |
| 12 | مجهول المصدر أو انتهاء ناقص | جزئي | `expiry_required` عند الاستلام، `test_domain::test_production_requires_date_on_every_gram_input` | لا خانة «مصدر مجهول» تحجز تلقائيًا |
| 13 | انقطاع التبريد | جزئي | `test_shop::test_q01_recall_isolates_descendants_and_finds_invoices`، `test_audit::test_r03_...` | لا واقعة «انقطاع تبريد» بعينها؛ يُستخدم الاستدعاء اليدوي |
| 14 | فك تجميد | مغطى | `test_matrix_open_rows::test_row14_*` (3 اختبارات)، `tests/ui/rows.js` | المدة يحددها المالك؛ البرنامج لا يفترض مدة ولا درجة حرارة |
| 15 | غياب مكون أو بديل غير معتمد | مغطى | `test_audit::test_r04_recipe_components_...`، `test_r04_approved_alternative_counts_as_its_component` | — |
| 16 | مكون غير لحمي بدور لحم (E03) | مغطى | `test_market::test_e03_meat_role_needs_an_approved_meat_product`، `test_e03_composite_with_documented_meat_share_...` | — |
| 17 | بديل يحمل حساسية جديدة | مغطى | `test_chain::test_row17_substitute_allergens_reach_the_lot_and_label` | — |
| 18 | إنتاج من عبوة محجوزة (E02) | مغطى | `test_market::test_e02_production_cannot_consume_stock_promised_to_packs`، `test_e02_explicit_unpack_...` | — |
| 19 | عبوات بأوزان مختلفة | مغطى | `test_chain::test_row19_20_packs_of_different_weights_...` | — |
| 20 | إعادة طباعة الملصق | مغطى | الاختبار نفسه: إعادة الطباعة لا تُنشئ كمية ولا رقم عبوة | — |
| 21 | رفع سعر وخفض آخر بالمجموع نفسه (E04) | مغطى | `test_market::test_e04_quote_fingerprint_covers_every_line`، `test_e04_quote_hash_covers_discounts_and_quantities`، `test_audit::test_r14_price_change_after_scan_needs_acceptance` | — |
| 22 | سعر الملصق يختلف عن سعر الكيلو | جزئي | `test_scanner::test_b01_b03_weight_label_and_unknown`، `test_b02_price_label_without_weight_is_blocked` | لا سياسة معلنة لاختلاف سعر الميزان عن سعر الكتالوج |
| 23 | آخر كمية من محطتين | مغطى | `test_domain::test_concurrent_distinct_sales_cannot_sell_same_last_stock`، `test_shop::test_s01_two_cashiers_last_unit` | اختبار الاتصال الفعلي من جهازين على شبكة حقيقية لم يُنفذ |
| 24 | نقر مزدوج أو رد مفقود | مغطى | `test_domain::test_concurrent_same_key_sales_post_once`، `test_audit::test_r10_replay_belongs_to_its_creator`، العمليات المعلقة في `core.js` | — |
| 25 | تعليق فاتورة ثم تغير السعر | جزئي | التعليق والاستئناف في `sell.js`؛ الخادم يرفض بصمة عرض قديمة (`price_changed`) | لا اختبار متصفح مخصص لتعليق/استئناف |
| 26 | اشترِ واحصل ثم رد جزء | مغطى | `test_matrix_open_rows::test_row26_*`، `tests/ui/rows.js` | عروض الصنف نفسه فقط؛ عروض بين أصناف مختلفة غير منفذة |
| 27 | دفعتان لنفس الدين أو سداد زائد | مغطى | `test_chain::test_row27_28_...` | — |
| 28 | دفع بعد رد جزئي | مغطى | الاختبار نفسه | — |
| 29 | عكس تحصيل بعد رد نقده (E01) | مغطى | `test_market::test_e01_collection_used_by_a_refund_cannot_be_reversed`، `test_e01_untouched_collection_still_reverses_...` | — |
| 30 | تحويل مرفوض لعميل غير مسجل (E05) | مغطى | `test_market::test_e05_goods_return_when_an_anonymous_transfer_was_rejected`، `test_e05_mixed_cash_and_rejected_transfer_partial_returns` | — |
| 31 | رد على أكثر من وسيلة | مغطى | `test_audit::test_r01_one_item_paid_half_cash_half_card_is_refunded_by_both`، `test_refund_follows_payment_method` | — |
| 32 | دفعة مستدعاة ثم مرتجع | مغطى | `test_audit::test_r03_recall_survives_returns_and_cannot_be_released_by_status` | — |
| 33 | معيبة مقابل تغيير رأي | مغطى | `test_chain::test_row33_...` | السياسة القانونية للشكوى تحتاج مراجعة قانونية خارج الكود |
| 34 | بيع أثناء العد | مغطى | `test_shop::test_count_with_sales_between_opening_and_counting_books_nothing` | — |
| 35 | عبوة محددة مفقودة | مغطى | `test_chain::test_row35_a_missing_pack_is_written_off_as_a_pack_before_the_count_balances` | — |
| 36 | من يعد لا يعتمد فرقه | مغطى | `test_shop::test_c01_c02_c03_c04_blind_count` (المالك بسبب موثق فقط) | — |
| 37 | تحديث قاعدة فيها تحصيلات قديمة (E06) | مغطى | `test_market::UpgradeFromV1` (٤ اختبارات) و`test_recovery::test_v1_shop_upgrades_...` | قاعدة عميل حقيقية لم تُجرَّب |
| 38 | إقفال يوم ثم تصحيح مؤرخ | مغطى | `test_matrix_open_rows::test_row38_closed_day_takes_no_posting_...` | — |
| 39 | إعادة تشغيل الخادم أو نافذتان | مغطى | `test_launcher::test_s04_...`، `test_api::test_r09_...`، `test_audit::test_r13_restart_epoch_...` | تشغيل فعلي على ويندوز بجهاز عميل نظيف: بوابة CI فقط |
| 40 | برنامج يعمل أيامًا | مغطى | `test_launcher::test_row40_a_long_running_program_backs_up_each_new_day` | وقت الاستعادة على بيانات حقيقية لم يُقَس |

**الخلاصة:** 36 مغطى، 4 جزئي (12، 13، 22، 25)، 0 غير منفذ. المتبقي في عمود «المتبقي الحقيقي» حدود معلنة وليست أعطالًا خفية.
