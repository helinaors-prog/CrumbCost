import re
import math
import hashlib
import logging

import pint


logger = logging.getLogger(__name__)
RECEIPT_ANALYSIS_ERROR_MESSAGE = (
    "We couldn’t analyze this receipt right now. Please try again in a moment."
)


class ReceiptAnalysisError(RuntimeError):
    pass


DENSITY_MAP = {
    "all-purpose flour": 120.0,
    "all purpose flour": 120.0,
    "bread flour": 120.0,
    "cake flour": 113.0,
    "whole wheat flour": 113.0,
    "almond flour": 96.0,
    "coconut flour": 128.0,
    "rye flour": 106.0,
    "flour": 120.0,
    "granulated sugar": 198.0,
    "cane sugar": 198.0,
    "powdered sugar": 113.0,
    "confectioners sugar": 113.0,
    "brown sugar": 213.0,
    "coconut sugar": 154.0,
    "sugar": 198.0,
    "cocoa powder": 84.0,
    "cocoa": 84.0,
    "baking soda": 288.0,
    "cornstarch": 128.0,
    "rolled oats": 89.0,
    "old-fashioned oats": 89.0,
    "quick oats": 89.0,
    "oats": 89.0,
    "chocolate chips": 170.0,
    "chocolate chunks": 170.0,
    "chopped chocolate": 170.0,
    "shredded coconut": 85.0,
    "almonds": 142.0,
    "pecans": 109.0,
    "walnuts": 113.0,
    "peanut butter": 270.0,
    "almond butter": 272.0,
    "honey": 340.0,
    "molasses": 340.0,
    "maple syrup": 312.0,
    "butter": 227.0,
    "oil": 218.0,
    "heavy cream": 227.0,
    "sour cream": 227.0,
    "cream cheese": 227.0,
    "yogurt": 227.0,
    "milk": 240.0,
    "water": 236.6,
    "vanilla extract": 208.0,
    "marinara": 245.0,
    "sauce": 245.0,
}

STANDARD_CONVERSION_SOURCE = (
    "King Arthur Baking ingredient weight chart",
    "https://www.kingarthurbaking.com/learn/ingredient-weight-chart",
)

STANDARD_INGREDIENT_CONVERSIONS = (
    {"keys": ("unsweetened cocoa", "cocoa powder"), "ingredient": "Cocoa powder", "preparation": "unsweetened cocoa powder", "quantity": 0.5, "unit": "cup", "grams": 42.0},
    {"keys": ("honey",), "ingredient": "Honey", "preparation": "liquid honey", "quantity": 1.0, "unit": "tbsp", "grams": 21.0},
    {"keys": ("medium rye flour",), "ingredient": "Medium rye flour", "preparation": "medium rye flour", "quantity": 1.0, "unit": "cup", "grams": 106.0},
    {"keys": ("white rye flour",), "ingredient": "White rye flour", "preparation": "white rye flour", "quantity": 1.0, "unit": "cup", "grams": 106.0},
    {"keys": ("brown sugar",), "ingredient": "Brown sugar", "preparation": "packed brown sugar", "quantity": 1.0, "unit": "cup", "grams": 213.0},
    {"keys": ("poppy seed", "poppy seeds"), "ingredient": "Poppy seeds", "preparation": "whole poppy seeds", "quantity": 2.0, "unit": "tbsp", "grams": 18.0},
    {"keys": ("sesame seed", "sesame seeds"), "ingredient": "Sesame seeds", "preparation": "whole sesame seeds", "quantity": 0.5, "unit": "cup", "grams": 71.0},
    {"keys": ("granulated sugar", "white sugar"), "ingredient": "White sugar", "preparation": "granulated white sugar", "quantity": 1.0, "unit": "cup", "grams": 198.0},
    {"keys": ("chocolate chips",), "ingredient": "Chocolate chips", "preparation": "chocolate chips", "quantity": 1.0, "unit": "cup", "grams": 170.0},
    {"keys": ("cornstarch", "corn starch"), "ingredient": "Cornstarch", "preparation": "cornstarch powder", "quantity": 0.25, "unit": "cup", "grams": 28.0},
    {"keys": ("all purpose flour", "all-purpose flour"), "ingredient": "All-purpose flour", "preparation": "flour", "quantity": 1.0, "unit": "cup", "grams": 120.0},
    {"keys": ("bread flour",), "ingredient": "Bread flour", "preparation": "flour", "quantity": 1.0, "unit": "cup", "grams": 120.0},
    {"keys": ("cake flour",), "ingredient": "Cake flour", "preparation": "flour", "quantity": 1.0, "unit": "cup", "grams": 113.0},
    {"keys": ("whole wheat flour",), "ingredient": "Whole-wheat flour", "preparation": "flour", "quantity": 1.0, "unit": "cup", "grams": 113.0},
    {"keys": ("almond flour",), "ingredient": "Almond flour", "preparation": "finely ground", "quantity": 1.0, "unit": "cup", "grams": 96.0},
    {"keys": ("coconut flour",), "ingredient": "Coconut flour", "preparation": "flour", "quantity": 1.0, "unit": "cup", "grams": 128.0},
    {"keys": ("baking soda",), "ingredient": "Baking soda", "preparation": "level teaspoon", "quantity": 0.5, "unit": "tsp", "grams": 3.0},
    {"keys": ("baking powder",), "ingredient": "Baking powder", "preparation": "level teaspoon", "quantity": 1.0, "unit": "tsp", "grams": 4.0},
    {"keys": ("butter",), "ingredient": "Butter", "preparation": "solid butter", "quantity": 8.0, "unit": "tbsp", "grams": 113.0},
    {"keys": ("table salt", "fine salt"), "ingredient": "Table salt", "preparation": "fine/table grind", "quantity": 1.0, "unit": "tbsp", "grams": 18.0},
    {"keys": ("diamond crystal",), "ingredient": "Kosher salt", "preparation": "Diamond Crystal", "quantity": 1.0, "unit": "tbsp", "grams": 8.0},
    {"keys": ("morton kosher", "morton's kosher"), "ingredient": "Kosher salt", "preparation": "Morton", "quantity": 1.0, "unit": "tbsp", "grams": 16.0},
    {"keys": ("old fashioned oats", "old-fashioned oats", "quick oats", "rolled oats"), "ingredient": "Oats", "preparation": "old-fashioned or quick-cooking", "quantity": 1.0, "unit": "cup", "grams": 89.0},
    {"keys": ("flax meal", "ground flax"), "ingredient": "Flax", "preparation": "ground meal", "quantity": 0.5, "unit": "cup", "grams": 50.0},
    {"keys": ("flaxseed", "flax seed"), "ingredient": "Flax", "preparation": "whole seeds", "quantity": 0.25, "unit": "cup", "grams": 35.0},
)


def normalize_ingredient_name(item_name):
    normalized = re.sub(r"[^a-z0-9]+", " ", str(item_name).casefold()).strip()
    for alias, replacement in {
        "lt brn sugar": "light brown sugar",
        "light brn sugar": "light brown sugar",
        "dk brn sugar": "dark brown sugar",
        "brn sugar": "brown sugar",
        "sugar light brown": "light brown sugar",
        "sugar dark brown": "dark brown sugar",
        "a p flour": "all purpose flour",
        "ap flour": "all purpose flour",
        "morton s": "morton",
        "unswt cocoa": "unsweetened cocoa",
        "med rye flour": "medium rye flour",
    }.items():
        normalized = re.sub(r"\b" + re.escape(alias) + r"\b", replacement, normalized)
    return normalized


def standard_conversion_options(item_name):
    """Return conservative reference matches, surfacing material ambiguities."""
    normalized = normalize_ingredient_name(item_name)
    words = set(normalized.split())
    # These describe different products, not ignorable receipt/brand text.
    if words & {"oil", "syrup", "extract", "flavored", "flavour", "flavor", "drink", "beverage", "creamed", "crystallized"}:
        return []
    if "brown sugar" in normalized and words & {"loose", "unpacked", "powder", "powdered", "liquid"}:
        return []
    if any(seed in normalized for seed in ("poppy seed", "sesame seed")) and words & {"powder", "powdered", "ground", "meal", "paste", "crushed", "flour"}:
        return []
    if "honey" in words and words & {"powder", "powdered", "granulated", "dried", "comb"}:
        return []
    if "cocoa" in words and words & {"sweetened", "instant", "hot", "butter", "nibs"}:
        return []
    if "rye" in words and "flour" in words and (not words & {"medium", "white"} or words & {"dark", "whole", "pumpernickel"}):
        return []
    if any(word in normalized.split() for word in ("blend", "mix", "substitute", "spread", "whipped", "filling")) or "gluten free" in normalized:
        return []
    if "butter" in normalized and any(word in normalized.split() for word in ("peanut", "almond", "cocoa", "apple")):
        return []
    if "salt" in normalized.split() and not any(
        marker in normalized
        for marker in ("table salt", "fine salt", "diamond crystal", "morton kosher")
    ):
        return [
            entry for entry in STANDARD_INGREDIENT_CONVERSIONS
            if entry["ingredient"] in {"Table salt", "Kosher salt"}
        ]
    if "flour" in normalized and not any(
        key in normalized
        for key in (
            "all purpose", "bread flour", "cake flour", "whole wheat",
            "almond flour", "coconut flour", "medium rye flour", "white rye flour",
        )
    ):
        return [
            entry for entry in STANDARD_INGREDIENT_CONVERSIONS
            if entry["ingredient"] in {
                "All-purpose flour", "Bread flour", "Cake flour",
                "Whole-wheat flour", "Almond flour", "Coconut flour",
            }
        ]
    if "flax" in normalized and not any(
        key in normalized for key in ("meal", "ground", "flaxseed", "flax seed")
    ):
        return [
            entry for entry in STANDARD_INGREDIENT_CONVERSIONS
            if entry["ingredient"] == "Flax"
        ]
    matches = []
    for entry in STANDARD_INGREDIENT_CONVERSIONS:
        if any(
            re.search(r"\b" + re.escape(normalize_ingredient_name(key)) + r"\b", normalized)
            for key in entry["keys"]
        ):
            matches.append(entry)
    if "flax" in normalized and any(word in normalized.split() for word in ("ground", "meal")):
        matches = [entry for entry in matches if entry["preparation"] == "ground meal"]
    return matches


def automatic_conversion_reference(pantry_item):
    if pantry_item.get("conversion_confirmed"):
        return None
    try:
        custom_density = float(pantry_item.get("density_g_per_cup"))
        if math.isfinite(custom_density) and custom_density > 0:
            return None
    except (TypeError, ValueError):
        pass
    normalized = normalize_ingredient_name(pantry_item.get("name", ""))
    if any(word in normalized.split() for word in ("loose", "unpacked")):
        return None
    options = standard_conversion_options(normalized)
    if len(options) != 1:
        return None
    reference = options[0]
    if reference["ingredient"] == "Cocoa powder" and "unsweetened" not in normalized.split():
        # A plain receipt label does not establish that this isn't sweetened cocoa.
        return None
    if reference["preparation"].startswith("whole") and any(
        word in normalized.split() for word in ("ground", "meal", "paste", "crushed")
    ):
        return None
    return reference


def conversion_profile_from_label(quantity, unit, grams):
    quantity = float(quantity)
    grams = float(grams)
    if not math.isfinite(quantity) or not math.isfinite(grams) or quantity <= 0 or grams <= 0:
        return None
    normalized_unit = normalize_unit_label(unit)
    grams_per_unit = grams / quantity
    if normalized_unit == "tsp":
        return {"grams_per_teaspoon": grams_per_unit}
    if normalized_unit == "tbsp":
        return {"density_g_per_cup": grams_per_unit * 16.0}
    if normalized_unit == "cup":
        return {"density_g_per_cup": grams_per_unit}
    if normalized_unit == "count":
        return {"weight_per_item_g": grams_per_unit}
    return None


def build_unit_registry():
    registry = pint.UnitRegistry()
    registry.define("count = 1 = item = piece = each = jar = can = bottle")
    registry.define("dozen = 12 * count")
    return registry


def density_for_ingredient(item_name, custom_density=None):
    try:
        custom_value = float(custom_density)
        if math.isfinite(custom_value) and custom_value > 0:
            return custom_value
    except (TypeError, ValueError):
        pass
    normalized_name = str(item_name).casefold()
    candidates = DENSITY_MAP.items()
    for name, density in sorted(
        candidates,
        key=lambda entry: len(entry[0]),
        reverse=True,
    ):
        if name in normalized_name:
            return density
    return None


def normalize_unit_label(unit):
    normalized = str(unit).casefold().strip()
    compact = re.sub(r"[\s._-]+", "", normalized)
    aliases = {
        "ounce": "oz",
        "ounces": "oz",
        "pound": "lb",
        "pounds": "lb",
        "gram": "g",
        "grams": "g",
        "kilogram": "kg",
        "kilograms": "kg",
        "teaspoon": "tsp",
        "teaspoons": "tsp",
        "tablespoon": "tbsp",
        "tablespoons": "tbsp",
        "floz": "fluid_ounce",
        "fluidounce": "fluid_ounce",
        "fluidounces": "fluid_ounce",
        "cups": "cup",
        "pints": "pint",
        "quarts": "quart",
        "gallons": "gallon",
        "milliliter": "ml",
        "milliliters": "ml",
        "millilitre": "ml",
        "millilitres": "ml",
        "l": "liter",
        "litre": "liter",
        "litres": "liter",
        "liters": "liter",
        "items": "count",
        "item": "count",
        "counts": "count",
        "count": "count",
        "each": "count",
        "dozens": "dozen",
    }
    return aliases.get(compact, normalized)


def unit_family(unit, registry):
    try:
        dimensionality = registry(normalize_unit_label(unit)).dimensionality
    except (TypeError, ValueError, pint.PintError):
        return None
    if dimensionality == registry.gram.dimensionality:
        return "weight"
    if dimensionality == registry.liter.dimensionality:
        return "volume"
    if dimensionality == registry.count.dimensionality:
        return "count"
    return None


def calculate_ingredient_cost(pantry_item, recipe_qty, recipe_unit, registry):
    try:
        def positive_number(value):
            try:
                number = float(value)
            except (TypeError, ValueError):
                return None
            return number if math.isfinite(number) and number > 0 else None

        pantry_unit = normalize_unit_label(pantry_item["unit"])
        recipe_unit = normalize_unit_label(recipe_unit)
        pantry_total = pantry_item["quantity"] * registry(pantry_unit)
        recipe_used = recipe_qty * registry(recipe_unit)

        try:
            converted_qty = recipe_used.to(pantry_total.units)
            return converted_qty.magnitude * (
                pantry_item["price"] / pantry_total.magnitude
            )
        except pint.DimensionalityError:
            pass

        profile_confirmed = bool(pantry_item.get("conversion_confirmed", False))
        direct_quantity = positive_number(pantry_item.get("package_recipe_quantity"))
        direct_unit = pantry_item.get("package_recipe_unit")
        if (
            profile_confirmed
            and direct_quantity is not None
            and isinstance(direct_unit, str)
            and direct_unit.strip()
        ):
            try:
                direct_total = float(direct_quantity) * registry(
                    normalize_unit_label(direct_unit)
                )
                converted_qty = recipe_used.to(direct_total.units)
                return converted_qty.magnitude * (
                    pantry_item["price"] / direct_total.magnitude
                )
            except pint.DimensionalityError:
                pass

        pantry_family = unit_family(pantry_unit, registry)
        recipe_family = unit_family(recipe_unit, registry)
        weight_per_item = positive_number(pantry_item.get("weight_per_item_g"))
        if profile_confirmed and weight_per_item is not None:
            if pantry_family == "count" and recipe_family == "weight":
                package_grams = pantry_total.to(registry.count).magnitude * weight_per_item
                recipe_grams = recipe_used.to(registry.gram).magnitude
                return recipe_grams * pantry_item["price"] / package_grams
            if pantry_family == "weight" and recipe_family == "count":
                package_grams = pantry_total.to(registry.gram).magnitude
                recipe_grams = recipe_used.to(registry.count).magnitude * weight_per_item
                return recipe_grams * pantry_item["price"] / package_grams

        grams_per_teaspoon = positive_number(pantry_item.get("grams_per_teaspoon"))
        profile_density = positive_number(pantry_item.get("profile_density_g_per_cup"))
        if profile_confirmed and grams_per_teaspoon is not None:
            density = grams_per_teaspoon * 48.0
        elif profile_confirmed and profile_density is not None:
            density = profile_density
        else:
            density = positive_number(pantry_item.get("density_g_per_cup"))
            reference = automatic_conversion_reference(pantry_item)
            if density is None and reference:
                reference_profile = conversion_profile_from_label(
                    reference["quantity"], reference["unit"], reference["grams"]
                )
                density = reference_profile.get("density_g_per_cup")
                if density is None and reference_profile.get("grams_per_teaspoon"):
                    density = reference_profile["grams_per_teaspoon"] * 48.0
        if density is None:
            return None
        if pantry_family == "weight" and recipe_family == "volume":
            cups_used = recipe_used.to(registry.cup).magnitude
            grams_used = cups_used * density
            pantry_grams = pantry_total.to(registry.gram).magnitude
            return grams_used * (pantry_item["price"] / pantry_grams)
        if pantry_family == "volume" and recipe_family == "weight":
            pantry_cups = pantry_total.to(registry.cup).magnitude
            pantry_grams = pantry_cups * density
            recipe_grams = recipe_used.to(registry.gram).magnitude
            return recipe_grams * (pantry_item["price"] / pantry_grams)
        return None
    except (ArithmeticError, KeyError, TypeError, ValueError, pint.PintError):
        return None


def should_apply_receipt_conversion(existing_item, suggestion_confirmed):
    """Receipt suggestions never replace a conversion the user already confirmed."""
    return bool(suggestion_confirmed) and not bool(
        existing_item and existing_item.get("conversion_confirmed")
    )


def clean_pdf_text(text):
    return re.sub(
        r"[^\x00-\xFF]",
        "",
        str(text).replace("⚠️", "[!]"),
    ).strip()


def safe_filename(value, fallback="Recipe"):
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value or fallback)
    return cleaned.strip("._") or fallback


def calculate_pricing_options(
    total_cogs,
    ingredient_cost,
    packaging_cost,
    labor_cost,
    target_margin,
    markup_rate,
):
    return {
        "Gross margin": total_cogs / (1.0 - target_margin),
        "Cost markup": total_cogs * (1.0 + markup_rate),
        "Materials markup + labor": labor_cost
        + (ingredient_cost + packaging_cost) * (1.0 + markup_rate),
    }


def calculate_pricing_confidence(total_batch_cost, batch_yield, selling_price):
    try:
        total_batch_cost = float(total_batch_cost)
        batch_yield = int(batch_yield)
        selling_price = float(selling_price)
    except (TypeError, ValueError, OverflowError):
        return None

    if (
        not math.isfinite(total_batch_cost)
        or not math.isfinite(selling_price)
        or total_batch_cost < 0
        or batch_yield <= 0
        or selling_price <= 0
    ):
        return None

    cost_per_item = total_batch_cost / batch_yield
    profit_per_item = selling_price - cost_per_item
    gross_margin = profit_per_item / selling_price
    batch_revenue = selling_price * batch_yield
    batch_profit = batch_revenue - total_batch_cost
    if profit_per_item > 0.005:
        status = "Profitable"
    elif profit_per_item < -0.005:
        status = "Below cost"
    else:
        status = "Break-even"

    return {
        "cost_per_item": cost_per_item,
        "profit_per_item": profit_per_item,
        "gross_margin": gross_margin,
        "batch_revenue": batch_revenue,
        "batch_profit": batch_profit,
        "status": status,
    }


def normalize_pricing_method(selected_method, pricing_options):
    if selected_method in pricing_options:
        return selected_method
    if "Gross margin" in pricing_options:
        return "Gross margin"
    return next(iter(pricing_options), None)


def ingredient_draft_has_changes(recipe_items, draft_items):
    if len(recipe_items) != len(draft_items):
        return True

    current_by_id = {item.get("row_id"): item for item in recipe_items}
    for draft in draft_items:
        current = current_by_id.get(draft.get("row_id"))
        if current is None or bool(draft.get("remove")):
            return True
        try:
            quantity_changed = abs(
                float(draft.get("qty")) - float(current.get("qty"))
            ) > 1e-9
        except (TypeError, ValueError):
            return True
        if quantity_changed or str(draft.get("unit")) != str(current.get("unit")):
            return True

    return False


def pantry_state_signature(rows):
    def optional_float(value):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    normalized = []
    for row in rows:
        try:
            row_id = int(row["id"])
            quantity = float(row["quantity"])
            price = float(row["price"])
        except (KeyError, TypeError, ValueError, OverflowError):
            return None
        normalized.append(
            (
                row_id,
                str(row.get("name", "")),
                quantity,
                str(row.get("unit", "")),
                price,
                optional_float(row.get("density_g_per_cup")),
                bool(row.get("Delete", False)),
            )
        )
    return tuple(sorted(normalized, key=lambda row: row[0]))


def pantry_draft_has_changes(saved_rows, draft_rows):
    return pantry_state_signature(saved_rows) != pantry_state_signature(draft_rows)


def update_visible_selection(selected_ids, visible_ids, select_all):
    selected = {int(row_id) for row_id in selected_ids}
    visible = {int(row_id) for row_id in visible_ids}
    return selected | visible if select_all else selected - visible


def visible_selection_control(selected_ids, visible_ids):
    selected = {int(row_id) for row_id in selected_ids}
    visible = {int(row_id) for row_id in visible_ids}
    all_visible_selected = bool(visible and visible.issubset(selected))
    return {
        "selected": all_visible_selected,
        "label": (
            "Unselect all visible"
            if all_visible_selected
            else "Select all visible"
        ),
    }


def receipt_upload_signature(file_name, file_bytes):
    if file_bytes is None:
        return None
    digest = hashlib.sha256(file_bytes).hexdigest()
    return f"{file_name or ''}:{digest}"


def clear_receipt_scan_state(state, clear_widget_state=True):
    state["extracted_items"] = []
    state["extracted_store"] = ""
    state["receipt_extracted_baseline"] = []
    state["pending_receipt_discard"] = False
    state["receipt_scan_warnings"] = []
    state["receipt_cleanup_pending"] = False
    if clear_widget_state:
        state.pop("receipt_editor", None)
        state.pop("receipt_count_units_verified", None)
        state.pop("receipt_package_details_verified", None)


def reconcile_receipt_upload(state, current_signature):
    initialized = bool(state.get("receipt_upload_initialized", False))
    changed = initialized and state.get("receipt_upload_signature") != current_signature
    if changed:
        clear_receipt_scan_state(state)
    state["receipt_upload_signature"] = current_signature
    state["receipt_upload_initialized"] = True
    return changed


def receipt_draft_has_changes(extracted_rows, draft_rows):
    def normalize(rows):
        normalized = []
        for row in rows:
            try:
                quantity = float(row.get("quantity"))
                price = float(row.get("price"))
            except (TypeError, ValueError, OverflowError):
                return None
            normalized.append(
                (
                    str(row.get("name", "")),
                    quantity,
                    str(row.get("unit", "")),
                    price,
                )
            )
        return tuple(normalized)

    return normalize(extracted_rows) != normalize(draft_rows)


def resolve_config_value(name, environ, secrets=None):
    environment_value = environ.get(name)
    if environment_value:
        return str(environment_value).strip()
    if secrets is not None:
        try:
            secret_value = secrets.get(name)
        except Exception:
            secret_value = None
        if secret_value:
            return str(secret_value).strip()
    return None


def select_gemini_model(available_models, configured_model=None):
    supported = []
    for model in available_models:
        name = str(getattr(model, "name", "") or "")
        actions = set(getattr(model, "supported_actions", None) or [])
        if (
            name.startswith("models/gemini-")
            and "generateContent" in actions
            and any(family in name.casefold() for family in ("flash", "pro"))
            and not any(
                excluded in name.casefold()
                for excluded in ("-tts", "-image", "transcribe", "computer-use")
            )
        ):
            supported.append(name)

    if configured_model:
        configured_name = str(configured_model).strip()
        full_name = (
            configured_name
            if configured_name.startswith("models/")
            else f"models/{configured_name}"
        )
        if full_name not in supported:
            raise ValueError(
                f"Configured Gemini model is unavailable or incompatible: {full_name}"
            )
        return full_name.removeprefix("models/")

    preferred_models = (
        "models/gemini-3.5-flash-lite",
        "models/gemini-flash-latest",
    )
    for preferred_model in preferred_models:
        if preferred_model in supported:
            return preferred_model.removeprefix("models/")

    stable_flash = [
        name
        for name in supported
        if "flash" in name and "preview" not in name and "latest" not in name
    ]
    if stable_flash:
        return sorted(stable_flash, reverse=True)[0].removeprefix("models/")
    if supported:
        return sorted(supported)[0].removeprefix("models/")
    raise ValueError("No compatible Gemini generate-content model is available.")


def generate_receipt_content(client, model_name, contents, config):
    try:
        return client.models.generate_content(
            model=model_name,
            contents=contents,
            config=config,
        )
    except Exception as err:
        logger.exception("Gemini receipt analysis failed: %s", err)
        raise ReceiptAnalysisError(RECEIPT_ANALYSIS_ERROR_MESSAGE) from None


def build_recipe_start_from_pantry(selected_rows, row_id_factory):
    recipe_items = [
        {
            "name": row["name"],
            "qty": 1.0,
            "unit": str(row["unit"]).lower().strip(),
            "row_id": row_id_factory(),
        }
        for row in selected_rows
    ]
    count = len(recipe_items)
    message = (
        f"Started a new recipe with {count} selected ingredient"
        f"{'s' if count != 1 else ''}. Review the quantities and units below."
    )
    return recipe_items, message


def request_tab_navigation(state, tab_name):
    state["active_main_tab"] = tab_name
    state["pending_main_tab_navigation"] = tab_name


def consume_tab_navigation(state):
    pending_tab = state.get("pending_main_tab_navigation")
    if pending_tab and state.get("active_main_tab") == pending_tab:
        state["pending_main_tab_navigation"] = None
        return True
    return False


def calculate_product_analysis(
    total_cost,
    batch_yield,
    chosen_price,
    target_margin,
    ingredient_costs,
    labor_cost,
    packaging_cost,
    warnings=None,
):
    total_cost = float(total_cost)
    batch_yield = int(batch_yield)
    chosen_price = float(chosen_price)
    target_margin = float(target_margin)
    warnings = list(warnings or [])

    cost_per_item = total_cost / batch_yield if batch_yield > 0 else None
    recommended_price = (
        total_cost / (1.0 - target_margin) / batch_yield
        if batch_yield > 0 and 0 <= target_margin < 1
        else None
    )
    actual_margin = (
        (chosen_price - cost_per_item) / chosen_price
        if chosen_price > 0 and cost_per_item is not None
        else None
    )

    category_costs = {
        "Ingredients": float(sum(cost for _, cost in ingredient_costs)),
        "Labor": float(labor_cost),
        "Packaging": float(packaging_cost),
    }
    category_drivers = [
        {
            "name": name,
            "cost": cost,
            "percent": cost / total_cost if total_cost > 0 else None,
        }
        for name, cost in sorted(
            category_costs.items(), key=lambda item: item[1], reverse=True
        )
    ]
    ingredient_drivers = [
        {
            "name": str(name),
            "cost": float(cost),
            "percent": float(cost) / total_cost if total_cost > 0 else None,
        }
        for name, cost in sorted(
            ingredient_costs, key=lambda item: item[1], reverse=True
        )[:2]
    ]

    return {
        "total_cost": total_cost,
        "cost_per_item": cost_per_item,
        "chosen_price": chosen_price if chosen_price > 0 else None,
        "target_margin": target_margin,
        "recommended_price": recommended_price,
        "actual_margin": actual_margin,
        "price_gap": (
            chosen_price - recommended_price
            if chosen_price > 0 and recommended_price is not None
            else None
        ),
        "category_drivers": category_drivers,
        "ingredient_drivers": ingredient_drivers,
        "warnings": warnings,
    }


def format_product_analysis(analysis):
    def currency(value):
        return "Not available" if value is None else f"${value:,.2f}"

    actual_margin = analysis["actual_margin"]
    target_margin = analysis["target_margin"]
    if actual_margin is None:
        margin_text = "Margin health is incomplete until the warnings below are resolved."
    elif actual_margin + 1e-9 < target_margin:
        margin_text = (
            f"The chosen price produces a {actual_margin * 100:.1f}% margin, "
            f"which is {(target_margin - actual_margin) * 100:.1f} percentage "
            f"points below the {target_margin * 100:.0f}% target."
        )
    else:
        margin_text = (
            f"The chosen price produces a {actual_margin * 100:.1f}% margin, "
            f"meeting the {target_margin * 100:.0f}% target."
        )
    if actual_margin is not None and analysis["warnings"]:
        margin_text += " This result is provisional because costs are missing or unresolved."

    price_gap = analysis["price_gap"]
    if price_gap is None:
        price_text = "Enter a valid yield and chosen price to compare prices."
    else:
        price_text = (
            f"Chosen price: {currency(analysis['chosen_price'])} per item · "
            f"Recommended price: {currency(analysis['recommended_price'])} per item"
        )
    price_note = (
        f"The chosen price is {currency(abs(price_gap))} per item below the recommendation."
        if price_gap is not None and price_gap < -0.005
        else None
    )

    driver_lines = [
        f"{driver['name']}: {currency(driver['cost'])}"
        + (
            f" · {driver['percent'] * 100:.1f}% of total cost"
            if driver["percent"] is not None
            else ""
        )
        for driver in analysis["category_drivers"]
        if driver["cost"] > 0
    ]
    ingredient_lines = [
        f"{driver['name']}: {currency(driver['cost'])}"
        f" · {driver['percent'] * 100:.1f}% of total cost"
        for driver in analysis["ingredient_drivers"]
        if driver["percent"] is not None
    ]
    warning_lines = list(analysis["warnings"])

    actions = []
    if analysis["warnings"]:
        actions.append("Resolve the listed cost or unit issues, then run the analysis again.")
    if price_gap is not None and price_gap < -0.005:
        actions.append(
            f"Apply the {currency(analysis['recommended_price'])} per-item "
            "recommendation or deliberately accept a margin below target."
        )
    if not actions:
        actions.append(
            "Before quoting a customer, update any changes in ingredient, labor, "
            "or packaging costs."
        )

    snapshot = (
        f"Total batch cost: {currency(analysis['total_cost'])} · "
        f"Cost per item: {currency(analysis['cost_per_item'])}"
    )
    return {
        "cost_snapshot": snapshot,
        "margin_health": margin_text,
        "pricing": price_text,
        "pricing_note": price_note,
        "cost_drivers": driver_lines or ["No cost drivers are available."],
        "ingredient_drivers": ingredient_lines,
        "warnings": warning_lines or ["No missing costs or needed conversions detected."],
        "actions": actions[:2],
    }


def calculate_order_scale(batch_yield, units_per_package, package_count):
    items_requested = int(units_per_package * package_count)
    batches_required = max(1, math.ceil(items_requested / batch_yield))
    return {
        "items_requested": items_requested,
        "batches_required": batches_required,
        "items_produced": int(batches_required * batch_yield),
    }


def calculate_order_quote(
    batch_yield,
    requested_items,
    total_batch_cost,
    chosen_price_per_item,
    use_whole_batch_price=False,
    whole_batch_price=0,
):
    try:
        batch_yield = int(batch_yield)
        requested_items = int(requested_items)
        total_batch_cost = float(total_batch_cost)
        chosen_price_per_item = float(chosen_price_per_item)
        whole_batch_price = float(whole_batch_price)
    except (TypeError, ValueError, OverflowError):
        return None

    if (
        batch_yield <= 0
        or requested_items <= 0
        or total_batch_cost < 0
        or chosen_price_per_item <= 0
        or not all(
            math.isfinite(value)
            for value in (
                total_batch_cost,
                chosen_price_per_item,
                whole_batch_price,
            )
        )
    ):
        return None

    batches_required = math.ceil(requested_items / batch_yield)
    items_produced = batches_required * batch_yield
    production_cost = batches_required * total_batch_cost
    cost_per_item = total_batch_cost / batch_yield
    allocated_cost = requested_items * cost_per_item
    inventory_remaining = items_produced - requested_items
    normal_total = requested_items * chosen_price_per_item
    whole_batch_eligible = requested_items % batch_yield == 0
    whole_batch_applied = (
        bool(use_whole_batch_price)
        and whole_batch_eligible
        and whole_batch_price > 0
    )
    quoted_total = (
        batches_required * whole_batch_price
        if whole_batch_applied
        else normal_total
    )
    gross_profit = quoted_total - allocated_cost
    gross_margin = gross_profit / quoted_total if quoted_total > 0 else None
    batch_revenue = batch_yield * chosen_price_per_item
    batch_profit = batch_revenue - total_batch_cost
    batch_margin = batch_profit / batch_revenue if batch_revenue > 0 else None
    cash_outlay_balance = quoted_total - production_cost
    price_difference = normal_total - quoted_total if whole_batch_applied else 0.0

    return {
        "requested_items": requested_items,
        "batches_required": batches_required,
        "items_produced": items_produced,
        "inventory_remaining": inventory_remaining,
        "cost_per_item": cost_per_item,
        "allocated_cost": allocated_cost,
        "production_cost": production_cost,
        "cash_outlay_balance": cash_outlay_balance,
        "normal_per_item_total": normal_total,
        "quoted_total": quoted_total,
        "gross_profit": gross_profit,
        "gross_margin": gross_margin,
        "batch_revenue": batch_revenue,
        "batch_profit": batch_profit,
        "batch_margin": batch_margin,
        "whole_batch_eligible": whole_batch_eligible,
        "whole_batch_applied": whole_batch_applied,
        "price_difference": price_difference,
        "price_difference_percent": (
            price_difference / normal_total if whole_batch_applied else 0.0
        ),
    }
