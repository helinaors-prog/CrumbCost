import unittest
import math
from types import SimpleNamespace
from unittest.mock import Mock

from crumbcost_pdf import (
    format_order_calculation,
    generate_customer_estimate,
    order_price_label,
)

from crumbcost_core import (
    build_unit_registry,
    build_recipe_start_from_pantry,
    calculate_order_quote,
    calculate_order_scale,
    calculate_pricing_confidence,
    calculate_pricing_options,
    calculate_product_analysis,
    calculate_ingredient_cost,
    clear_receipt_scan_state,
    clean_pdf_text,
    format_product_analysis,
    ingredient_draft_has_changes,
    normalize_pricing_method,
    normalize_unit_label,
    pantry_draft_has_changes,
    pantry_state_signature,
    receipt_draft_has_changes,
    receipt_upload_signature,
    reconcile_receipt_upload,
    request_tab_navigation,
    generate_receipt_content,
    ReceiptAnalysisError,
    RECEIPT_ANALYSIS_ERROR_MESSAGE,
    resolve_config_value,
    select_gemini_model,
    consume_tab_navigation,
    conversion_profile_from_label,
    density_for_ingredient,
    safe_filename,
    should_apply_receipt_conversion,
    standard_conversion_options,
    automatic_conversion_reference,
    update_visible_selection,
    visible_selection_control,
)


class CoreCalculationTests(unittest.TestCase):
    def test_product_identity_and_preparation_block_false_seed_sugar_matches(self):
        for name in (
            "SESAME SEED OIL", "Brand SESAME-SEED OIL 16 FL OZ",
            "BROWN SUGAR SYRUP", "DOMINO LT. BRN. SUGAR SYRUP",
            "POPPY SEED POWDER", "WHOLE POPPY SEEDS, GROUND",
            "GROUND SESAME SEEDS", "LOOSE LIGHT BROWN SUGAR",
            "HONEY POWDER", "CREAMED HONEY", "SWEETENED COCOA POWDER",
            "HOT COCOA POWDER", "DARK RYE FLOUR",
            "MEDIUM DARK RYE FLOUR",
        ):
            with self.subTest(name=name):
                item = {"name": name, "quantity": 1, "unit": "lb", "price": 4}
                self.assertEqual(standard_conversion_options(name), [])
                self.assertIsNone(automatic_conversion_reference(item))
                self.assertIsNone(calculate_ingredient_cost(item, 1, "cup", self.registry))
                # Known package-label facts remain authoritative even for these names.
                item.update(conversion_confirmed=True, grams_per_teaspoon=4)
                self.assertAlmostEqual(calculate_ingredient_cost(item, 1, "tsp", self.registry), 16 / 453.59237)

    def test_sourced_reference_and_receipt_identity_matches(self):
        for name, grams_per_cup in (
            ("Brand WHOLE SESAME-SEEDS 1 LB", 142),
            ("POPPY SEEDS WHOLE", 144), ("LT. BRN. SUGAR PACKED", 213),
            ("UNSWEETENED COCOA POWDER", 84), ("Brand UNSWT COCOA 8 OZ", 84),
            ("PURE LIQUID HONEY", 336), ("HONEY", 336),
            ("MED. RYE FLOUR 5 LB", 106), ("WHITE RYE FLOUR", 106),
        ):
            with self.subTest(name=name):
                item = {"name": name, "quantity": 1, "unit": "lb", "price": 4}
                self.assertIsNotNone(automatic_conversion_reference(item))
                self.assertAlmostEqual(calculate_ingredient_cost(item, 1, "cup", self.registry), grams_per_cup / 453.59237 * 4)

    def test_unspecified_cocoa_requires_type_selection(self):
        item = {"name": "COCOA POWDER", "quantity": 1, "unit": "lb", "price": 4}
        self.assertEqual(standard_conversion_options(item["name"])[0]["preparation"], "unsweetened cocoa powder")
        self.assertIsNone(automatic_conversion_reference(item))
        self.assertIsNone(calculate_ingredient_cost(item, 1, "cup", self.registry))

    def test_receipt_brown_sugar_costs_automatically_without_saved_profile(self):
        for name in (
            "LIGHT BROWN SUGAR", "Light-Brown Sugar", "DOMINO LT BRN SUGAR 2 LB",
            "SUGAR, LIGHT BROWN", "dark brown sugar",
        ):
            with self.subTest(name=name):
                item = {"name": name, "quantity": 2, "unit": "lb", "price": 3.99}
                original = dict(item)
                self.assertAlmostEqual(
                    calculate_ingredient_cost(item, 1, "cup", self.registry),
                    213 / (2 * 453.59237) * 3.99,
                )
                self.assertEqual(automatic_conversion_reference(item)["preparation"], "packed brown sugar")
                self.assertEqual(item, original)

    def test_whole_seeds_cost_automatically_but_ground_seeds_do_not(self):
        for name, grams in (("POPPY SEEDS", 144), ("SESAME-SEEDS", 142)):
            item = {"name": name, "quantity": 1, "unit": "lb", "price": 4}
            self.assertAlmostEqual(
                calculate_ingredient_cost(item, 1, "cup", self.registry),
                grams / 453.59237 * 4,
            )
            item["name"] = "GROUND " + name
            self.assertIsNone(calculate_ingredient_cost(item, 1, "cup", self.registry))

    def test_automatic_reference_never_overrides_confirmed_or_custom_density(self):
        item = {
            "name": "LIGHT BROWN SUGAR", "quantity": 1, "unit": "lb", "price": 4,
            "conversion_confirmed": True, "profile_density_g_per_cup": 190,
        }
        self.assertIsNone(automatic_conversion_reference(item))
        self.assertAlmostEqual(calculate_ingredient_cost(item, 1, "cup", self.registry), 190 / 453.59237 * 4)
        item.pop("conversion_confirmed")
        item["density_g_per_cup"] = 180
        self.assertIsNone(automatic_conversion_reference(item))
        self.assertAlmostEqual(calculate_ingredient_cost(item, 1, "cup", self.registry), 180 / 453.59237 * 4)

    def test_materially_different_names_stay_unresolved(self):
        for name in (
            "LOOSE BROWN SUGAR", "COARSE SEA SALT", "FLOUR", "PEANUT BUTTER",
            "GLUTEN FREE ALL PURPOSE FLOUR", "BROWN SUGAR SUBSTITUTE", "UNKNOWN POWDER",
        ):
            with self.subTest(name=name):
                item = {"name": name, "quantity": 1, "unit": "lb", "price": 4}
                self.assertIsNone(automatic_conversion_reference(item))
                self.assertIsNone(calculate_ingredient_cost(item, 1, "cup", self.registry))

    def test_receipt_unsalted_butter_is_not_misidentified_as_salt(self):
        item = {"name": "UNSALTED BUTTER", "quantity": 1, "unit": "lb", "price": 4}
        self.assertEqual(automatic_conversion_reference(item)["ingredient"], "Butter")
        self.assertAlmostEqual(calculate_ingredient_cost(item, 8, "tbsp", self.registry), 113 / 453.59237 * 4)

    @classmethod
    def setUpClass(cls):
        cls.registry = build_unit_registry()

    def test_direct_weight_conversion(self):
        item = {"name": "Flour", "quantity": 1, "unit": "lb", "price": 4}
        cost = calculate_ingredient_cost(item, 8, "oz", self.registry)
        self.assertAlmostEqual(cost, 2.0, places=6)

    def test_specific_sugar_density_wins(self):
        self.assertEqual(density_for_ingredient("Powdered Sugar"), 113.0)
        self.assertEqual(density_for_ingredient("Dark Brown Sugar"), 213.0)

    def test_volume_to_weight_conversion(self):
        item = {
            "name": "Powdered Sugar",
            "quantity": 240,
            "unit": "g",
            "price": 6,
            "conversion_confirmed": True,
            "profile_density_g_per_cup": 113.0,
        }
        cost = calculate_ingredient_cost(item, 1, "cup", self.registry)
        self.assertAlmostEqual(cost, 2.825, places=6)

    def test_five_pound_flour_used_by_cup(self):
        item = {
            "name": "All-purpose flour",
            "quantity": 5,
            "unit": "lb",
            "price": 6.50,
            "conversion_confirmed": True,
            "profile_density_g_per_cup": 120.0,
        }
        cost = calculate_ingredient_cost(item, 2.5, "cups", self.registry)
        expected = 300.0 / (5 * 453.59237) * 6.50
        self.assertAlmostEqual(cost, expected, places=6)

    def test_four_point_four_ounce_salt_used_by_teaspoon(self):
        item = {
            "name": "Sea salt",
            "quantity": 4.4,
            "unit": "oz",
            "price": 1.29,
            "conversion_confirmed": True,
            "grams_per_teaspoon": 6.0,
        }
        cost = calculate_ingredient_cost(item, 1, "teaspoon", self.registry)
        expected = 6.0 / (4.4 * 28.349523125) * 1.29
        self.assertAlmostEqual(cost, expected, places=6)

    def test_one_pound_butter_used_by_tablespoon(self):
        item = {
            "name": "Butter",
            "quantity": 1,
            "unit": "lb",
            "price": 4.80,
            "conversion_confirmed": True,
            "profile_density_g_per_cup": 227.0,
        }
        cost = calculate_ingredient_cost(item, 3, "tbsp", self.registry)
        expected = (3 / 16 * 227.0) / 453.59237 * 4.80
        self.assertAlmostEqual(cost, expected, places=6)

    def test_saved_density_profile_ignores_empty_database_fields(self):
        item = {
            "name": "Butter",
            "quantity": 1,
            "unit": "lb",
            "price": 4.80,
            "conversion_confirmed": True,
            "profile_density_g_per_cup": 227.0,
            "grams_per_teaspoon": math.nan,
            "package_recipe_quantity": math.nan,
            "package_recipe_unit": math.nan,
            "weight_per_item_g": math.nan,
        }
        self.assertIsNotNone(
            calculate_ingredient_cost(item, 1, "tbsp", self.registry)
        )

    def test_twelve_eggs_used_by_count(self):
        item = {"name": "Eggs", "quantity": 12, "unit": "count", "price": 4.20}
        self.assertAlmostEqual(
            calculate_ingredient_cost(item, 3, "each", self.registry),
            1.05,
        )

    def test_same_family_and_dozen_conversions(self):
        oil = {"name": "Oil", "quantity": 1, "unit": "liter", "price": 8}
        self.assertAlmostEqual(
            calculate_ingredient_cost(oil, 250, "ml", self.registry),
            2.0,
        )
        eggs = {"name": "Eggs", "quantity": 1, "unit": "dozen", "price": 6}
        self.assertAlmostEqual(
            calculate_ingredient_cost(eggs, 2, "items", self.registry),
            1.0,
        )

    def test_direct_package_conversion_and_saved_reuse(self):
        saved_item = {
            "name": "Coarse salt",
            "quantity": 4.4,
            "unit": "oz",
            "price": 1.29,
            "conversion_confirmed": True,
            "package_recipe_quantity": 64.0,
            "package_recipe_unit": "tablespoons",
        }
        first = calculate_ingredient_cost(saved_item, 2, "tbsp", self.registry)
        future_recipe = calculate_ingredient_cost(saved_item, 6, "tsp", self.registry)
        self.assertAlmostEqual(first, 2 / 64 * 1.29)
        self.assertAlmostEqual(future_recipe, 2 / 64 * 1.29)

    def test_weight_per_item_conversion(self):
        item = {
            "name": "Chocolate pieces",
            "quantity": 12,
            "unit": "count",
            "price": 6,
            "conversion_confirmed": True,
            "weight_per_item_g": 10.0,
        }
        self.assertAlmostEqual(
            calculate_ingredient_cost(item, 30, "g", self.registry),
            1.5,
        )

    def test_missing_cross_family_conversion_returns_none(self):
        item = {"name": "Unknown salt", "quantity": 4.4, "unit": "oz", "price": 1.29}
        self.assertIsNone(
            calculate_ingredient_cost(item, 1, "tsp", self.registry)
        )

    def test_verified_suggestion_requires_exact_ingredient_type(self):
        flour = standard_conversion_options("All-purpose flour")
        self.assertEqual(len(flour), 1)
        self.assertEqual(flour[0]["grams"], 120.0)
        salt_choices = standard_conversion_options("Sea salt")
        self.assertGreaterEqual(len(salt_choices), 3)
        self.assertEqual(standard_conversion_options("Unknown seed"), [])

    def test_package_label_conversion_saves_and_reuses_exact_value(self):
        profile = conversion_profile_from_label(1, "tablespoon", 9)
        self.assertEqual(profile, {"density_g_per_cup": 144.0})
        saved_item = {
            "name": "Custom ingredient",
            "quantity": 16,
            "unit": "oz",
            "price": 4.0,
            "conversion_confirmed": True,
            "profile_density_g_per_cup": profile["density_g_per_cup"],
        }
        first_cost = calculate_ingredient_cost(saved_item, 2, "tbsp", self.registry)
        reused_cost = calculate_ingredient_cost(saved_item, 6, "tsp", self.registry)
        self.assertAlmostEqual(first_cost, reused_cost)
        self.assertAlmostEqual(first_cost, 18 / 453.59237 * 4.0)

    def test_invalid_package_label_measurements_have_no_preview_profile(self):
        self.assertIsNone(conversion_profile_from_label(0, "tbsp", 9))
        self.assertIsNone(conversion_profile_from_label(1, "oz", 9))

    def test_receipt_price_update_preserves_confirmed_conversion(self):
        existing = {
            "name": "Sea salt",
            "price": 1.29,
            "conversion_confirmed": True,
            "grams_per_teaspoon": 6.0,
        }
        self.assertFalse(should_apply_receipt_conversion(existing, True))
        self.assertTrue(should_apply_receipt_conversion(None, True))
        self.assertFalse(should_apply_receipt_conversion(None, False))

    def test_baking_soda_tablespoons_cost_from_weight_package(self):
        item = {
            "name": "BAKING SODA",
            "quantity": 1,
            "unit": "lb",
            "price": 0.89,
            "density_g_per_cup": None,
            "conversion_confirmed": True,
            "profile_density_g_per_cup": 288.0,
        }
        cost = calculate_ingredient_cost(item, 20, "tbsp", self.registry)
        expected_cost = (360 / 453.59237) * 0.89
        self.assertAlmostEqual(cost, expected_cost, places=6)

    def test_custom_density_overrides_map_and_unknown_has_no_fallback(self):
        self.assertEqual(density_for_ingredient("Mystery flour", 150), 150.0)
        self.assertIsNone(density_for_ingredient("Mystery ingredient"))

    def test_invalid_conversion_returns_none(self):
        item = {"name": "Eggs", "quantity": 12, "unit": "count", "price": 4}
        self.assertIsNone(
            calculate_ingredient_cost(item, 1, "cup", self.registry)
        )

    def test_equivalent_fluid_ounce_labels_calculate_cost(self):
        aliases = [
            "fl_oz",
            "fl oz",
            "fl. oz.",
            "floz",
            "fluid ounce",
            "fluid ounces",
        ]
        for pantry_unit in aliases:
            for recipe_unit in aliases:
                with self.subTest(
                    pantry_unit=pantry_unit,
                    recipe_unit=recipe_unit,
                ):
                    item = {
                        "name": "PURE VANILLA EXTRACT",
                        "quantity": 2,
                        "unit": pantry_unit,
                        "price": 3.99,
                    }
                    cost = calculate_ingredient_cost(
                        item,
                        1,
                        recipe_unit,
                        self.registry,
                    )
                    self.assertAlmostEqual(cost, 1.995, places=6)
                    self.assertEqual(
                        normalize_unit_label(recipe_unit),
                        "fluid_ounce",
                    )

    def test_pdf_text_and_filename_are_safe(self):
        self.assertEqual(clean_pdf_text("⚠️ Crème"), "[!] Crème")
        self.assertEqual(safe_filename("Cake / 12?"), "Cake_12")

    def test_customer_estimate_price_label_matches_order_type(self):
        self.assertEqual(order_price_label("Individual item"), "Price per item")
        self.assertEqual(order_price_label("Half-dozen"), "Price per half-dozen")
        self.assertEqual(order_price_label("Dozen"), "Price per dozen")
        self.assertEqual(order_price_label("Custom box"), "Price per custom box")

    def test_customer_estimate_pdf_is_generated_for_half_dozen_order(self):
        pdf = generate_customer_estimate(
            "Chocolate chip cookies",
            "Sample customer",
            "Half-dozen",
            2,
            12,
            1,
            6.0,
            12.0,
        )
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertGreater(len(pdf), 1000)

    def test_customer_estimate_order_calculation_for_multiple_packages(self):
        self.assertEqual(
            format_order_calculation(4, "Half-dozen", 6.01, 24.04),
            "4 half-dozens × $6.01 = $24.04",
        )
        self.assertEqual(
            format_order_calculation(2, "Individual item", 3.5, 7),
            "2 items × $3.50 = $7.00",
        )
        self.assertIsNone(
            format_order_calculation(1, "Half-dozen", 6.01, 6.01)
        )

    def test_order_scaling_uses_full_batches(self):
        result = calculate_order_scale(48, 12, 6)
        self.assertEqual(result["items_requested"], 72)
        self.assertEqual(result["batches_required"], 2)
        self.assertEqual(result["items_produced"], 96)

    def test_order_quote_uses_whole_batch_price_for_exact_multiples(self):
        per_item = calculate_order_quote(24, 24, 10.85, 1.0, False, 22.0)
        self.assertFalse(per_item["whole_batch_applied"])
        self.assertEqual(per_item["quoted_total"], 24.0)

        one_batch = calculate_order_quote(24, 24, 10.85, 1.0, True, 22.0)
        self.assertEqual(one_batch["quoted_total"], 22.0)
        self.assertEqual(one_batch["batches_required"], 1)
        self.assertAlmostEqual(one_batch["gross_profit"], 11.15)

        two_batches = calculate_order_quote(24, 48, 10.85, 1.0, True, 22.0)
        self.assertEqual(two_batches["quoted_total"], 44.0)
        self.assertEqual(two_batches["production_cost"], 21.70)
        self.assertEqual(two_batches["allocated_cost"], 21.70)
        self.assertEqual(two_batches["price_difference"], 4.0)

    def test_order_quote_non_multiple_uses_chosen_per_item_price(self):
        quote = calculate_order_quote(24, 25, 10.85, 1.0, True, 22.0)
        self.assertFalse(quote["whole_batch_applied"])
        self.assertEqual(quote["quoted_total"], 25.0)
        self.assertEqual(quote["batches_required"], 2)
        self.assertEqual(quote["production_cost"], 21.70)
        self.assertAlmostEqual(quote["allocated_cost"], 10.85 / 24 * 25)
        self.assertAlmostEqual(quote["gross_profit"], 25 - 10.85 / 24 * 25)

    def test_order_quote_allocates_cost_for_one_item(self):
        quote = calculate_order_quote(24, 1, 10.85, 1.0)
        self.assertEqual(quote["quoted_total"], 1.0)
        self.assertAlmostEqual(quote["allocated_cost"], 10.85 / 24)
        self.assertAlmostEqual(quote["gross_profit"], 1.0 - 10.85 / 24)
        self.assertAlmostEqual(quote["gross_margin"], 1.0 - 10.85 / 24)
        self.assertEqual(quote["inventory_remaining"], 23)
        self.assertEqual(quote["production_cost"], 10.85)
        self.assertAlmostEqual(quote["cash_outlay_balance"], -9.85)

    def test_order_quote_for_one_and_two_complete_batches(self):
        one_batch = calculate_order_quote(24, 24, 10.85, 1.0)
        self.assertAlmostEqual(one_batch["allocated_cost"], 10.85)
        self.assertAlmostEqual(one_batch["gross_profit"], 13.15)
        self.assertEqual(one_batch["inventory_remaining"], 0)
        self.assertAlmostEqual(one_batch["batch_revenue"], 24.0)
        self.assertAlmostEqual(one_batch["batch_profit"], 13.15)

        two_batches = calculate_order_quote(24, 48, 10.85, 1.0)
        self.assertAlmostEqual(two_batches["allocated_cost"], 21.70)
        self.assertAlmostEqual(two_batches["gross_profit"], 26.30)
        self.assertEqual(two_batches["inventory_remaining"], 0)
        self.assertAlmostEqual(two_batches["production_cost"], 21.70)

    def test_pricing_methods(self):
        prices = calculate_pricing_options(50, 20, 5, 25, 0.5, 1.0)
        self.assertAlmostEqual(prices["Gross margin"], 100)
        self.assertAlmostEqual(prices["Cost markup"], 100)
        self.assertAlmostEqual(prices["Materials markup + labor"], 75)

    def test_pricing_confidence_profitable(self):
        result = calculate_pricing_confidence(36, 24, 4)
        self.assertAlmostEqual(result["cost_per_item"], 1.5)
        self.assertAlmostEqual(result["profit_per_item"], 2.5)
        self.assertAlmostEqual(result["gross_margin"], 0.625)
        self.assertAlmostEqual(result["batch_revenue"], 96)
        self.assertAlmostEqual(result["batch_profit"], 60)
        self.assertEqual(result["status"], "Profitable")

    def test_pricing_confidence_break_even_and_below_cost(self):
        self.assertEqual(
            calculate_pricing_confidence(24, 12, 2)["status"],
            "Break-even",
        )
        self.assertEqual(
            calculate_pricing_confidence(24, 12, 1.5)["status"],
            "Below cost",
        )

    def test_pricing_confidence_rejects_invalid_inputs(self):
        self.assertIsNone(calculate_pricing_confidence(20, 0, 4))
        self.assertIsNone(calculate_pricing_confidence(20, 10, 0))
        self.assertIsNone(calculate_pricing_confidence(-1, 10, 4))
        self.assertIsNone(calculate_pricing_confidence(float("nan"), 10, 4))

    def test_pricing_method_defaults_when_state_is_missing_or_invalid(self):
        pricing_options = {
            "Gross margin": 100,
            "Cost markup": 90,
        }
        missing_state = {}
        self.assertEqual(
            normalize_pricing_method(
                missing_state.get("selected_pricing_method"),
                pricing_options,
            ),
            "Gross margin",
        )
        self.assertEqual(
            normalize_pricing_method(None, pricing_options),
            "Gross margin",
        )
        self.assertEqual(
            normalize_pricing_method("Removed method", pricing_options),
            "Gross margin",
        )
        self.assertEqual(
            normalize_pricing_method("Cost markup", pricing_options),
            "Cost markup",
        )

    def test_ingredient_draft_pending_change_detection(self):
        recipe = [
            {"row_id": "row-1", "name": "Flour", "qty": 2.0, "unit": "cup"}
        ]
        unchanged = [
            {
                "row_id": "row-1",
                "name": "Flour",
                "qty": 2.0,
                "unit": "cup",
                "remove": False,
            }
        ]
        self.assertFalse(ingredient_draft_has_changes(recipe, unchanged))

        quantity_edit = [dict(unchanged[0], qty=2.5)]
        unit_edit = [dict(unchanged[0], unit="oz")]
        removal = [dict(unchanged[0], remove=True)]
        self.assertTrue(ingredient_draft_has_changes(recipe, quantity_edit))
        self.assertTrue(ingredient_draft_has_changes(recipe, unit_edit))
        self.assertTrue(ingredient_draft_has_changes(recipe, removal))

    def test_price_book_dirty_state_ignores_selection_and_row_order(self):
        saved = [
            {
                "id": 1,
                "name": "Flour",
                "quantity": 5.0,
                "unit": "lb",
                "price": 4.99,
                "density_g_per_cup": None,
            },
            {
                "id": 2,
                "name": "Sugar",
                "quantity": 4.0,
                "unit": "lb",
                "price": 3.99,
                "density_g_per_cup": 198.0,
            },
        ]
        draft = [
            dict(saved[1], **{"Add to Recipe": True, "Delete": False}),
            dict(saved[0], **{"Add to Recipe": False, "Delete": False}),
        ]
        self.assertFalse(pantry_draft_has_changes(saved, draft))
        self.assertEqual(pantry_state_signature(saved), pantry_state_signature(draft))

    def test_price_book_dirty_state_detects_edits_and_deletion(self):
        saved = [
            {
                "id": 1,
                "name": "Flour",
                "quantity": 5.0,
                "unit": "lb",
                "price": 4.99,
                "density_g_per_cup": None,
            }
        ]
        for change in (
            {"quantity": 6.0},
            {"unit": "oz"},
            {"price": 5.49},
            {"density_g_per_cup": 120.0},
            {"name": "Bread flour"},
            {"Delete": True},
        ):
            with self.subTest(change=change):
                draft = [dict(saved[0], **change)]
                self.assertTrue(pantry_draft_has_changes(saved, draft))
        self.assertTrue(pantry_draft_has_changes(saved, []))

    def test_price_book_dirty_state_resets_against_saved_baseline(self):
        saved = [
            {
                "id": 1,
                "name": "Flour",
                "quantity": 5.0,
                "unit": "lb",
                "price": 4.99,
                "density_g_per_cup": None,
            }
        ]
        edited = [dict(saved[0], price=5.49)]
        self.assertTrue(pantry_draft_has_changes(saved, edited))
        refreshed_baseline = [dict(edited[0])]
        self.assertFalse(
            pantry_draft_has_changes(refreshed_baseline, edited)
        )

    def test_select_all_changes_only_visible_pantry_rows(self):
        selected = {1, 4}
        self.assertEqual(
            update_visible_selection(selected, [2, 3], True),
            {1, 2, 3, 4},
        )
        self.assertEqual(
            update_visible_selection({1, 2, 3, 4}, [2, 3], False),
            {1, 4},
        )

    def test_master_selection_label_for_filtered_and_unfiltered_rows(self):
        self.assertEqual(
            visible_selection_control(set(), [1, 2, 3]),
            {"selected": False, "label": "Select all visible"},
        )
        self.assertEqual(
            visible_selection_control({1, 2}, [1, 2, 3]),
            {"selected": False, "label": "Select all visible"},
        )
        self.assertEqual(
            visible_selection_control({1, 2, 3}, [1, 2, 3]),
            {"selected": True, "label": "Unselect all visible"},
        )
        self.assertEqual(
            visible_selection_control({1, 2, 4}, [1, 2]),
            {"selected": True, "label": "Unselect all visible"},
        )

    def test_removing_receipt_clears_scan_state_without_touching_pantry(self):
        state = self._receipt_review_state()
        changed = reconcile_receipt_upload(state, None)
        self.assertTrue(changed)
        self._assert_receipt_review_cleared(state)
        self.assertEqual(state["pantry"], [{"id": 99, "name": "Flour"}])

    def test_replacing_receipt_clears_previous_scan_state(self):
        state = self._receipt_review_state()
        replacement = receipt_upload_signature("new.png", b"new receipt")
        changed = reconcile_receipt_upload(state, replacement)
        self.assertTrue(changed)
        self._assert_receipt_review_cleared(state)
        self.assertEqual(state["receipt_upload_signature"], replacement)

    def test_discarding_scan_results_clears_only_scan_review_state(self):
        state = self._receipt_review_state()
        clear_receipt_scan_state(state)
        self._assert_receipt_review_cleared(state)
        self.assertEqual(state["pantry"], [{"id": 99, "name": "Flour"}])

    def test_receipt_unsaved_edit_detection(self):
        baseline = [
            {"name": "Butter", "quantity": 1, "unit": "lb", "price": 4.99}
        ]
        self.assertFalse(receipt_draft_has_changes(baseline, [dict(baseline[0])]))
        self.assertTrue(
            receipt_draft_has_changes(
                baseline,
                [dict(baseline[0], price=5.49)],
            )
        )
        self.assertTrue(receipt_draft_has_changes(baseline, []))

    def test_gemini_model_configuration_and_supported_selection(self):
        models = [
            SimpleNamespace(
                name="models/gemini-2.5-flash",
                supported_actions=["generateContent", "countTokens"],
                description="Stable multimodal model",
            ),
            SimpleNamespace(
                name="models/gemini-flash-latest",
                supported_actions=["generateContent", "countTokens"],
                description="Latest multimodal flash model",
            ),
            SimpleNamespace(
                name="models/gemini-3.5-flash-lite",
                supported_actions=["generateContent", "countTokens"],
                description="Current multimodal flash-lite model",
            ),
            SimpleNamespace(
                name="models/gemini-1.5-flash",
                supported_actions=["countTokens"],
                description="Discontinued multimodal model",
            ),
        ]
        self.assertEqual(
            select_gemini_model(models),
            "gemini-3.5-flash-lite",
        )
        self.assertEqual(
            select_gemini_model(models, "gemini-2.5-flash"),
            "gemini-2.5-flash",
        )
        with self.assertRaises(ValueError):
            select_gemini_model(models, "gemini-1.5-flash")
        self.assertEqual(
            resolve_config_value(
                "GEMINI_MODEL",
                {"GEMINI_MODEL": "gemini-2.5-flash"},
                {"GEMINI_MODEL": "gemini-flash-latest"},
            ),
            "gemini-2.5-flash",
        )
        self.assertEqual(
            resolve_config_value(
                "GEMINI_MODEL",
                {},
                {"GEMINI_MODEL": "gemini-flash-latest"},
            ),
            "gemini-flash-latest",
        )

    def test_gemini_errors_are_logged_but_user_message_is_friendly(self):
        client = Mock()
        client.models.generate_content.side_effect = RuntimeError(
            "404 model endpoint unavailable"
        )
        with self.assertLogs("crumbcost_core", level="ERROR") as captured:
            with self.assertRaises(ReceiptAnalysisError) as raised:
                generate_receipt_content(client, "gemini-flash-latest", [], {})
        self.assertEqual(str(raised.exception), RECEIPT_ANALYSIS_ERROR_MESSAGE)
        self.assertNotIn("404", str(raised.exception))
        self.assertIn("404 model endpoint unavailable", "\n".join(captured.output))

    def test_price_book_recipe_start_requests_one_time_create_tab_navigation(self):
        selected_rows = [
            {"name": f"Ingredient {index}", "unit": "LB"}
            for index in range(1, 8)
        ]
        row_ids = iter(f"row-{index}" for index in range(1, 8))
        recipe_items, message = build_recipe_start_from_pantry(
            selected_rows,
            lambda: next(row_ids),
        )
        self.assertEqual(len(recipe_items), 7)
        self.assertEqual(recipe_items[0]["unit"], "lb")
        self.assertEqual(recipe_items[-1]["row_id"], "row-7")
        self.assertEqual(
            message,
            "Started a new recipe with 7 selected ingredients. "
            "Review the quantities and units below.",
        )

        state = {"active_main_tab": "Bakery Ingredient Price Book"}
        request_tab_navigation(state, "Create a Recipe")
        self.assertEqual(state["active_main_tab"], "Create a Recipe")
        self.assertEqual(
            state["pending_main_tab_navigation"],
            "Create a Recipe",
        )
        self.assertTrue(consume_tab_navigation(state))
        self.assertIsNone(state["pending_main_tab_navigation"])
        state["active_main_tab"] = "My Saved Recipes"
        self.assertFalse(consume_tab_navigation(state))
        self.assertEqual(state["active_main_tab"], "My Saved Recipes")

    @staticmethod
    def _receipt_review_state():
        return {
            "receipt_upload_initialized": True,
            "receipt_upload_signature": receipt_upload_signature(
                "old.png", b"old receipt"
            ),
            "extracted_items": [
                {"name": "Butter", "quantity": 1, "unit": "lb", "price": 4.99}
            ],
            "extracted_store": "Bakery Market",
            "receipt_extracted_baseline": [
                {"name": "Butter", "quantity": 1, "unit": "lb", "price": 4.99}
            ],
            "pending_receipt_discard": True,
            "receipt_scan_warnings": ["Review count units."],
            "receipt_editor": {"edited_rows": {0: {"price": 5.49}}},
            "receipt_count_units_verified": True,
            "receipt_package_details_verified": True,
            "pantry": [{"id": 99, "name": "Flour"}],
        }

    def _assert_receipt_review_cleared(self, state):
        self.assertEqual(state["extracted_items"], [])
        self.assertEqual(state["extracted_store"], "")
        self.assertEqual(state["receipt_extracted_baseline"], [])
        self.assertFalse(state["pending_receipt_discard"])
        self.assertEqual(state["receipt_scan_warnings"], [])
        self.assertNotIn("receipt_editor", state)
        self.assertNotIn("receipt_count_units_verified", state)
        self.assertNotIn("receipt_package_details_verified", state)

    def test_product_analysis_uses_existing_calculation_inputs(self):
        analysis = calculate_product_analysis(
            total_cost=33,
            batch_yield=24,
            chosen_price=2.75,
            target_margin=0.55,
            ingredient_costs=[("Chocolate chips", 5.10), ("Butter", 3.60)],
            labor_cost=18,
            packaging_cost=3,
        )
        self.assertAlmostEqual(analysis["cost_per_item"], 1.375)
        self.assertAlmostEqual(analysis["recommended_price"], 33 / 0.45 / 24)
        self.assertAlmostEqual(analysis["actual_margin"], 0.5)
        self.assertAlmostEqual(analysis["price_gap"], 2.75 - (33 / 0.45 / 24))
        self.assertEqual(analysis["category_drivers"][0]["name"], "Labor")

        review = format_product_analysis(analysis)
        self.assertEqual(
            review["cost_snapshot"],
            "Total batch cost: $33.00 · Cost per item: $1.38",
        )
        self.assertEqual(
            review["pricing"],
            "Chosen price: $2.75 per item · Recommended price: $3.06 per item",
        )
        self.assertIn("$0.31 per item below", review["pricing_note"])
        self.assertNotIn("**", str(review))

    def test_product_analysis_handles_missing_inputs_and_warnings(self):
        analysis = calculate_product_analysis(
            total_cost=0,
            batch_yield=0,
            chosen_price=0,
            target_margin=0.55,
            ingredient_costs=[],
            labor_cost=0,
            packaging_cost=0,
            warnings=["Unit mismatch: Baking soda."],
        )
        self.assertIsNone(analysis["cost_per_item"])
        self.assertIsNone(analysis["recommended_price"])
        self.assertIsNone(analysis["actual_margin"])
        review = format_product_analysis(analysis)
        self.assertIn("Cost per item: Not available", review["cost_snapshot"])
        self.assertIn("Unit mismatch: Baking soda.", review["warnings"])

    def test_product_analysis_exact_currency_rendering(self):
        analysis = calculate_product_analysis(
            total_cost=10.85,
            batch_yield=24,
            chosen_price=1,
            target_margin=0.55,
            ingredient_costs=[("Ingredients", 10.85)],
            labor_cost=0,
            packaging_cost=0,
        )
        review = format_product_analysis(analysis)
        self.assertEqual(
            review["cost_snapshot"],
            "Total batch cost: $10.85 · Cost per item: $0.45",
        )
        analysis["recommended_price"] = 1
        analysis["price_gap"] = 0
        review = format_product_analysis(analysis)
        self.assertEqual(
            review["pricing"],
            "Chosen price: $1.00 per item · Recommended price: $1.00 per item",
        )
        self.assertEqual(
            review["actions"],
            [
                "Before quoting a customer, update any changes in ingredient, "
                "labor, or packaging costs."
            ],
        )


if __name__ == "__main__":
    unittest.main()
