import io
import importlib
import html
import json
import os
import sqlite3
import logging
import math
from datetime import date
from pathlib import Path
from uuid import uuid4

import pandas as pd
import altair as alt
from PIL import Image
from pydantic import BaseModel, Field, field_validator
import streamlit as st
from fpdf import FPDF

from google import genai
from google.genai import types

import crumbcost_core
import crumbcost_pdf

# Streamlit reruns app.py without always reloading imported local modules.
# Explicit reloads keep helper functions synchronized during development.
importlib.reload(crumbcost_core)
importlib.reload(crumbcost_pdf)

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
    normalize_pricing_method,
    normalize_unit_label,
    should_apply_receipt_conversion,
    standard_conversion_options,
    STANDARD_CONVERSION_SOURCE,
    automatic_conversion_reference,
    safe_filename,
    update_visible_selection,
    visible_selection_control,
)
from crumbcost_pdf import generate_customer_estimate, generate_recipe_estimate


logger = logging.getLogger(__name__)


# ---------------------------------------------------------
# 1. APP CONFIGURATION
# ---------------------------------------------------------

st.set_page_config(
    page_title="CrumbCost",
    page_icon="🧁",
    layout="wide",
)

st.markdown(
    """
    <style>
    .block-container {
        max-width: 1380px;
        padding-top: 2rem;
        padding-bottom: 4rem;
    }
    h1, h2, h3 {letter-spacing: -0.025em;}
    h1 {font-weight: 750;}
    h2, h3 {font-weight: 680;}
    .crumbcost-subtitle {
        margin-top: -0.7rem;
        margin-bottom: 0.45rem;
        opacity: 0.72;
        font-size: 1.05rem;
        font-weight: 500;
        line-height: 1.35;
    }
    .unit-mismatch-cell {
        display: inline-block;
        margin-top: 0.55rem;
        padding: 0.25rem 0.45rem;
        border: 1px solid currentColor;
        border-radius: 0.5rem;
        cursor: help;
        text-decoration: underline dotted;
        text-underline-offset: 0.2rem;
    }
    [data-testid="stMetric"] {
        min-height: 6.2rem;
        border-radius: 0.85rem;
        padding: 0.95rem 1rem;
    }
    [data-testid="stExpander"] {
        border-radius: 0.85rem;
    }
    [data-testid="stForm"] {
        border-radius: 0.85rem;
    }
    [data-baseweb="input"] > div,
    [data-baseweb="select"] > div,
    [data-baseweb="textarea"] > div {border-radius: 0.65rem;}
    [data-testid="stButton"] button,
    [data-testid="stDownloadButton"] button {
        min-height: 2.65rem;
        border-radius: 0.65rem;
        font-weight: 600;
        white-space: nowrap;
        overflow: visible;
    }
    [data-testid="stDataFrame"] {border-radius: 0.85rem; overflow: hidden;}
    .st-key-pantry_selection_table {position: relative;}
    .st-key-pantry_select_all_visible {
        position: absolute;
        z-index: 3;
        top: 0.1rem;
        left: 0.65rem;
        max-width: 12rem;
    }
    .st-key-pantry_select_all_visible [data-testid="stCheckbox"] {
        min-height: 2.15rem;
        align-items: center;
    }
    @media (max-width: 700px) {
        .block-container {padding: 1rem 0.8rem 2.5rem;}
        .crumbcost-subtitle {font-size: 0.95rem;}
        [data-testid="stHorizontalBlock"] {gap: 0.55rem;}
    }
    </style>
    """,
    unsafe_allow_html=True,
)

DB_FILE = Path(__file__).resolve().with_name("bakery_calc.db")
BACKUP_DIR = Path(__file__).resolve().with_name("backups")
RECIPE_UNITS = [
    "tsp", "tbsp", "cup", "fl oz", "ml", "liter", "oz", "lb", "g", "kg",
    "pint", "quart", "gallon", "item", "count", "each", "dozen",
]
WEIGHT_UNITS = {"g", "kg", "oz", "lb"}
VOLUME_UNITS = {
    "tsp", "tbsp", "cup", "fl_oz", "ml", "l", "pint", "quart", "gallon"
}
COUNT_UNITS = {"count"}
PRODUCT_TYPES = [
    "Cookies",
    "Cupcakes",
    "Cakes",
    "Breads",
    "Pastries",
    "Pies",
    "Brownies",
    "Catering Trays",
    "Other",
]
SELLING_FORMAT_SIZES = {
    "Individual item": 1,
    "Half-dozen": 6,
    "Dozen": 12,
    "Two-dozen": 24,
    "Slice": 1,
    "Loaf": 1,
    "Individual serving": 1,
    "Whole batch": None,
    "Whole cake": None,
    "Whole pie": None,
    "Tray": None,
    "Custom box": None,
    "Custom order": None,
    "Custom tray": None,
}
SELLING_FORMATS_BY_PRODUCT = {
    "Cookies": ["Individual item", "Half-dozen", "Dozen", "Two-dozen", "Custom box", "Whole batch"],
    "Cupcakes": ["Individual item", "Half-dozen", "Dozen", "Two-dozen", "Custom box", "Whole batch"],
    "Cakes": ["Whole cake", "Slice", "Custom order"],
    "Breads": ["Loaf", "Slice", "Half-dozen", "Dozen", "Custom order"],
    "Pastries": ["Individual item", "Half-dozen", "Dozen", "Two-dozen", "Custom box", "Whole batch"],
    "Pies": ["Whole pie", "Slice", "Custom order"],
    "Brownies": ["Individual item", "Half-dozen", "Dozen", "Two-dozen", "Custom box", "Whole batch"],
    "Catering Trays": ["Tray", "Individual serving", "Custom tray"],
    "Other": ["Individual item", "Whole batch", "Custom order"],
}
WHOLE_BATCH_FORMATS = {"Whole batch", "Whole cake", "Whole pie", "Tray"}
CUSTOM_FORMATS = {"Custom box", "Custom order", "Custom tray"}


# ---------------------------------------------------------
# 2. GOOGLE LOGIN
# ---------------------------------------------------------

if not st.user.is_logged_in:

    st.title("🧁 Welcome to CrumbCost")

    st.write(
        "Sign in with Google to save and access your own "
        "private bakery ingredient price book and recipes."
    )

    if st.button("Sign in with Google", type="primary"):
        st.login()

    st.stop()


# ---------------------------------------------------------
# 3. DATABASE SETUP
# ---------------------------------------------------------

def get_connection():
    conn = sqlite3.connect(DB_FILE, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def create_daily_backup():
    if not DB_FILE.exists():
        return None
    BACKUP_DIR.mkdir(exist_ok=True)
    backup_path = BACKUP_DIR / f"crumbcost-{date.today().isoformat()}.db"
    if not backup_path.exists():
        source = sqlite3.connect(DB_FILE)
        destination = sqlite3.connect(backup_path)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
    return backup_path


def init_db():
    conn = get_connection()
    c = conn.cursor()

    # Users table
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            google_id TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE,
            name TEXT
        )
    """)

    # Pantry table
    c.execute("""
        CREATE TABLE IF NOT EXISTS pantry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            quantity REAL NOT NULL,
            unit TEXT NOT NULL,
            price REAL NOT NULL,
            UNIQUE(user_id, name),
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)

    # Recipes table
    c.execute("""
        CREATE TABLE IF NOT EXISTS recipes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            product_type TEXT NOT NULL DEFAULT 'Other',
            batch_yield INTEGER NOT NULL,
            labor_rate REAL DEFAULT 0,
            prep_hours REAL DEFAULT 0,
            packaging_cost REAL DEFAULT 0,
            target_margin REAL DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)

    # Recipe ingredients table
    c.execute("""
        CREATE TABLE IF NOT EXISTS recipe_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id INTEGER NOT NULL,
            ingredient_name TEXT NOT NULL,
            quantity REAL NOT NULL,
            unit TEXT NOT NULL,
            FOREIGN KEY(recipe_id) REFERENCES recipes(id)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS pantry_price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pantry_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            quantity REAL NOT NULL,
            unit TEXT NOT NULL,
            price REAL NOT NULL,
            recorded_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(pantry_id) REFERENCES pantry(id),
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS ingredient_conversion_profiles (
            pantry_id INTEGER PRIMARY KEY,
            density_g_per_cup REAL,
            grams_per_teaspoon REAL,
            package_recipe_quantity REAL,
            package_recipe_unit TEXT,
            weight_per_item_g REAL,
            confirmed INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT 'user',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(pantry_id) REFERENCES pantry(id) ON DELETE CASCADE
        )
    """)
    pantry_columns = {
        row["name"] for row in c.execute("PRAGMA table_info(pantry)").fetchall()
    }
    if "density_g_per_cup" not in pantry_columns:
        c.execute("ALTER TABLE pantry ADD COLUMN density_g_per_cup REAL")
    recipe_columns = {
        row["name"] for row in c.execute("PRAGMA table_info(recipes)").fetchall()
    }
    if "product_type" not in recipe_columns:
        c.execute(
            "ALTER TABLE recipes ADD COLUMN product_type TEXT NOT NULL DEFAULT 'Other'"
        )

    c.execute(
        """
        INSERT OR IGNORE INTO ingredient_conversion_profiles (
            pantry_id, density_g_per_cup, confirmed, source
        )
        SELECT id, density_g_per_cup, 1, 'legacy confirmed density'
        FROM pantry
        WHERE density_g_per_cup IS NOT NULL AND density_g_per_cup > 0
        """
    )
    c.execute(
        """
        INSERT OR IGNORE INTO ingredient_conversion_profiles (
            pantry_id, density_g_per_cup, confirmed, source
        )
        SELECT id, 288.0, 1, 'verified baking-soda conversion'
        FROM pantry
        WHERE LOWER(REPLACE(name, '-', ' ')) LIKE '%baking soda%'
        """
    )

    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_price_history_pantry "
        "ON pantry_price_history(pantry_id, recorded_at)"
    )
    c.execute(
        """
        INSERT INTO pantry_price_history (pantry_id, user_id, quantity, unit, price)
        SELECT p.id, p.user_id, p.quantity, p.unit, p.price
        FROM pantry AS p
        WHERE NOT EXISTS (
            SELECT 1
            FROM pantry_price_history AS h
            WHERE h.pantry_id = p.id
        )
        """
    )

    conn.commit()
    conn.close()


def get_or_create_user():
    """
    Find the logged-in user in the database.
    If they do not exist yet, create them.
    """

    google_id = getattr(st.user, "sub", None)
    email = getattr(st.user, "email", None)
    name = getattr(st.user, "name", None)

    if not google_id:
        st.error(
            "Google login succeeded, but the app could not "
            "identify your Google account."
        )
        st.stop()

    conn = get_connection()
    c = conn.cursor()

    c.execute(
        """
        SELECT id
        FROM users
        WHERE google_id = ?
        """,
        (google_id,),
    )

    user = c.fetchone()

    if user:
        user_id = user["id"]

        c.execute(
            """
            UPDATE users
            SET email = ?, name = ?
            WHERE id = ?
            """,
            (email, name, user_id),
        )

    else:
        c.execute(
            """
            INSERT INTO users (
                google_id,
                email,
                name
            )
            VALUES (?, ?, ?)
            """,
            (google_id, email, name),
        )

        user_id = c.lastrowid

    conn.commit()
    conn.close()

    return user_id


# Create database tables
create_daily_backup()
init_db()

# Get the ID for the currently logged-in user
CURRENT_USER_ID = get_or_create_user()


# ---------------------------------------------------------
# 4. PANTRY DATABASE FUNCTIONS
# ---------------------------------------------------------

def get_pantry(user_id):
    conn = get_connection()

    df = pd.read_sql(
        """
        SELECT
            p.id,
            p.name,
            p.quantity,
            p.unit,
            p.price,
            p.density_g_per_cup,
            cp.density_g_per_cup AS profile_density_g_per_cup,
            cp.grams_per_teaspoon,
            cp.package_recipe_quantity,
            cp.package_recipe_unit,
            cp.weight_per_item_g,
            COALESCE(cp.confirmed, 0) AS conversion_confirmed,
            cp.source AS conversion_source
        FROM pantry AS p
        LEFT JOIN ingredient_conversion_profiles AS cp ON cp.pantry_id = p.id
        WHERE p.user_id = ?
        ORDER BY p.name ASC
        """,
        conn,
        params=(user_id,),
    )

    conn.close()

    return df.to_dict(orient="records")


def save_pantry_item(
    user_id,
    name,
    qty,
    unit,
    price,
    pantry_id=None,
    density_g_per_cup=None,
):

    name = name.strip()
    unit = unit.strip().lower()

    conn = get_connection()
    c = conn.cursor()

    if pantry_id is None:
        c.execute(
            """
            SELECT id, name, quantity, unit, price, density_g_per_cup
            FROM pantry
            WHERE user_id = ? AND name = ? COLLATE NOCASE
            """,
            (user_id, name),
        )
    else:
        c.execute(
            "SELECT id, name, quantity, unit, price, density_g_per_cup "
            "FROM pantry WHERE id = ? AND user_id = ?",
            (pantry_id, user_id),
        )

    existing_item = c.fetchone()

    if existing_item:
        pantry_id = existing_item["id"]
        record_price = any(
            (
                str(existing_item["name"]) != name,
                float(existing_item["quantity"]) != float(qty),
                str(existing_item["unit"]) != unit,
                float(existing_item["price"]) != float(price),
            )
        )
        c.execute(
            """
            UPDATE pantry
            SET name = ?, quantity = ?, unit = ?, price = ?, density_g_per_cup = ?
            WHERE id = ? AND user_id = ?
            """,
            (
                name, qty, unit, price, density_g_per_cup,
                existing_item["id"], user_id,
            ),
        )
    else:
        c.execute(
            """
            INSERT INTO pantry (
                user_id, name, quantity, unit, price, density_g_per_cup
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                user_id, name, qty, unit, price, density_g_per_cup,
            ),
        )
        pantry_id = c.lastrowid
        record_price = True

    if record_price:
        c.execute(
            """
            INSERT INTO pantry_price_history (
                pantry_id, user_id, quantity, unit, price
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (pantry_id, user_id, qty, unit, price),
        )

    conn.commit()
    conn.close()

    return pantry_id


def save_conversion_profile(
    user_id,
    pantry_id,
    density_g_per_cup=None,
    grams_per_teaspoon=None,
    package_recipe_quantity=None,
    package_recipe_unit=None,
    weight_per_item_g=None,
    source="user",
):
    values = (
        density_g_per_cup,
        grams_per_teaspoon,
        package_recipe_quantity,
        weight_per_item_g,
    )
    if not any(value is not None and float(value) > 0 for value in values):
        raise ValueError("Enter at least one positive conversion value.")
    if package_recipe_quantity is not None and not package_recipe_unit:
        raise ValueError("Choose the recipe unit contained in the package.")

    conn = get_connection()
    owns_item = conn.execute(
        "SELECT 1 FROM pantry WHERE id = ? AND user_id = ?",
        (pantry_id, user_id),
    ).fetchone()
    if not owns_item:
        conn.close()
        raise ValueError("Ingredient not found in your price book.")
    conn.execute(
        """
        INSERT INTO ingredient_conversion_profiles (
            pantry_id, density_g_per_cup, grams_per_teaspoon,
            package_recipe_quantity, package_recipe_unit,
            weight_per_item_g, confirmed, source, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, 1, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(pantry_id) DO UPDATE SET
            density_g_per_cup = excluded.density_g_per_cup,
            grams_per_teaspoon = excluded.grams_per_teaspoon,
            package_recipe_quantity = excluded.package_recipe_quantity,
            package_recipe_unit = excluded.package_recipe_unit,
            weight_per_item_g = excluded.weight_per_item_g,
            confirmed = 1,
            source = excluded.source,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            pantry_id,
            density_g_per_cup,
            grams_per_teaspoon,
            package_recipe_quantity,
            package_recipe_unit,
            weight_per_item_g,
            source,
        ),
    )
    conn.commit()
    conn.close()


def delete_pantry_item(user_id, pantry_id):
    conn = get_connection()
    conn.execute(
        "DELETE FROM ingredient_conversion_profiles WHERE pantry_id = ?",
        (pantry_id,),
    )
    conn.execute(
        "DELETE FROM pantry_price_history WHERE pantry_id = ? AND user_id = ?",
        (pantry_id, user_id),
    )
    conn.execute(
        "DELETE FROM pantry WHERE id = ? AND user_id = ?",
        (pantry_id, user_id),
    )
    conn.commit()
    conn.close()


def get_price_history(user_id, pantry_id):
    conn = get_connection()
    history = pd.read_sql(
        """
        SELECT recorded_at, quantity, unit, price
        FROM pantry_price_history
        WHERE user_id = ? AND pantry_id = ?
        ORDER BY recorded_at ASC, id ASC
        """,
        conn,
        params=(user_id, pantry_id),
    )
    conn.close()
    return history

# ---------------------------------------------------------
# 5. RECIPE DATABASE FUNCTIONS
# ---------------------------------------------------------

def save_recipe(
    user_id,
    name,
    product_type,
    batch_yield,
    labor_rate,
    prep_hours,
    packaging_cost,
    target_margin,
    recipe_items,
    recipe_id=None,
):
    conn = get_connection()
    c = conn.cursor()

    if recipe_id is None:
        c.execute(
            """
            INSERT INTO recipes (
                user_id, name, product_type, batch_yield, labor_rate, prep_hours,
                packaging_cost, target_margin
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id, name, product_type, batch_yield, labor_rate, prep_hours,
                packaging_cost, target_margin,
            ),
        )
        recipe_id = c.lastrowid
    else:
        owned = c.execute(
            "SELECT 1 FROM recipes WHERE id = ? AND user_id = ?",
            (recipe_id, user_id),
        ).fetchone()
        if not owned:
            conn.close()
            raise ValueError("Recipe not found for this user")
        c.execute(
            """
            UPDATE recipes
            SET name = ?, product_type = ?, batch_yield = ?, labor_rate = ?, prep_hours = ?,
                packaging_cost = ?, target_margin = ?
            WHERE id = ? AND user_id = ?
            """,
            (
                name, product_type, batch_yield, labor_rate, prep_hours,
                packaging_cost, target_margin, recipe_id, user_id,
            ),
        )
        c.execute("DELETE FROM recipe_items WHERE recipe_id = ?", (recipe_id,))

    # Save each ingredient
    for item in recipe_items:

        c.execute(
            """
            INSERT INTO recipe_items (
                recipe_id,
                ingredient_name,
                quantity,
                unit
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                recipe_id,
                item["name"],
                item["qty"],
                item["unit"],
            ),
        )

    conn.commit()
    conn.close()

    return recipe_id


def delete_recipe(user_id, recipe_id):
    conn = get_connection()
    owned = conn.execute(
        "SELECT 1 FROM recipes WHERE id = ? AND user_id = ?",
        (recipe_id, user_id),
    ).fetchone()
    if owned:
        conn.execute("DELETE FROM recipe_items WHERE recipe_id = ?", (recipe_id,))
        conn.execute(
            "DELETE FROM recipes WHERE id = ? AND user_id = ?",
            (recipe_id, user_id),
        )
        conn.commit()
    conn.close()


def duplicate_recipe(user_id, recipe_id):
    conn = get_connection()
    recipe = conn.execute(
        "SELECT * FROM recipes WHERE id = ? AND user_id = ?",
        (recipe_id, user_id),
    ).fetchone()
    items = conn.execute(
        "SELECT ingredient_name, quantity, unit FROM recipe_items "
        "WHERE recipe_id = ? ORDER BY id",
        (recipe_id,),
    ).fetchall()
    conn.close()
    if not recipe:
        raise ValueError("Recipe not found for this user")
    return save_recipe(
        user_id,
        f"{recipe['name']} Copy",
        recipe["product_type"],
        recipe["batch_yield"],
        recipe["labor_rate"],
        recipe["prep_hours"],
        recipe["packaging_cost"],
        recipe["target_margin"],
        [
            {"name": item["ingredient_name"], "qty": item["quantity"], "unit": item["unit"]}
            for item in items
        ],
    )

def get_saved_recipes(user_id):

    conn = get_connection()

    recipes = pd.read_sql(
        """
        SELECT
            id,
            name,
            product_type,
            batch_yield,
            labor_rate,
            prep_hours,
            packaging_cost,
            target_margin
        FROM recipes
        WHERE user_id = ?
        ORDER BY id DESC
        """,
        conn,
        params=(user_id,),
    )

    conn.close()

    return recipes.to_dict(
        orient="records"
    )


def get_recipe_items(recipe_id, user_id):

    conn = get_connection()

    items = pd.read_sql(
        """
        SELECT
            ingredient_name,
            quantity,
            unit
        FROM recipe_items AS ri
        JOIN recipes AS r ON r.id = ri.recipe_id
        WHERE ri.recipe_id = ? AND r.user_id = ?
        ORDER BY ri.id ASC
        """,
        conn,
        params=(recipe_id, user_id),
    )

    conn.close()

    return items.to_dict(
        orient="records"
    )

# ---------------------------------------------------------
# 6. UNIT CONVERSIONS & DENSITY
# ---------------------------------------------------------

ureg = build_unit_registry()


def unit_family(unit):
    return crumbcost_core.unit_family(unit, ureg) or "unknown"


def conversion_needed_guidance(pantry_item, recipe_unit):
    if pantry_item is None:
        return (
            "This ingredient is not in the Bakery Ingredient Price Book. Add its "
            "package quantity, package unit, and price before calculating the cost."
        )

    package_unit = str(pantry_item["unit"]).lower().strip()
    package_family = unit_family(package_unit)
    recipe_family = unit_family(recipe_unit)
    if package_family == "count":
        return (
            f"This ingredient is purchased by {package_unit}, while the recipe uses "
            f"{recipe_unit}. Set up a direct package conversion or weight per item."
        )
    if package_family == "weight":
        if recipe_family == "volume":
            return (
                f"This ingredient is purchased by {package_unit} (weight), while the "
                f"recipe uses {recipe_unit} (volume). Confirm a density or direct "
                "package conversion to calculate its cost."
            )
        return (
            f"The saved package unit {package_unit} cannot be converted to "
            f"{recipe_unit}. Set up a conversion for this ingredient."
        )
    if package_family == "volume":
        return (
            f"This ingredient is purchased by {package_unit} (volume), while the "
            f"recipe uses {recipe_unit}. Confirm a density or direct package "
            "conversion to calculate its cost."
        )
    return (
        f"CrumbCost does not recognize the saved package unit '{package_unit}'. "
        "Correct the package unit in the Bakery Ingredient Price Book or set up a "
        "direct package conversion."
    )


def display_unit(unit, plural=False):
    labels = {
        "tsp": "teaspoon", "tbsp": "tablespoon", "fluid_ounce": "fluid ounce",
        "cup": "cup", "pint": "pint", "quart": "quart", "gallon": "gallon",
        "ml": "milliliter", "liter": "liter", "oz": "ounce", "lb": "pound",
        "g": "gram", "kg": "kilogram", "count": "item", "dozen": "dozen",
    }
    label = labels.get(normalize_unit_label(unit), str(unit))
    return label + ("s" if plural and label != "dozen" else "")


def positive_value(value, fallback):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return number if math.isfinite(number) and number > 0 else fallback


def close_recipe_conversion():
    st.session_state.conversion_setup_pantry_id = None


@st.dialog("Ingredient conversion", width="medium", on_dismiss=close_recipe_conversion)
def render_recipe_conversion_dialog(pantry_item, recipe_unit):
    # Dialog widget reruns stay local; saving triggers one full cost recalculation.
    if st.session_state.conversion_setup_pantry_id is None:
        st.rerun()
    render_conversion_editor(pantry_item, "recipe_conversion", recipe_unit)


def render_conversion_editor(pantry_item, key_prefix, recipe_unit=None):
    pantry_id = int(pantry_item["id"])
    st.markdown(f"#### Conversion for {pantry_item['name']}")
    st.write(
        f"Purchased: {float(pantry_item['quantity']):g} {pantry_item['unit']} "
        f"for ${float(pantry_item['price']):,.2f}"
    )
    if recipe_unit:
        st.write(f"Recipe uses: {display_unit(recipe_unit, plural=True)}")

    empty_profile = {
        "density_g_per_cup": None,
        "grams_per_teaspoon": None,
        "package_recipe_quantity": None,
        "package_recipe_unit": None,
        "weight_per_item_g": None,
    }

    def save_selected_conversion(profile, source):
        save_conversion_profile(
            CURRENT_USER_ID, pantry_id, source=source,
            **{**empty_profile, **profile},
        )
        st.session_state.conversion_setup_pantry_id = None
        st.session_state.pantry_price_book_signature = None
        st.toast("Conversion saved. Ingredient cost recalculated.")

    def preview_and_save(profile, preview_unit, button_label, source, suffix, missing=None):
        missing = list(missing or [])
        if not profile and not missing:
            missing.append("Enter a positive conversion measurement.")
        profile = profile or {}
        preview_item = {
            **pantry_item,
            "conversion_confirmed": True,
            "profile_density_g_per_cup": profile.get("density_g_per_cup"),
            **{
                key: profile.get(key)
                for key in empty_profile
                if key != "density_g_per_cup"
            },
        }
        preview_cost = (
            calculate_ingredient_cost(preview_item, 1, preview_unit, ureg)
            if not missing and preview_unit else None
        )
        if not missing and (preview_cost is None or not math.isfinite(preview_cost)):
            missing.append("Choose a measurement that connects the purchase and recipe units.")
        if not missing:
            st.metric(
                f"Estimated cost per {display_unit(preview_unit)}",
                f"${preview_cost:,.2f}",
            )
        st.button(
            button_label,
            type="primary",
            width="stretch",
            key=f"{key_prefix}_{suffix}_{pantry_id}",
            disabled=bool(missing),
            on_click=save_selected_conversion,
            args=(profile, source),
        )
        if missing:
            st.caption("To save: " + " ".join(missing))

    if pantry_item.get("conversion_confirmed"):
        st.success(
            "A confirmed conversion is already saved and remains in use until you "
            "choose and save a replacement."
        )
        for field, label in (
            ("grams_per_teaspoon", "g per teaspoon"),
            ("profile_density_g_per_cup", "g per cup"),
            ("weight_per_item_g", "g per item"),
        ):
            saved_value = positive_value(pantry_item.get(field), None)
            if saved_value is not None:
                st.caption(f"Saved conversion: {saved_value:g} {label}")

    reference_options = standard_conversion_options(pantry_item["name"])
    selected_reference = None
    if reference_options:
        st.markdown("##### Sourced standard conversion")
        if len(reference_options) > 1 or automatic_conversion_reference(pantry_item) is None:
            reference_labels = {
                f"{option['ingredient']} — {option['preparation']}": option
                for option in reference_options
            }
            selected_label = st.selectbox(
                "Which ingredient type are you using?",
                options=list(reference_labels) + ["None of these — use my package label"],
                index=None,
                placeholder="Choose the exact type",
                key=f"{key_prefix}_reference_type_{pantry_id}",
            )
            selected_reference = reference_labels.get(selected_label)
        else:
            selected_reference = reference_options[0]

        if selected_reference:
            st.write(f"Ingredient: {selected_reference['ingredient']}")
            st.write(f"Preparation/type: {selected_reference['preparation']}")
            st.write(
                f"Reference measurement: {selected_reference['quantity']:g} "
                f"{display_unit(selected_reference['unit'])} = "
                f"{selected_reference['grams']:g} g"
            )
            source_name, source_url = STANDARD_CONVERSION_SOURCE
            st.caption(f"Source: [{source_name}]({source_url})")
            reference_profile = conversion_profile_from_label(
                selected_reference["quantity"],
                selected_reference["unit"],
                selected_reference["grams"],
            )
            preview_and_save(
                reference_profile,
                recipe_unit or selected_reference["unit"],
                "Use this conversion",
                f"verified reference: {source_name}",
                "use_reference",
            )
    else:
        st.info(
            "No reliable standard match was found. Use the serving-size statement "
            "on your package instead."
        )

    with st.expander("Use my package label", expanded=not reference_options):
        st.write("Look for something like ‘1 tablespoon (9 g)’ on the package.")
        label_cols = st.columns([1, 1.35, 1])
        label_quantity = label_cols[0].number_input(
            "Quantity",
            min_value=0.01,
            value=None,
            placeholder="1",
            key=f"{key_prefix}_label_quantity_{pantry_id}",
        )
        label_unit = label_cols[1].selectbox(
            "Unit",
            ["teaspoon", "tablespoon", "cup", "item"],
            index=None,
            placeholder="Choose unit",
            key=f"{key_prefix}_label_unit_{pantry_id}",
        )
        label_grams = label_cols[2].number_input(
            "Weighs (g)",
            min_value=0.01,
            value=None,
            placeholder="9",
            key=f"{key_prefix}_label_grams_{pantry_id}",
        )
    label_missing = []
    if positive_value(label_quantity, None) is None:
        label_missing.append("Enter a quantity greater than zero.")
    if label_unit not in {"teaspoon", "tablespoon", "cup", "item"}:
        label_missing.append("Choose teaspoon, tablespoon, cup, or item.")
    if positive_value(label_grams, None) is None:
        label_missing.append("Enter a weight in grams greater than zero.")
    label_profile = None
    if not label_missing:
        label_profile = conversion_profile_from_label(
            label_quantity, label_unit, label_grams
        )
    preview_and_save(
        label_profile,
        recipe_unit or label_unit,
        "Save conversion",
        "package label confirmed by user",
        "save_label",
        missing=label_missing,
    )

    with st.expander("Advanced options"):
        fact_options = [
            "Grams per cup",
            "Grams per teaspoon",
            "Recipe units contained in this package",
            "Weight per item",
        ]
        known_fact = st.selectbox(
            "Conversion method",
            fact_options,
            index=None,
            placeholder="Choose a method",
            key=f"{key_prefix}_advanced_fact_{pantry_id}",
        )
        profile = dict(empty_profile)
        advanced_preview_unit = recipe_unit
        if known_fact == "Grams per cup":
            profile["density_g_per_cup"] = st.number_input(
                "Grams per cup", min_value=0.01, value=None,
                key=f"{key_prefix}_gpc_{pantry_id}",
            )
            advanced_preview_unit = advanced_preview_unit or "cup"
        elif known_fact == "Grams per teaspoon":
            profile["grams_per_teaspoon"] = st.number_input(
                "Grams per teaspoon", min_value=0.001, value=None, format="%.4f",
                key=f"{key_prefix}_gpt_{pantry_id}",
            )
            advanced_preview_unit = advanced_preview_unit or "tsp"
        elif known_fact == "Recipe units contained in this package":
            direct_cols = st.columns([1, 1])
            profile["package_recipe_quantity"] = direct_cols[0].number_input(
                "Units in package", min_value=0.01, value=None,
                key=f"{key_prefix}_direct_qty_{pantry_id}",
            )
            profile["package_recipe_unit"] = direct_cols[1].selectbox(
                "Recipe unit", RECIPE_UNITS, index=None,
                key=f"{key_prefix}_direct_unit_{pantry_id}",
            )
            advanced_preview_unit = advanced_preview_unit or profile["package_recipe_unit"]
        elif known_fact == "Weight per item":
            profile["weight_per_item_g"] = st.number_input(
                "Weight per item (grams)", min_value=0.01, value=None,
                key=f"{key_prefix}_item_g_{pantry_id}",
            )
            advanced_preview_unit = advanced_preview_unit or "item"
        has_advanced_value = any(
            positive_value(profile.get(field), None) is not None
            for field in (
                "density_g_per_cup", "grams_per_teaspoon",
                "package_recipe_quantity", "weight_per_item_g",
            )
        )
        preview_and_save(
            profile if has_advanced_value else None,
            advanced_preview_unit,
            "Save advanced conversion",
            "advanced conversion confirmed by user",
            "save_advanced",
        )

    if st.button("Cancel", width="stretch", key=f"{key_prefix}_cancel_{pantry_id}"):
        st.session_state.conversion_setup_pantry_id = None
        st.rerun()


# ---------------------------------------------------------
# 7. SESSION STATE
# ---------------------------------------------------------

if "recipe_items" not in st.session_state:
    st.session_state.recipe_items = []

for recipe_item in st.session_state.recipe_items:
    recipe_item.setdefault("row_id", uuid4().hex)

if "ingredient_editor_draft" not in st.session_state:
    st.session_state.ingredient_editor_draft = []

if "ingredient_draft_source" not in st.session_state:
    st.session_state.ingredient_draft_source = None

if "extracted_items" not in st.session_state:
    st.session_state.extracted_items = []

if "extracted_store" not in st.session_state:
    st.session_state.extracted_store = ""

if "receipt_extracted_baseline" not in st.session_state:
    st.session_state.receipt_extracted_baseline = []

if "receipt_upload_signature" not in st.session_state:
    st.session_state.receipt_upload_signature = None

if "receipt_upload_initialized" not in st.session_state:
    st.session_state.receipt_upload_initialized = False

if "pending_receipt_discard" not in st.session_state:
    st.session_state.pending_receipt_discard = False

if "receipt_scan_warnings" not in st.session_state:
    st.session_state.receipt_scan_warnings = []

if "receipt_cleanup_pending" not in st.session_state:
    st.session_state.receipt_cleanup_pending = False

if "recipe_name" not in st.session_state:
    st.session_state.recipe_name = ""

if "labor_rate" not in st.session_state:
    st.session_state.labor_rate = 0.0

if "prep_hours" not in st.session_state:
    st.session_state.prep_hours = 0.0

if "packaging_cost" not in st.session_state:
    st.session_state.packaging_cost = 0.0

if "target_margin" not in st.session_state:
    st.session_state.target_margin = 65

if "editing_recipe_id" not in st.session_state:
    st.session_state.editing_recipe_id = None

if "loaded_recipe_name" not in st.session_state:
    st.session_state.loaded_recipe_name = None

if "active_main_tab" not in st.session_state:
    st.session_state.active_main_tab = "Create a Recipe"

if "pending_main_tab_navigation" not in st.session_state:
    st.session_state.pending_main_tab_navigation = None

if "recipe_starter_message" not in st.session_state:
    st.session_state.recipe_starter_message = None

if "selling_format" not in st.session_state:
    st.session_state.selling_format = "Individual item"

if "custom_package_size" not in st.session_state:
    st.session_state.custom_package_size = 1

if "order_package_count" not in st.session_state:
    st.session_state.order_package_count = 1

if "whole_batch_price" not in st.session_state:
    st.session_state.whole_batch_price = 0.0

if "use_whole_batch_price" not in st.session_state:
    st.session_state.use_whole_batch_price = False

if "markup_percent" not in st.session_state:
    st.session_state.markup_percent = 100

if "selected_pricing_method" not in st.session_state:
    st.session_state.selected_pricing_method = "Gross margin"

if "chosen_selling_price_per_item" not in st.session_state:
    st.session_state.chosen_selling_price_per_item = 0.0

def use_pricing_recommendation(price_per_item):
    st.session_state.chosen_selling_price_per_item = float(price_per_item)


def reset_chosen_selling_price():
    st.session_state.chosen_selling_price_per_item = 0.0


if "advanced_pricing_simplified" not in st.session_state:
    st.session_state.selected_pricing_method = "Gross margin"
    st.session_state.advanced_pricing_simplified = True

if "pending_recipe_delete_id" not in st.session_state:
    st.session_state.pending_recipe_delete_id = None

if "pending_pantry_deletes" not in st.session_state:
    st.session_state.pending_pantry_deletes = []

if "pantry_price_book_draft" not in st.session_state:
    st.session_state.pantry_price_book_draft = []

if "pantry_price_book_signature" not in st.session_state:
    st.session_state.pantry_price_book_signature = None

if "pantry_editor_revision" not in st.session_state:
    st.session_state.pantry_editor_revision = 0

if "pantry_recipe_selection_ids" not in st.session_state:
    st.session_state.pantry_recipe_selection_ids = set()

if "pantry_select_all_visible" not in st.session_state:
    st.session_state.pantry_select_all_visible = False

if "product_analysis" not in st.session_state:
    st.session_state.product_analysis = None

if "product_analysis_signature" not in st.session_state:
    st.session_state.product_analysis_signature = None

if "conversion_setup_pantry_id" not in st.session_state:
    st.session_state.conversion_setup_pantry_id = None

# ---------------------------------------------------------
# 8. RECEIPT OCR ENGINE
# ---------------------------------------------------------

class ExtractedPantryItem(BaseModel):
    name: str = Field(min_length=1)
    quantity: float | None = None
    unit: str | None = None
    price: float
    package_details_uncertain: bool = False
    conversion_suggestion_type: str | None = None
    conversion_suggestion_value: float | None = None
    conversion_suggestion_unit: str | None = None

    @field_validator("quantity", "price", "conversion_suggestion_value")
    @classmethod
    def require_positive_number(cls, value):
        if value is None:
            return value
        if not math.isfinite(value) or value <= 0:
            raise ValueError("must be greater than zero")
        return value


class ReceiptExtractResponse(BaseModel):
    store_name: str
    items: list[ExtractedPantryItem]


def parse_receipt_image(image_bytes):

    api_key = resolve_config_value(
        "GEMINI_API_KEY",
        os.environ,
        st.secrets,
    )

    if not api_key:
        logger.error("Gemini receipt analysis is not configured: API key missing.")
        st.error(RECEIPT_ANALYSIS_ERROR_MESSAGE)
        return None

    configured_model = resolve_config_value(
        "GEMINI_MODEL",
        os.environ,
        st.secrets,
    )
    client = genai.Client(api_key=api_key)

    prompt = """
    Extract all grocery line items from this receipt.

    For each item:

    - name: clean ingredient or product name
    - quantity: the total usable amount inside the purchased package, using the
      printed net weight, volume, or item count when visible
    - unit: one of tsp, tbsp, cup, fl oz, ml, liter, oz, lb, g, kg, pint,
      quart, gallon, item, count, each, or dozen
    - price: total price paid for the item as a float
    - package_details_uncertain: true when quantity or unit is missing or unclear
    - conversion_suggestion_type: optional; one of density_g_per_cup,
      grams_per_teaspoon, package_recipe_quantity, or weight_per_item_g
    - conversion_suggestion_value: optional positive numeric suggestion
    - conversion_suggestion_unit: required only for package_recipe_quantity

    Important: do not use count merely because one bag, box, bottle, jar, or carton
    was purchased. A 5 lb bag of flour is quantity 5 and unit lb, not quantity 1 and
    unit count. Use count only when the contents are genuinely used as individual
    items, such as a carton containing 12 eggs. If package size is not visible or
    certain, return null for quantity and/or unit and set package_details_uncertain
    true. Never invent a package size, density, or conversion. Suggest a conversion
    only when it is printed on the package or clearly supported by the image; the
    user must confirm it before it is used.
    """

    try:
        model_name = select_gemini_model(
            list(client.models.list()),
            configured_model,
        )
        response = generate_receipt_content(
            client,
            model_name,
            contents=[
                types.Part.from_bytes(
                    data=image_bytes,
                    mime_type="image/jpeg",
                ),
                prompt,
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=ReceiptExtractResponse,
                temperature=0.1,
            ),
        )
        if response.parsed is None:
            raise ValueError("Gemini returned no parsed receipt data.")
        return response.parsed
    except ReceiptAnalysisError as err:
        st.error(str(err))
    except Exception as err:
        logger.exception("Gemini receipt setup or parsing failed: %s", err)
        st.error(RECEIPT_ANALYSIS_ERROR_MESSAGE)
    return None

# ---------------------------------------------------------
# 10. PDF QUOTE GENERATOR
# ---------------------------------------------------------


def generate_pdf_quote(
    recipe_title,
    yield_count,
    items,
    total_cogs,
    cost_per_unit,
    srp_batch,
    srp_unit,
):

    pdf = FPDF()

    pdf.add_page()

    pdf.set_font(
        "Helvetica",
        "B",
        18,
    )

    pdf.cell(
        0,
        10,
        "Bakery & Catering Cost Sheet",
        ln=True,
        align="C",
    )

    pdf.ln(5)

    pdf.set_font(
        "Helvetica",
        "B",
        12,
    )

    clean_title = (
        clean_pdf_text(recipe_title)
        or "Custom Order"
    )

    pdf.cell(
        0,
        8,
        f"Recipe / Item: {clean_title}",
        ln=True,
    )

    pdf.set_font(
        "Helvetica",
        "",
        10,
    )

    pdf.cell(
        0,
        6,
        f"One Batch Produces: {yield_count} sellable items",
        ln=True,
    )

    pdf.ln(4)

    # Table header
    pdf.set_font(
        "Helvetica",
        "B",
        10,
    )

    pdf.set_fill_color(
        240,
        240,
        240,
    )

    pdf.cell(
        100,
        8,
        "Ingredient",
        border=1,
        fill=True,
    )

    pdf.cell(
        40,
        8,
        "Amount",
        border=1,
        fill=True,
    )

    pdf.cell(
        40,
        8,
        "Cost",
        border=1,
        fill=True,
        ln=True,
    )

    # Table body
    pdf.set_font(
        "Helvetica",
        "",
        10,
    )

    for itm in items:

        pdf.cell(
            100,
            7,
            clean_pdf_text(
                itm["Ingredient"]
            ),
            border=1,
        )

        pdf.cell(
            40,
            7,
            clean_pdf_text(
                itm["Amount"]
            ),
            border=1,
        )

        pdf.cell(
            40,
            7,
            clean_pdf_text(
                itm["Cost"]
            ),
            border=1,
            ln=True,
        )

    pdf.ln(6)

    pdf.set_font(
        "Helvetica",
        "B",
        11,
    )

    pdf.cell(
        0,
        7,
        f"Cost for Whole Batch: ${total_cogs:.2f}",
        ln=True,
    )

    pdf.cell(
        0,
        7,
        f"Cost Per Unit: ${cost_per_unit:.2f}",
        ln=True,
    )

    pdf.cell(
        0,
        7,
        f"Price for Whole Batch: ${srp_batch:.2f}",
        ln=True,
    )

    pdf.cell(
        0,
        7,
        f"Price Per Sellable Item: ${srp_unit:.2f}",
        ln=True,
    )

    return bytes(
        pdf.output()
    )


# ---------------------------------------------------------
# 11. HEADER
# ---------------------------------------------------------

header_left, header_right = st.columns(
    [3, 1],
    vertical_alignment="center",
)

with header_left:

    st.title("🧁 CrumbCost")

    st.markdown(
        """
        <div class="crumbcost-subtitle">
            Bakery Product Cost &amp; Pricing Calculator
        </div>
        """,
        unsafe_allow_html=True,
    )

    user_name = getattr(
        st.user,
        "name",
        "Baker",
    )

    st.caption(
        f"Signed in as {user_name}"
    )


with header_right:
    if st.button(
        "Log out",
        use_container_width=True,
    ):
        st.logout()


# ---------------------------------------------------------
# 12. APP TABS
# ---------------------------------------------------------

MAIN_TAB_NAMES = [
    "Create a Recipe",
    "My Saved Recipes",
    "Scan Grocery Receipt",
    "Bakery Ingredient Price Book",
]
pending_main_tab = st.session_state.pending_main_tab_navigation
if pending_main_tab in MAIN_TAB_NAMES:
    st.session_state.active_main_tab = pending_main_tab

tab_builder, tab_recipes, tab_receipt, tab_pantry = st.tabs(
    MAIN_TAB_NAMES,
    key="active_main_tab",
    on_change="rerun",
)
consume_tab_navigation(st.session_state)

# ---------------------------------------------------------
# TAB 1: RECIPE BUILDER
# ---------------------------------------------------------

with tab_builder:

    if st.session_state.recipe_starter_message:
        st.success(st.session_state.recipe_starter_message)

    if st.session_state.editing_recipe_id:
        edit_notice, cancel_edit = st.columns([4, 1])
        edit_notice.info(
            f"Editing saved recipe: {st.session_state.loaded_recipe_name or st.session_state.recipe_name}"
        )
        if cancel_edit.button("Cancel Editing", use_container_width=True):
            st.session_state.editing_recipe_id = None
            st.session_state.loaded_recipe_name = None
            st.session_state.recipe_starter_message = None
            st.session_state.recipe_name = ""
            st.session_state.recipe_items = []
            reset_chosen_selling_price()
            st.rerun()

    col_left, col_right = st.columns(
        [1, 1],
        gap="medium",
        vertical_alignment="top",
    )

    current_pantry = get_pantry(
        CURRENT_USER_ID
    )

    with col_left:

        st.subheader(
            "Recipe Formulation"
        )

        recipe_name = st.text_input(
    	"Recipe Title",
   	 placeholder="e.g. Vanilla Cupcake Batch",
  	  key="recipe_name",
	)

        product_type = st.selectbox(
            "Bakery Product Type",
            options=PRODUCT_TYPES,
            index=None,
            placeholder="Choose a bakery product type",
            key="product_type",
        )

        if st.session_state.get("batch_yield") is None:
            st.session_state.batch_yield = 0

        batch_yield = st.number_input(
	  "Batch Yield — Sellable Items Produced per Batch",
	 min_value=0,
	 step=1,
	 help=(
            "A batch is one complete run of the recipe. Enter the number of "
            "individual items you can sell from that run—for example, enter "
            "24 when one cookie recipe produces 24 cookies."
        ),
	  key="batch_yield",
	)

        st.markdown(
            "**Add Ingredients**"
        )

        with st.expander("Unit conversion guide"):
            st.caption(
                "Weight units (g, kg, oz, lb) convert directly. Volume units "
                "(tsp, tbsp, fl oz, cup, pint, quart, gallon, ml, liter) convert "
                "directly, as do item/count/each/dozen. Confirm an ingredient "
                "conversion to connect weight, volume, and count formats."
            )

        pantry_names = [
            item["name"]
            for item in current_pantry
        ]

        if pantry_names:

            c1, c2, c3, c4 = st.columns(
                [2, 1, 1, 1]
            )

            selected_ing = c1.selectbox(
                "Ingredient",
                options=pantry_names,
                index=None,
                placeholder="Choose an ingredient",
                label_visibility="collapsed",
            )

            ing_qty = c2.number_input(
                "Qty",
                min_value=0.01,
                value=1.0,
                step=0.25,
                label_visibility="collapsed",
            )

            ing_unit = c3.selectbox(
                "Unit",
                options=RECIPE_UNITS,
                label_visibility="collapsed",
            )

            if c4.button(
                "Add to Recipe",
                use_container_width=True,
                disabled=selected_ing is None,
            ):

                matching_item = next(
                    (
                        item
                        for item in st.session_state.recipe_items
                        if item["name"].casefold() == selected_ing.casefold()
                        and item["unit"] == ing_unit
                    ),
                    None,
                )
                if matching_item:
                    matching_item["qty"] += ing_qty
                    st.toast("Merged with the existing recipe ingredient.")
                else:
                    st.session_state.recipe_items.append(
                        {
                            "name": selected_ing,
                            "qty": ing_qty,
                            "unit": ing_unit,
                            "row_id": uuid4().hex,
                        }
                    )
                st.rerun()

        else:

            st.info(
                "You do not have any saved ingredients. Add ingredients in "
                "the Bakery Ingredient Price Book tab first."
            )

        with st.expander("Add a New Ingredient"):
            st.caption(
                "Save a package price here, then choose the ingredient above to "
                "add the amount used in this recipe."
            )
            with st.form("recipe_new_ingredient_form", clear_on_submit=True):
                new_ing_name = st.text_input(
                    "Ingredient name",
                    placeholder="e.g. Cake flour",
                )
                new_ing_qty_col, new_ing_unit_col, new_ing_price_col = st.columns(3)
                new_ing_package_qty = new_ing_qty_col.number_input(
                    "Package quantity",
                    min_value=0.01,
                    value=1.0,
                    step=0.25,
                )
                new_ing_package_unit = new_ing_unit_col.selectbox(
                    "Package unit",
                    options=RECIPE_UNITS,
                    index=None,
                    placeholder="Choose unit",
                )
                new_ing_price = new_ing_price_col.number_input(
                    "Price paid ($)",
                    min_value=0.01,
                    value=None,
                    step=0.25,
                    placeholder="Enter price",
                )
                new_ing_density = st.number_input(
                    "Custom density in grams per cup (optional)",
                    min_value=0.0,
                    value=0.0,
                    step=1.0,
                    help=(
                        "Leave at 0 to use CrumbCost's built-in baking density. "
                        "Only enter a value for an override or an ingredient CrumbCost "
                        "does not recognize."
                    ),
                )
                save_new_ingredient = st.form_submit_button(
                    "Save to Bakery Ingredient Price Book",
                    use_container_width=True,
                )
            if save_new_ingredient:
                if not new_ing_name.strip():
                    st.warning("Please enter an ingredient name.")
                elif new_ing_package_unit is None:
                    st.warning("Please choose the package unit.")
                elif new_ing_price is None:
                    st.warning("Please enter the price paid.")
                else:
                    save_pantry_item(
                        CURRENT_USER_ID,
                        new_ing_name.strip(),
                        new_ing_package_qty,
                        new_ing_package_unit,
                        new_ing_price,
                        density_g_per_cup=new_ing_density or None,
                    )
                    st.toast(
                        f"{new_ing_name.strip()} saved. Choose it above to add it "
                        "to this recipe.",
                        icon="💾",
                    )
                    st.rerun()

        table_rows = []

        total_ingredients_cost = 0.0
        has_unresolved_costs = False
        ingredient_cost_details = []
        unit_mismatches = []

        if st.session_state.recipe_items:
            for item in (
                st.session_state.recipe_items
            ):

                p_item = next(
                    (
                        p
                        for p in current_pantry
                        if p["name"]
                        == item["name"]
                    ),
                    None,
                )

                line_cost = (
                    calculate_ingredient_cost(
                        p_item,
                        item["qty"],
                        item["unit"],
                        ureg,
                    )
                    if p_item
                    else None
                )

                if line_cost is not None:

                    total_ingredients_cost += (
                        line_cost
                    )
                    ingredient_cost_details.append((item["name"], line_cost))

                    uses_conversion_profile = (
                        p_item is not None
                        and unit_family(p_item["unit"]) != unit_family(item["unit"])
                    )
                    cost_display = (
                        f"Est. ${line_cost:.2f}"
                        if uses_conversion_profile
                        else f"${line_cost:.2f}"
                    )

                else:

                    has_unresolved_costs = True

                    unit_mismatches.append(
                        {
                            "ingredient": item["name"],
                            "pantry_id": int(p_item["id"]) if p_item else None,
                            "recipe_unit": item["unit"],
                            "message": conversion_needed_guidance(
                                p_item,
                                item["unit"],
                            ),
                        }
                    )

                    cost_display = "Conversion needed" if p_item else "Missing price"

                table_rows.append(
                    {
                        "Ingredient": item["name"],
                        "Amount": (
                            f"{item['qty']} "
                            f"{item['unit']}"
                        ),
                        "Cost": cost_display,
                    }
                )

            editor_rows = [
                {
                    "Ingredient": item["name"],
                    "Quantity": item["qty"],
                    "Unit": item["unit"],
                    "Cost": table_rows[index]["Cost"],
                    "Remove": False,
                    "Row ID": item["row_id"],
                }
                for index, item in enumerate(st.session_state.recipe_items)
            ]

            mismatch_messages = {
                mismatch["ingredient"]: mismatch["message"]
                for mismatch in unit_mismatches
            }
            unit_options = sorted(
                set(
                    RECIPE_UNITS
                    + [item["unit"] for item in current_pantry]
                    + [item["unit"] for item in st.session_state.recipe_items]
                )
            )
            draft_source = tuple(
                (
                    item["row_id"],
                    item["name"],
                    float(item["qty"]),
                    item["unit"],
                )
                for item in st.session_state.recipe_items
            )
            if st.session_state.ingredient_draft_source != draft_source:
                st.session_state.ingredient_editor_draft = [
                    {
                        "row_id": item["row_id"],
                        "name": item["name"],
                        "qty": float(item["qty"]),
                        "unit": item["unit"],
                        "remove": False,
                    }
                    for item in st.session_state.recipe_items
                ]
                for draft_item in st.session_state.ingredient_editor_draft:
                    row_id = draft_item["row_id"]
                    st.session_state[f"recipe_quantity_{row_id}"] = draft_item["qty"]
                    st.session_state[f"recipe_unit_{row_id}"] = draft_item["unit"]
                    st.session_state[f"recipe_remove_{row_id}"] = False
                st.session_state.ingredient_draft_source = draft_source

            editor_header = st.columns([2.1, 1, 1, 1.4, 0.65])
            for column, label in zip(
                editor_header,
                ["Ingredient", "Quantity", "Unit", "Cost", "Remove"],
            ):
                column.markdown(f"**{label}**")

            draft_rows = []
            for original in editor_rows:
                row_columns = st.columns([2.1, 1, 1, 1.4, 0.65])
                stable_row_key = original["Row ID"]
                row_columns[0].write(original["Ingredient"])
                draft_quantity = row_columns[1].number_input(
                    f"Quantity for {original['Ingredient']}",
                    min_value=0.01,
                    step=0.25,
                    label_visibility="collapsed",
                    key=f"recipe_quantity_{stable_row_key}",
                )
                draft_unit = row_columns[2].selectbox(
                    f"Unit for {original['Ingredient']}",
                    options=unit_options,
                    label_visibility="collapsed",
                    key=f"recipe_unit_{stable_row_key}",
                )
                if original["Cost"] in {"Conversion needed", "Missing price"}:
                    tooltip = html.escape(
                        mismatch_messages[original["Ingredient"]],
                        quote=True,
                    )
                    row_columns[3].markdown(
                        '<span class="unit-mismatch-cell" '
                        f'title="{tooltip}">{original["Cost"]}</span>',
                        unsafe_allow_html=True,
                    )
                    mismatch = next(
                        mismatch for mismatch in unit_mismatches
                        if mismatch["ingredient"] == original["Ingredient"]
                    )
                    if mismatch["pantry_id"] is not None and row_columns[3].button(
                        "Set up conversion",
                        key=f"setup_conversion_{stable_row_key}",
                    ):
                        st.session_state.conversion_setup_pantry_id = mismatch["pantry_id"]
                else:
                    row_columns[3].write(original["Cost"])
                    cost_pantry_item = next(
                        (item for item in current_pantry if item["name"] == original["Ingredient"]),
                        None,
                    )
                    reference = automatic_conversion_reference(cost_pantry_item) if cost_pantry_item else None
                    if reference and original["Cost"].startswith("Est."):
                        row_columns[3].caption(
                            f"Using {reference['preparation']} measurements. "
                            f"[Source]({STANDARD_CONVERSION_SOURCE[1]})"
                        )
                    elif cost_pantry_item and original["Cost"].startswith("Est."):
                        row_columns[3].caption("Using your saved conversion.")
                    if cost_pantry_item and original["Cost"].startswith("Est."):
                        if row_columns[3].button("Change", key=f"change_conversion_{stable_row_key}"):
                            st.session_state.conversion_setup_pantry_id = int(cost_pantry_item["id"])
                draft_remove = row_columns[4].checkbox(
                    f"Remove {original['Ingredient']}",
                    label_visibility="collapsed",
                    key=f"recipe_remove_{stable_row_key}",
                    help="Remove this ingredient when updates are applied.",
                )
                draft_rows.append(
                    {
                        "row_id": stable_row_key,
                        "name": original["Ingredient"],
                        "qty": float(draft_quantity),
                        "unit": draft_unit,
                        "remove": draft_remove,
                    }
                )

            st.session_state.ingredient_editor_draft = draft_rows
            ingredient_edits_pending = ingredient_draft_has_changes(
                st.session_state.recipe_items,
                draft_rows,
            )

            def apply_ingredient_updates():
                st.session_state.recipe_items = [
                    {
                        "name": item["name"],
                        "qty": float(item["qty"]),
                        "unit": item["unit"],
                        "row_id": item["row_id"],
                    }
                    for item in st.session_state.ingredient_editor_draft
                    if not item["remove"]
                ]
                st.session_state.ingredient_draft_source = None
                st.toast("Ingredient changes applied and costs recalculated.")

            st.button(
                "Apply Ingredient Updates",
                type="primary",
                width="stretch",
                disabled=not ingredient_edits_pending,
                key="apply_ingredient_updates",
                on_click=apply_ingredient_updates,
            )
            st.caption(
                "Change detected—apply the update to recalculate the cost."
                if ingredient_edits_pending
                else "Change a quantity, unit, or Remove option to enable Apply."
            )

            if st.session_state.conversion_setup_pantry_id is not None:
                setup_item = next(
                    (
                        item for item in current_pantry
                        if int(item["id"]) == int(st.session_state.conversion_setup_pantry_id)
                    ),
                    None,
                )
                setup_mismatch = next(
                    (
                        mismatch for mismatch in unit_mismatches
                        if mismatch["pantry_id"] == st.session_state.conversion_setup_pantry_id
                    ),
                    None,
                )
                if setup_item:
                    render_recipe_conversion_dialog(
                        setup_item,
                        setup_mismatch["recipe_unit"] if setup_mismatch else next(
                            (item["unit"] for item in st.session_state.recipe_items if item["name"] == setup_item["name"]),
                            None,
                        ),
                    )

            if st.button(
                "Clear Recipe",
                use_container_width=True,
            ):
                st.session_state.recipe_items = []
                reset_chosen_selling_price()
                st.rerun()

        else:

            st.info(
                "No ingredients added yet. "
                "Select an ingredient above."
            )


    with col_right:

        st.subheader(
            "Overhead, Labor & Margins"
        )

        overhead_top_left, overhead_top_right = st.columns(2)
        labor_rate = overhead_top_left.number_input(
            "Labor Rate ($/hour)",
            min_value=0.0,
            step=0.25,
            key="labor_rate",
        )
        prep_hours = overhead_top_right.number_input(
            "Prep & Bake Time (Hours)",
            min_value=0.0,
            step=0.25,
            key="prep_hours",
        )

        overhead_bottom_left, overhead_bottom_right = st.columns(2)
        packaging_cost = overhead_bottom_left.number_input(
            "Total Packaging Cost for This Batch ($)",
            min_value=0.0,
            step=0.25,
            key="packaging_cost",
        )
        target_margin = (
            overhead_bottom_right.number_input(
                "Target Gross Margin (%)",
                min_value=10,
                max_value=85,
                step=1,
                key="target_margin",
            )
            / 100.0
        )

        if st.session_state.recipe_items:
            validation_messages = []
            if target_margin < 0.20:
                validation_messages.append(
                    "The target margin is unusually low and may leave little "
                    "room for overhead or unexpected costs."
                )
            elif target_margin > 0.75:
                validation_messages.append(
                    "The target margin is unusually high and may produce a "
                    "selling price above the intended market."
                )
            for message in validation_messages:
                st.warning(message)

        labor_cost = (
            labor_rate
            * prep_hours
        )

        total_cogs = (
            total_ingredients_cost
            + packaging_cost
            + labor_cost
        )

        cost_per_unit = (
            total_cogs
            / batch_yield
            if batch_yield
            else 0
        )

        srp_batch = (
            total_cogs
            / (1.0 - target_margin)
            if target_margin < 1.0
            else 0
        )

        srp_unit = (
            srp_batch
            / batch_yield
            if batch_yield
            else 0
        )

        net_profit = (
            srp_batch
            - total_cogs
        )

        st.space("small")

        if has_unresolved_costs:
            st.error(
                "Pricing is incomplete because one or more ingredient "
                "units could not be converted."
            )
        else:
            m1, m2 = st.columns(2)

            m1.metric(
                "Cost for Whole Batch",
                f"${total_cogs:.2f}",
            )

            m2.metric(
                "Cost Per Unit",
                f"${cost_per_unit:.2f}",
            )

            st.caption(
                "Pricing options and order scaling appear below the recipe form."
            )

        if st.button(
            "💾 Update Recipe"
            if st.session_state.editing_recipe_id
            else "💾 Save Recipe",
            type="primary",
            use_container_width=True,
        ):

            if not recipe_name.strip():

                st.warning(
                    "Please enter a recipe title before saving."
                )

            elif not product_type:

                st.warning(
                    "Please choose a bakery product type before saving."
                )

            elif not batch_yield:

                st.warning(
                    "Please enter the number of sellable items produced by one batch."
                )

            elif not st.session_state.recipe_items:

                st.warning(
                    "Please add at least one ingredient."
                )

            elif has_unresolved_costs:

                st.warning(
                    "Set up all needed conversions before saving. "
                    "Unresolved ingredients are not included in COGS."
                )

            else:

                save_recipe(
                    CURRENT_USER_ID,
                    recipe_name.strip(),
                    product_type,
                    batch_yield,
                    labor_rate,
                    prep_hours,
                    packaging_cost,
                    target_margin,
                    st.session_state.recipe_items,
                    st.session_state.editing_recipe_id,
                )

                st.success(
                    f"'{recipe_name}' saved to your recipes!"
                )
                st.session_state.editing_recipe_id = None
                st.session_state.loaded_recipe_name = None
                st.session_state.recipe_starter_message = None

    markup_rate = st.session_state.markup_percent / 100.0
    pricing_options = calculate_pricing_options(
        total_cogs,
        total_ingredients_cost,
        packaging_cost,
        labor_cost,
        target_margin,
        markup_rate,
    )
    gross_margin_recommendation = (
        pricing_options["Gross margin"] / batch_yield
        if batch_yield
        else 0.0
    )
    recommendation_ready = (
        bool(st.session_state.recipe_items)
        and not has_unresolved_costs
        and total_ingredients_cost > 0
        and batch_yield > 0
    )

    st.space("small")
    st.subheader("Pricing confidence")
    with st.container(border=True):
        st.caption(
            "Compare the price you choose with the live recommendation for your "
            "target gross margin. Your chosen price changes only when you edit it "
            "or apply a recommendation."
        )
        recommendation_label = (
            f"Recommended price for your {target_margin * 100:.0f}% target"
        )
        st.metric(
            recommendation_label,
            (
                f"${gross_margin_recommendation:,.2f} per item"
                if recommendation_ready
                else "—"
            ),
            help=(
                "Calculated from current ingredient, labor, and packaging costs, "
                "divided by batch yield at the selected target gross margin."
            ),
        )
        st.button(
            "Apply target-margin recommendation",
            key="apply_target_margin_recommendation",
            type="primary",
            disabled=not recommendation_ready,
            on_click=use_pricing_recommendation,
            args=(gross_margin_recommendation,),
        )
        st.caption(
            "This recommendation updates automatically when costs, batch yield, "
            "or target margin change. Applying it replaces the chosen price."
        )
        chosen_selling_price = st.number_input(
            "Chosen selling price per item ($)",
            min_value=0.0,
            step=0.25,
            format="%.2f",
            key="chosen_selling_price_per_item",
        )

        confidence = calculate_pricing_confidence(
            total_cogs,
            batch_yield,
            chosen_selling_price,
        )
        cost_per_item_display = (
            f"${total_cogs / batch_yield:,.2f}"
            if batch_yield > 0 and not has_unresolved_costs
            else "—"
        )
        summary_columns = st.columns(4)
        summary_columns[0].metric("Total batch cost", f"${total_cogs:,.2f}")
        summary_columns[1].metric("Cost per item", cost_per_item_display)
        summary_columns[2].metric(
            "Profit per item",
            f"${confidence['profit_per_item']:,.2f}" if confidence else "—",
        )
        summary_columns[3].metric(
            "Gross margin",
            f"{confidence['gross_margin'] * 100:,.1f}%" if confidence else "—",
        )

        blocking_messages = []
        if not st.session_state.recipe_items:
            blocking_messages.append("Add at least one recipe ingredient.")
        elif has_unresolved_costs:
            blocking_messages.append(
                "Resolve every missing price or needed conversion before relying "
                "on this summary."
            )
        elif total_ingredients_cost <= 0:
            blocking_messages.append(
                "Ingredient cost is missing. Review the recipe and price book."
            )
        if batch_yield <= 0:
            blocking_messages.append(
                "Enter the number of sellable items produced by one batch."
            )
        if chosen_selling_price <= 0:
            blocking_messages.append(
                "Enter a chosen selling price per item greater than $0."
            )

        advisory_messages = []
        if labor_rate <= 0 or prep_hours <= 0:
            advisory_messages.append(
                "Labor is not included. Enter both an hourly rate and preparation "
                "time, or confirm that $0 labor is intentional."
            )
        if packaging_cost <= 0:
            advisory_messages.append(
                "Packaging is $0. Confirm that boxes, labels, liners, and bags "
                "are not needed."
            )

        if blocking_messages:
            st.error(
                "**Needs attention**\n\n- " + "\n- ".join(blocking_messages),
                icon=":material/error:",
            )
        elif confidence:
            if confidence["status"] == "Profitable":
                st.success(
                    "**Profitable** — the chosen selling price is above direct cost.",
                    icon=":material/check_circle:",
                )
            elif confidence["status"] == "Break-even":
                st.warning(
                    "**Break-even** — the chosen selling price produces "
                    "approximately no gross profit.",
                    icon=":material/balance:",
                )
            else:
                st.error(
                    f"**Below cost** — this price loses "
                    f"${abs(confidence['profit_per_item']):,.2f} per item.",
                    icon=":material/trending_down:",
                )

            st.caption(
                f"At ${chosen_selling_price:,.2f} per item, a batch of "
                f"{batch_yield} produces ${confidence['batch_revenue']:,.2f} "
                f"in revenue and ${confidence['batch_profit']:,.2f} in estimated "
                "gross profit."
            )
            actual_margin_percent = confidence["gross_margin"] * 100
            target_margin_percent = target_margin * 100
            if (
                recommendation_ready
                and chosen_selling_price + 1e-9 < gross_margin_recommendation
            ):
                price_gap = gross_margin_recommendation - chosen_selling_price
                advisory_messages.append(
                    f"Your chosen price is ${price_gap:,.2f} per item below the "
                    f"${gross_margin_recommendation:,.2f} needed for your "
                    f"{target_margin_percent:.0f}% target. It currently produces "
                    f"a {actual_margin_percent:.1f}% gross margin."
                )

        if advisory_messages:
            st.warning(
                "**Review these assumptions**\n\n- "
                + "\n- ".join(advisory_messages),
                icon=":material/warning:",
            )

    analysis_warnings = []
    if not st.session_state.recipe_items:
        analysis_warnings.append("No recipe ingredients have been added.")
    for mismatch in unit_mismatches:
        analysis_warnings.append(
            f"Conversion needed or missing price: {mismatch['ingredient']}."
        )
    if total_ingredients_cost <= 0:
        analysis_warnings.append("Ingredient cost is missing.")
    if labor_rate <= 0 or prep_hours <= 0:
        analysis_warnings.append("Labor cost is not included.")
    if packaging_cost <= 0:
        analysis_warnings.append("Packaging cost is not included.")
    if batch_yield <= 0:
        analysis_warnings.append("Batch yield is missing.")
    if chosen_selling_price <= 0:
        analysis_warnings.append("Chosen selling price per item is missing.")

    product_analysis_data = calculate_product_analysis(
        total_cost=total_cogs,
        batch_yield=batch_yield,
        chosen_price=chosen_selling_price,
        target_margin=target_margin,
        ingredient_costs=ingredient_cost_details,
        labor_cost=labor_cost,
        packaging_cost=packaging_cost,
        warnings=analysis_warnings,
    )
    product_analysis_signature = json.dumps(product_analysis_data, sort_keys=True)

    st.space("small")
    st.subheader("Product pricing analysis")
    st.caption(
        "Review margin health, pricing, cost drivers, and the next decisions "
        "supported by your current CrumbCost calculations."
    )
    if st.button(
        "Analyze This Product",
        width="stretch",
        disabled=not st.session_state.recipe_items,
    ):
        st.session_state.product_analysis = format_product_analysis(
            product_analysis_data
        )
        st.session_state.product_analysis_signature = product_analysis_signature

    if (
        st.session_state.product_analysis
        and st.session_state.product_analysis_signature == product_analysis_signature
    ):
        with st.container(border=True):
            analysis_copy = st.session_state.product_analysis
            st.markdown("### Cost snapshot")
            st.text(analysis_copy["cost_snapshot"])
            st.markdown("### Margin health")
            st.text(analysis_copy["margin_health"])
            st.markdown("### Chosen versus recommended")
            st.text(analysis_copy["pricing"])
            if analysis_copy["pricing_note"]:
                st.text(analysis_copy["pricing_note"])
            st.markdown("### Largest cost drivers")
            for driver in analysis_copy["cost_drivers"]:
                st.text(f"• {driver}")
            if analysis_copy["ingredient_drivers"]:
                st.markdown("#### Largest ingredient costs")
                for driver in analysis_copy["ingredient_drivers"]:
                    st.text(f"• {driver}")
            st.markdown("### Warnings")
            for warning in analysis_copy["warnings"]:
                st.text(f"• {warning}")
            st.markdown("### Next actions")
            for index, action in enumerate(analysis_copy["actions"], start=1):
                st.text(f"{index}. {action}")
            st.caption(
                "This analysis uses only the costs, yield, target margin, and "
                "chosen price currently entered in CrumbCost."
            )
    elif st.session_state.product_analysis:
        st.info(
            "Pricing inputs have changed. Select Analyze This Product to refresh "
            "the analysis."
        )

    st.space("small")
    st.subheader("Order size and recommended price")

    if (
        table_rows
        and not has_unresolved_costs
        and batch_yield
        and product_type
        and chosen_selling_price > 0
    ):
        format_col, package_col, order_col = st.columns(3)
        available_selling_formats = SELLING_FORMATS_BY_PRODUCT[product_type]
        if st.session_state.get("selling_format") not in available_selling_formats:
            st.session_state.selling_format = available_selling_formats[0]
        selling_format = format_col.selectbox(
            "How is this sold?",
            options=available_selling_formats,
            key="selling_format",
        )
        if selling_format in CUSTOM_FORMATS:
            units_per_package = package_col.number_input(
                "Items per package",
                min_value=1,
                step=1,
                key="custom_package_size",
            )
        elif selling_format in WHOLE_BATCH_FORMATS:
            units_per_package = batch_yield
            package_col.metric("Items per batch", batch_yield)
        else:
            units_per_package = SELLING_FORMAT_SIZES[selling_format]
            package_col.metric("Items per package", units_per_package)

        order_package_count = order_col.number_input(
            "Packages/items ordered",
            min_value=1,
            step=1,
            key="order_package_count",
        )
        order_scale = calculate_order_scale(
            batch_yield,
            units_per_package,
            order_package_count,
        )
        items_requested = order_scale["items_requested"]
        batches_required = order_scale["batches_required"]
        items_produced = order_scale["items_produced"]

        whole_price_col, whole_choice_col = st.columns(2)
        whole_batch_price = whole_price_col.number_input(
            "Optional whole-batch price ($)",
            min_value=0.0,
            step=1.0,
            format="%.2f",
            key="whole_batch_price",
            help="Price for one complete recipe batch.",
        )
        whole_batch_eligible = items_requested % batch_yield == 0
        if not whole_batch_eligible or whole_batch_price <= 0:
            st.session_state.use_whole_batch_price = False
        use_whole_batch_price = whole_choice_col.toggle(
            "Use whole-batch price",
            key="use_whole_batch_price",
            disabled=not whole_batch_eligible or whole_batch_price <= 0,
            help=(
                "When selected, each complete batch is quoted at the manual "
                "whole-batch price."
            ),
        )
        if not whole_batch_eligible:
            whole_choice_col.caption(
                "This quantity is not an exact batch multiple, so per-item pricing applies."
            )

        order_quote = calculate_order_quote(
            batch_yield,
            items_requested,
            total_cogs,
            chosen_selling_price,
            use_whole_batch_price,
            whole_batch_price,
        )

        with st.expander("Advanced pricing formulas"):
            st.caption(
                "Gross margin is the default. Open this section only when you "
                "want to compare or use a different pricing formula."
            )
            markup_percent = st.number_input(
                "Markup for alternative methods (%)",
                min_value=0,
                max_value=500,
                step=5,
                key="markup_percent",
            )
            markup_rate = markup_percent / 100.0
            pricing_options = calculate_pricing_options(
                total_cogs,
                total_ingredients_cost,
                packaging_cost,
                labor_cost,
                target_margin,
                markup_rate,
            )

            st.session_state.selected_pricing_method = normalize_pricing_method(
                st.session_state.get("selected_pricing_method"),
                pricing_options,
            )

            selected_pricing_method = st.segmented_control(
                "Pricing method",
                options=list(pricing_options),
                key="selected_pricing_method",
                required=True,
                width="stretch",
            )
            selected_pricing_method = normalize_pricing_method(
                selected_pricing_method,
                pricing_options,
            )

            st.markdown(
                "- **Gross margin:** total cost ÷ (1 − target margin)\n"
                "- **Cost markup:** total cost × (1 + markup)\n"
                "- **Materials markup + labor:** labor is added at face value; "
                "markup applies only to ingredients and packaging"
            )

            comparison_rows = []
            for method, batch_price in pricing_options.items():
                comparison_rows.append(
                    {
                        "Pricing method": method,
                        "Whole batch": batch_price,
                        "Per item": batch_price / batch_yield,
                        "This order": batch_price * batches_required,
                    }
                )
            comparison = pd.DataFrame(comparison_rows)

            def highlight_selected_method(row):
                selected = row["Pricing method"] == selected_pricing_method
                style = (
                    "background-color: #ffe4dc; color: #1f2a37; font-weight: 700;"
                    if selected
                    else ""
                )
                return [style] * len(row)

            st.dataframe(
                comparison.style.format(
                    {
                        "Whole batch": "${:,.2f}",
                        "Per item": "${:,.2f}",
                        "This order": "${:,.2f}",
                    }
                ).apply(highlight_selected_method, axis=1),
                use_container_width=True,
                hide_index=True,
            )
            formula_price_per_item = (
                pricing_options[selected_pricing_method] / batch_yield
            )
            st.button(
                f"Use ${formula_price_per_item:,.2f} as the chosen price per item",
                key="use_pricing_formula_price",
                on_click=use_pricing_recommendation,
                args=(formula_price_per_item,),
            )

        selected_batch_price = pricing_options[selected_pricing_method]
        selected_unit_price = selected_batch_price / batch_yield
        order_total = order_quote["quoted_total"]
        price_per_package = order_total / order_package_count
        selected_profit = selected_batch_price - total_cogs

        st.markdown("#### Order profitability")
        order_m1, order_m2, order_m3, order_m4 = st.columns(4)
        order_m1.metric("Quoted revenue", f"${order_total:,.2f}")
        order_m2.metric("Allocated cost", f"${order_quote['allocated_cost']:,.2f}")
        order_m3.metric("Estimated profit", f"${order_quote['gross_profit']:,.2f}")
        order_m4.metric("Gross margin", f"{order_quote['gross_margin'] * 100:,.1f}%")
        st.caption(
            "Allocated cost is the cost per item multiplied by the number of items sold."
        )

        st.markdown("#### Full-batch sales projection")
        batch_m1, batch_m2, batch_m3 = st.columns(3)
        batch_m1.metric("Revenue if the full batch sells", f"${order_quote['batch_revenue']:,.2f}")
        batch_m2.metric("Profit if the full batch sells", f"${order_quote['batch_profit']:,.2f}")
        batch_m3.metric("Projected batch margin", f"{order_quote['batch_margin'] * 100:,.1f}%")

        production_m1, production_m2, production_m3 = st.columns(3)
        production_m1.metric("Items requested", items_requested)
        production_m2.metric("Full batches needed", batches_required)
        production_m3.metric("Items produced", items_produced)

        if order_quote["inventory_remaining"]:
            st.info(
                f"Inventory: {order_quote['inventory_remaining']} items remain to sell "
                f"after filling this {items_requested}-item order."
            )

        with st.expander("Made specifically for this order — cash outlay scenario"):
            st.caption(
                "Use this optional view when you must make complete batches solely for "
                "this order. It shows immediate production cash outlay, not the default "
                "profit on the items sold."
            )
            cash_m1, cash_m2, cash_m3 = st.columns(3)
            cash_m1.metric("Complete batches required", batches_required)
            cash_m2.metric(
                "Production cash outlay",
                f"${order_quote['production_cost']:,.2f}",
            )
            cash_m3.metric(
                "Revenue after cash outlay",
                f"${order_quote['cash_outlay_balance']:,.2f}",
            )

        if order_quote["whole_batch_applied"]:
            difference = order_quote["price_difference"]
            if difference >= 0:
                st.info(
                    f"Whole-batch pricing saves the customer ${difference:,.2f} "
                    f"({order_quote['price_difference_percent'] * 100:,.1f}%) versus "
                    f"the normal ${order_quote['normal_per_item_total']:,.2f} per-item total."
                )
            else:
                st.info(
                    f"Whole-batch pricing adds a ${abs(difference):,.2f} premium "
                    f"versus the normal ${order_quote['normal_per_item_total']:,.2f} "
                    "per-item total."
                )
        st.caption(
            "The quote uses the chosen per-item price unless you explicitly select "
            "whole-batch pricing for an exact batch multiple."
        )

        with st.expander("Scaled ingredient requirements", expanded=True):
            scaled_rows = [
                {
                    "Ingredient": item["name"],
                    "Required quantity": item["qty"] * batches_required,
                    "Unit": item["unit"],
                }
                for item in st.session_state.recipe_items
            ]
            st.dataframe(
                pd.DataFrame(scaled_rows),
                use_container_width=True,
                hide_index=True,
            )

        breakdown = pd.DataFrame(
            {
                "Category": ["Ingredients", "Labor", "Packaging", "Profit"],
                "Amount": [
                    total_ingredients_cost / batch_yield * items_requested,
                    labor_cost / batch_yield * items_requested,
                    packaging_cost / batch_yield * items_requested,
                    order_quote["gross_profit"],
                ],
            }
        ).set_index("Category")
        with st.expander("Internal cost and profit breakdown"):
            st.dataframe(
                breakdown.style.format({"Amount": "${:,.2f}"}),
                use_container_width=True,
            )
            st.bar_chart(breakdown)

        customer_name = st.text_input(
            "Customer name for customer estimate",
            placeholder="Optional customer or company name",
        )
        if not recipe_name.strip():
            st.warning(
                "Enter a recipe name before downloading a customer estimate so "
                "the document clearly identifies what is being priced."
            )
        if selected_pricing_method == "Gross margin":
            pricing_label = f"{target_margin * 100:.0f}% gross margin"
        elif selected_pricing_method == "Cost markup":
            pricing_label = f"{markup_percent}% cost markup"
        else:
            pricing_label = f"{markup_percent}% materials markup + labor"

        internal_pdf = generate_recipe_estimate(
            recipe_name,
            batch_yield,
            table_rows,
            total_ingredients_cost,
            labor_cost,
            packaging_cost,
            total_cogs,
            cost_per_unit,
            selected_batch_price,
            selected_unit_price,
            pricing_label,
            selected_profit,
        )
        customer_pdf = generate_customer_estimate(
            recipe_name,
            customer_name,
            selling_format,
            order_package_count,
            items_requested,
            batches_required,
            price_per_package,
            order_total,
        )
        download_filename = safe_filename(recipe_name)
        internal_download, customer_download = st.columns(2)
        internal_download.download_button(
            "Download Internal Cost Sheet",
            data=internal_pdf,
            file_name=f"{download_filename}_internal_cost_sheet.pdf",
            mime="application/pdf",
            use_container_width=True,
        )
        customer_download.download_button(
            "Download Customer Estimate",
            data=customer_pdf,
            file_name=f"{download_filename}_customer_estimate.pdf",
            mime="application/pdf",
            use_container_width=True,
        )
    else:
        st.info(
            "Add recipe ingredients, set up needed conversions, enter a batch yield, "
            "choose a product type, and enter a chosen selling price to quote an order."
        )

# ---------------------------------------------------------
# TAB 2: MY SAVED RECIPES
# ---------------------------------------------------------

with tab_recipes:

    st.subheader(
        "My Saved Recipes"
    )

    if st.session_state.loaded_recipe_name:
        st.success(
            f"Loaded '{st.session_state.loaded_recipe_name}' for editing. "
            "Open the Create a Recipe tab to review and change it."
        )

    saved_recipes = get_saved_recipes(
        CURRENT_USER_ID
    )

    search_col, sort_col = st.columns([2, 1])
    recipe_search = search_col.text_input(
        "Search recipes",
        placeholder="Search by name",
    )
    recipe_sort = sort_col.selectbox(
        "Sort recipes",
        ["Newest first", "Name A–Z", "Name Z–A"],
    )

    if recipe_search:
        saved_recipes = [
            recipe
            for recipe in saved_recipes
            if recipe_search.casefold() in recipe["name"].casefold()
        ]
    if recipe_sort == "Name A–Z":
        saved_recipes.sort(key=lambda recipe: recipe["name"].casefold())
    elif recipe_sort == "Name Z–A":
        saved_recipes.sort(
            key=lambda recipe: recipe["name"].casefold(),
            reverse=True,
        )

    if saved_recipes:

        st.write(
            f"You have {len(saved_recipes)} saved recipe(s)."
        )

        recipe_options = {
            f"{recipe['name']} (Recipe #{recipe['id']})": recipe
            for recipe in saved_recipes
        }

        selected_recipe_label = st.selectbox(
            "Choose a saved recipe",
            options=list(recipe_options.keys()),
        )

        selected_recipe = recipe_options[
            selected_recipe_label
        ]

        st.space("small")

        st.subheader(
            selected_recipe["name"]
        )

        st.caption(f"Bakery product type: {selected_recipe['product_type']}")

        col1, col2, col3 = st.columns(3)

        col1.metric(
            "Sellable Items per Batch",
            selected_recipe["batch_yield"],
        )

        col2.metric(
            "Labor Rate",
            f"${selected_recipe['labor_rate']:.2f}/hr",
        )

        col3.metric(
            "Prep Time",
            f"{selected_recipe['prep_hours']:.2f} hrs",
        )

        st.write(
            f"**Packaging Cost:** "
            f"${selected_recipe['packaging_cost']:.2f}"
        )

        st.write(
            f"**Target Gross Margin:** "
            f"{selected_recipe['target_margin'] * 100:.0f}%"
        )

        st.markdown(
            "### Ingredients"
        )

        saved_items = get_recipe_items(
            selected_recipe["id"],
            CURRENT_USER_ID,
        )

        if saved_items:

            st.dataframe(
                pd.DataFrame(saved_items),
                use_container_width=True,
            )

        def load_selected_recipe():

            st.session_state.recipe_name = (
                selected_recipe["name"]
            )

            st.session_state.product_type = (
                selected_recipe["product_type"]
                if selected_recipe["product_type"] in PRODUCT_TYPES
                else "Other"
            )

            st.session_state.batch_yield = (
                int(selected_recipe["batch_yield"])
            )

            st.session_state.labor_rate = (
                float(selected_recipe["labor_rate"])
            )

            st.session_state.prep_hours = (
                float(selected_recipe["prep_hours"])
            )

            st.session_state.packaging_cost = (
                float(selected_recipe["packaging_cost"])
            )

            st.session_state.target_margin = int(
                selected_recipe["target_margin"] * 100
            )

            st.session_state.recipe_items = [
                {
                    "name": item["ingredient_name"],
                    "qty": item["quantity"],
                    "unit": item["unit"],
                    "row_id": uuid4().hex,
                }
                for item in saved_items
            ]
            st.session_state.editing_recipe_id = selected_recipe["id"]
            st.session_state.loaded_recipe_name = selected_recipe["name"]
            st.session_state.recipe_starter_message = None
            reset_chosen_selling_price()
            request_tab_navigation(st.session_state, "Create a Recipe")


        action_load, action_copy, action_delete = st.columns(3)

        action_load.button(
            "📂 Load & Edit",
            type="primary",
            use_container_width=True,
            on_click=load_selected_recipe,
        )

        if action_copy.button("Duplicate", use_container_width=True):
            duplicate_recipe(CURRENT_USER_ID, selected_recipe["id"])
            st.toast("Recipe duplicated.")
            st.rerun()

        if action_delete.button("Delete", use_container_width=True):
            st.session_state.pending_recipe_delete_id = selected_recipe["id"]
            st.rerun()

        if st.session_state.pending_recipe_delete_id == selected_recipe["id"]:
            st.warning(
                f"Delete '{selected_recipe['name']}' permanently? This also "
                "removes its saved ingredient list."
            )
            confirm_delete, cancel_delete = st.columns(2)
            if confirm_delete.button(
                "Confirm Recipe Deletion",
                type="primary",
                use_container_width=True,
            ):
                delete_recipe(CURRENT_USER_ID, selected_recipe["id"])
                if st.session_state.editing_recipe_id == selected_recipe["id"]:
                    st.session_state.editing_recipe_id = None
                    st.session_state.loaded_recipe_name = None
                st.session_state.pending_recipe_delete_id = None
                st.toast("Recipe deleted.")
                st.rerun()
            if cancel_delete.button("Cancel", use_container_width=True):
                st.session_state.pending_recipe_delete_id = None
                st.rerun()

        if not saved_items:

            st.info(
                "No ingredients found for this recipe."
            )

    else:

        st.info(
            "No recipes match your search, or none have been saved yet."
        )


# ---------------------------------------------------------
# TAB 3: RECEIPT SCANNER
# ---------------------------------------------------------

with tab_receipt:

    if st.session_state.receipt_cleanup_pending:
        clear_receipt_scan_state(st.session_state)

    st.subheader(
        "AI Grocery Receipt Scanner"
    )

    uploaded_file = st.file_uploader(
        "Upload Receipt Image",
        type=[
            "jpg",
            "jpeg",
            "png",
        ],
    )

    uploaded_bytes = uploaded_file.getvalue() if uploaded_file else None
    current_receipt_signature = receipt_upload_signature(
        uploaded_file.name if uploaded_file else None,
        uploaded_bytes,
    )
    reconcile_receipt_upload(st.session_state, current_receipt_signature)

    if uploaded_file:

        img = Image.open(
            io.BytesIO(uploaded_bytes)
        )

        st.image(
            img,
            caption="Uploaded Receipt",
            width=300,
        )

        if st.button(
            "Extract Receipt Items"
        ):

            with st.spinner(
                "Analyzing receipt..."
            ):

                buf = io.BytesIO()

                img.convert("RGB").save(
                    buf,
                    format="JPEG",
                )

                result = (
                    parse_receipt_image(
                        buf.getvalue()
                    )
                )

                if (
                    result
                    and result.items
                ):

                    st.session_state.extracted_store = (
                        result.store_name
                    )

                    st.session_state.extracted_items = [
                        {**item.model_dump(), "Use suggested conversion": False}
                        for item
                        in result.items
                    ]
                    st.session_state.receipt_extracted_baseline = [
                        dict(item) for item in st.session_state.extracted_items
                    ]
                    st.session_state.pending_receipt_discard = False
                    st.session_state.receipt_scan_warnings = []
                    st.session_state.pop("receipt_editor", None)
                    st.session_state.pop("receipt_count_units_verified", None)
                    st.session_state.pop("receipt_package_details_verified", None)

                    st.success(
                        f"Extracted from "
                        f"{result.store_name}!"
                    )

    if st.session_state.extracted_items:

        st.caption(
            "Review the package quantity, package unit, and price before saving. "
            "Rows can be added or deleted."
        )
        for item in st.session_state.extracted_items:
            suggestion_type = item.get("conversion_suggestion_type")
            suggestion_value = item.get("conversion_suggestion_value")
            if suggestion_type and suggestion_value:
                suggestion_labels = {
                    "density_g_per_cup": "grams per cup",
                    "grams_per_teaspoon": "grams per teaspoon",
                    "package_recipe_quantity": (
                        f"{item.get('conversion_suggestion_unit') or 'recipe units'} "
                        "in this package"
                    ),
                    "weight_per_item_g": "grams per item",
                }
                st.info(
                    f"Suggestion only — {item['name']}: {suggestion_value:g} "
                    f"{suggestion_labels.get(suggestion_type, suggestion_type)}. "
                    "Check Use suggested conversion to confirm it."
                )
        receipt_editor = st.data_editor(
            pd.DataFrame(
                st.session_state.extracted_items
            ),
            use_container_width=True,
            hide_index=True,
            num_rows="dynamic",
            column_config={
                "quantity": st.column_config.NumberColumn(
                    "Package quantity",
                    min_value=0.01,
                ),
                "unit": st.column_config.SelectboxColumn(
                    "Package unit",
                    options=RECIPE_UNITS,
                    required=False,
                    help=(
                        "Use count only for individually usable items such as eggs. "
                        "For a bag, box, bottle, jar, or carton, enter its printed "
                        "net weight or volume."
                    ),
                ),
                "price": st.column_config.NumberColumn(
                    "Price paid",
                    min_value=0.01,
                    format="$%.2f",
                ),
                "package_details_uncertain": None,
                "conversion_suggestion_type": None,
                "conversion_suggestion_value": None,
                "conversion_suggestion_unit": None,
                "Use suggested conversion": st.column_config.CheckboxColumn(
                    "Use suggested conversion",
                    help="Confirm and save this suggested conversion profile.",
                ),
            },
            key="receipt_editor",
        )

        receipt_rows = receipt_editor.to_dict(orient="records")
        uncertain_item_names = [
            str(row.get("name", "Unnamed item"))
            for row in receipt_rows
            if bool(row.get("package_details_uncertain"))
            or row.get("quantity") is None
            or pd.isna(row.get("quantity"))
            or not str(row.get("unit") or "").strip()
        ]
        package_details_verified = True
        if uncertain_item_names:
            st.warning(
                "Confirm the package size and unit for: "
                + ", ".join(uncertain_item_names)
                + ". The scanner did not have enough evidence to determine them."
            )
            package_details_verified = st.checkbox(
                "I confirmed the package quantities and units shown above.",
                key="receipt_package_details_verified",
            )
        count_item_names = [
            str(row.get("name", "Unnamed item"))
            for row in receipt_rows
            if str(row.get("unit", "")).lower().strip() == "count"
        ]
        count_units_verified = True
        if count_item_names:
            count_warning = (
                "Verify package sizes for count items: "
                f"{', '.join(count_item_names)}. Count is correct for items such as "
                "individual eggs. For a bag, box, bottle, jar, or carton, replace "
                "count with the printed package weight or volume."
            )
            st.session_state.receipt_scan_warnings = [count_warning]
            st.warning(count_warning)
            count_units_verified = st.checkbox(
                "I verified that count is the correct package unit for these items.",
                key="receipt_count_units_verified",
            )
        else:
            st.session_state.receipt_scan_warnings = []

        scan_has_unsaved_edits = receipt_draft_has_changes(
            st.session_state.receipt_extracted_baseline,
            receipt_rows,
        )

        def discard_scanned_ingredients():
            clear_receipt_scan_state(st.session_state)

        def request_scanned_ingredient_discard(has_unsaved_edits):
            if has_unsaved_edits:
                st.session_state.pending_receipt_discard = True
            else:
                discard_scanned_ingredients()

        def cancel_scanned_ingredient_discard():
            st.session_state.pending_receipt_discard = False

        save_scan_col, discard_scan_col = st.columns([2, 1])
        save_scanned_items = save_scan_col.button(
            "Save All to Bakery Ingredient Price Book",
            type="primary",
            disabled=not (count_units_verified and package_details_verified),
            width="stretch",
        )
        discard_scan_col.button(
            "Discard scanned ingredients",
            width="stretch",
            on_click=request_scanned_ingredient_discard,
            args=(scan_has_unsaved_edits,),
        )

        if st.session_state.pending_receipt_discard:
            if not scan_has_unsaved_edits:
                st.session_state.pending_receipt_discard = False
            else:
                st.warning(
                    "Discard your unsaved changes to these scanned ingredients? "
                    "Your existing Bakery Ingredient Price Book will not be changed."
                )
                confirm_discard_col, cancel_discard_col = st.columns(2)
                confirm_discard_col.button(
                    "Confirm discard",
                    type="primary",
                    width="stretch",
                    on_click=discard_scanned_ingredients,
                )
                cancel_discard_col.button(
                    "Keep reviewing",
                    width="stretch",
                    on_click=cancel_scanned_ingredient_discard,
                )

        if save_scanned_items:

            try:
                corrected_items = []
                for row in receipt_rows:
                    cleaned_row = {
                        key: (None if pd.isna(value) else value)
                        for key, value in row.items()
                        if key != "Use suggested conversion"
                    }
                    validated = ExtractedPantryItem.model_validate(cleaned_row).model_dump()
                    if validated["quantity"] is None or not validated["unit"]:
                        raise ValueError("Every item needs a confirmed package quantity and unit.")
                    corrected_items.append((validated, bool(row.get("Use suggested conversion"))))
                if not corrected_items:
                    raise ValueError("Add at least one receipt item.")
                pantry_before_save = {
                    item["name"].casefold(): item for item in get_pantry(CURRENT_USER_ID)
                }
                for item, use_suggestion in corrected_items:
                    pantry_id = save_pantry_item(
                        CURRENT_USER_ID,
                        item["name"],
                        item["quantity"],
                        item["unit"],
                        item["price"],
                    )
                    existing_item = pantry_before_save.get(item["name"].casefold())
                    if should_apply_receipt_conversion(existing_item, use_suggestion):
                        suggestion_type = item.get("conversion_suggestion_type")
                        suggestion_value = item.get("conversion_suggestion_value")
                        allowed_types = {
                            "density_g_per_cup", "grams_per_teaspoon",
                            "package_recipe_quantity", "weight_per_item_g",
                        }
                        if suggestion_type in allowed_types and suggestion_value:
                            profile = {field: None for field in allowed_types}
                            profile[suggestion_type] = suggestion_value
                            save_conversion_profile(
                                CURRENT_USER_ID,
                                pantry_id,
                                package_recipe_unit=(
                                    item.get("conversion_suggestion_unit")
                                    if suggestion_type == "package_recipe_quantity"
                                    else None
                                ),
                                source="receipt suggestion confirmed by user",
                                **profile,
                            )
                clear_receipt_scan_state(
                    st.session_state,
                    clear_widget_state=False,
                )
                st.session_state.receipt_cleanup_pending = True
                st.toast("Saved to your Bakery Ingredient Price Book!", icon="💾")
                st.rerun()
            except (ValueError, TypeError) as err:
                st.warning(f"Please correct the receipt data: {err}")


# ---------------------------------------------------------
# TAB 4: INGREDIENT PRICE BOOK
# ---------------------------------------------------------

with tab_pantry:

    st.subheader(
        "Bakery Ingredient Price Book"
    )

    pantry_records = get_pantry(
        CURRENT_USER_ID
    )

    if pantry_records:
        with st.expander("About volume-to-weight density"):
            st.caption(
                "CrumbCost includes standard grams-per-cup values for common "
                "baking staples. Enter Custom g/cup only when you want to override "
                "the built-in value or define an ingredient that is not recognized. "
                "For best accuracy, use the weight printed on the package or weigh "
                "one level cup with a kitchen scale."
            )
        pantry_search_col, pantry_sort_col = st.columns([2, 1])
        pantry_search = pantry_search_col.text_input(
            "Search pantry",
            placeholder="Search ingredients",
        )
        pantry_sort = pantry_sort_col.selectbox(
            "Sort pantry",
            ["Name A–Z", "Name Z–A", "Highest price", "Lowest price"],
        )

        saved_pantry_signature = pantry_state_signature(pantry_records)
        if st.session_state.pantry_price_book_signature != saved_pantry_signature:
            st.session_state.pantry_price_book_draft = [
                {**item, "Delete": False}
                for item in pantry_records
            ]
            st.session_state.pantry_price_book_signature = saved_pantry_signature
            st.session_state.pantry_editor_revision += 1

        all_pantry_ids = {int(item["id"]) for item in pantry_records}
        st.session_state.pantry_recipe_selection_ids = (
            set(st.session_state.pantry_recipe_selection_ids) & all_pantry_ids
        )

        visible_pantry = [
            dict(item) for item in st.session_state.pantry_price_book_draft
        ]
        if pantry_search:
            visible_pantry = [
                item
                for item in visible_pantry
                if pantry_search.casefold() in item["name"].casefold()
            ]
        reverse = pantry_sort in {"Name Z–A", "Highest price"}
        sort_field = "price" if "price" in pantry_sort.lower() else "name"
        visible_pantry.sort(
            key=lambda item: (
                item[sort_field].casefold()
                if sort_field == "name"
                else item[sort_field]
            ),
            reverse=reverse,
        )

        visible_pantry_ids = [int(item["id"]) for item in visible_pantry]
        master_selection = visible_selection_control(
            st.session_state.pantry_recipe_selection_ids,
            visible_pantry_ids,
        )
        st.session_state.pantry_select_all_visible = master_selection["selected"]

        def update_select_all_visible():
            st.session_state.pantry_recipe_selection_ids = update_visible_selection(
                st.session_state.pantry_recipe_selection_ids,
                visible_pantry_ids,
                st.session_state.pantry_select_all_visible,
            )
            st.session_state.pantry_editor_revision += 1

        pantry_editor_data = pd.DataFrame(visible_pantry)
        pantry_editor_data["Add to Recipe"] = pantry_editor_data["id"].map(
            lambda row_id: int(row_id)
            in st.session_state.pantry_recipe_selection_ids
        )
        pantry_editor_data = pantry_editor_data[
            ["Add to Recipe"]
            + [
                column
                for column in pantry_editor_data.columns
                if column != "Add to Recipe"
            ]
        ]
        with st.container(key="pantry_selection_table"):
            st.checkbox(
                master_selection["label"],
                key="pantry_select_all_visible",
                on_change=update_select_all_visible,
            )
            pantry_editor = st.data_editor(
                pantry_editor_data,
                width="stretch",
                hide_index=True,
                disabled=["id"],
                column_config={
                    "id": None,
                    "quantity": st.column_config.NumberColumn(
                        "Package size",
                        min_value=0.01,
                        help="Quantity contained in one purchased package.",
                    ),
                    "price": st.column_config.NumberColumn(
                        "Package price",
                        min_value=0.01,
                        format="$%.2f",
                    ),
                    "density_g_per_cup": st.column_config.NumberColumn(
                        "Custom g/cup",
                        min_value=0.01,
                        format="%.1f",
                        help="Optional custom grams per level cup.",
                    ),
                    "profile_density_g_per_cup": None,
                    "grams_per_teaspoon": None,
                    "package_recipe_quantity": None,
                    "package_recipe_unit": None,
                    "weight_per_item_g": None,
                    "conversion_confirmed": None,
                    "conversion_source": None,
                    "Delete": st.column_config.CheckboxColumn(
                        help="Check an ingredient, then save changes to delete it."
                    ),
                    "Add to Recipe": st.column_config.CheckboxColumn(
                        "",
                        width=190,
                        pinned=True,
                        help="Select several ingredients, then start a new recipe.",
                    ),
                },
                key=f"pantry_editor_v5_{st.session_state.pantry_editor_revision}",
            )

        editor_rows = pantry_editor.to_dict(orient="records")
        editor_by_id = {int(row["id"]): row for row in editor_rows}
        full_draft = []
        for draft_row in st.session_state.pantry_price_book_draft:
            row = editor_by_id.get(int(draft_row["id"]), draft_row)
            full_draft.append(
                {
                    key: value
                    for key, value in row.items()
                    if key != "Add to Recipe"
                }
            )
        st.session_state.pantry_price_book_draft = full_draft

        visible_selected_ids = {
            int(row["id"])
            for row in editor_rows
            if row["Add to Recipe"]
        }
        previous_selection_ids = set(st.session_state.pantry_recipe_selection_ids)
        st.session_state.pantry_recipe_selection_ids = update_visible_selection(
            st.session_state.pantry_recipe_selection_ids,
            visible_pantry_ids,
            False,
        ) | visible_selected_ids
        if (
            st.session_state.pantry_recipe_selection_ids
            != previous_selection_ids
        ):
            st.rerun()

        pantry_has_unsaved_changes = pantry_draft_has_changes(
            pantry_records,
            st.session_state.pantry_price_book_draft,
        )

        unusual_density_names = [
            str(row["name"])
            for row in editor_rows
            if row.get("density_g_per_cup") is not None
            and not pd.isna(row.get("density_g_per_cup"))
            and (
                float(row["density_g_per_cup"]) < 10
                or float(row["density_g_per_cup"]) > 1500
            )
        ]
        if unusual_density_names:
            st.warning(
                "Review the custom density for: "
                + ", ".join(unusual_density_names)
                + ". Typical ingredient densities are between 10 and 1,500 g/cup."
            )

        save_price_book_col, delete_all_col = st.columns([3, 1])
        if save_price_book_col.button(
            "Save Price Book Changes",
            type="primary",
            width="stretch",
            disabled=not pantry_has_unsaved_changes,
        ):
            try:
                editor_rows = st.session_state.pantry_price_book_draft
                delete_rows = [row for row in editor_rows if row["Delete"]]
                for row in editor_rows:
                    if row["Delete"]:
                        continue
                    if not str(row["name"]).strip() or not str(row["unit"]).strip():
                        raise ValueError("Names and units cannot be blank.")
                    if float(row["quantity"]) <= 0 or float(row["price"]) <= 0:
                        raise ValueError("Quantity and price must be positive.")
                    density_value = row.get("density_g_per_cup")
                    custom_density = (
                        None
                        if density_value is None or pd.isna(density_value)
                        else float(density_value)
                    )
                    save_pantry_item(
                        CURRENT_USER_ID,
                        str(row["name"]),
                        float(row["quantity"]),
                        str(row["unit"]),
                        float(row["price"]),
                        int(row["id"]),
                        custom_density,
                    )
                if delete_rows:
                    st.session_state.pending_pantry_deletes = [
                        {"id": int(row["id"]), "name": str(row["name"])}
                        for row in delete_rows
                    ]
                    st.toast("Changes saved. Confirm the selected deletions below.")
                else:
                    st.session_state.pending_pantry_deletes = []
                    st.toast("Ingredient price changes saved.")
                st.session_state.pantry_price_book_signature = None
                st.session_state.pantry_editor_revision += 1
                st.rerun()
            except (sqlite3.IntegrityError, TypeError, ValueError) as err:
                st.warning(str(err))

        if delete_all_col.button(
            "Delete All Ingredients",
            use_container_width=True,
        ):
            st.session_state.pending_pantry_deletes = [
                {"id": int(item["id"]), "name": str(item["name"])}
                for item in pantry_records
            ]
            st.rerun()

        if st.session_state.pending_pantry_deletes:
            pending_ids = {
                item["id"] for item in st.session_state.pending_pantry_deletes
            }
            all_price_book_ids = {int(item["id"]) for item in pantry_records}
            deleting_all_ingredients = pending_ids == all_price_book_ids
            pending_names = ", ".join(
                item["name"] for item in st.session_state.pending_pantry_deletes
            )
            if deleting_all_ingredients:
                st.warning(
                    f"Permanently delete all {len(pending_ids)} ingredients from "
                    "the Bakery Ingredient Price Book? Saved products will keep "
                    "their ingredient names, but their cost calculations will show "
                    "mismatches until ingredients are added again."
                )
            else:
                st.warning(
                    f"Permanently delete these ingredients from the price book: "
                    f"{pending_names}? Saved recipes will keep their ingredient names, "
                    "but future cost calculations may show a mismatch."
                )
            confirm_ingredients, cancel_ingredients = st.columns(2)
            if confirm_ingredients.button(
                (
                    "Confirm Delete All Ingredients"
                    if deleting_all_ingredients
                    else "Confirm Ingredient Deletion"
                ),
                type="primary",
                use_container_width=True,
            ):
                for item in st.session_state.pending_pantry_deletes:
                    delete_pantry_item(CURRENT_USER_ID, item["id"])
                st.session_state.pending_pantry_deletes = []
                st.toast(
                    "All ingredients deleted."
                    if deleting_all_ingredients
                    else "Selected ingredients deleted."
                )
                st.rerun()
            if cancel_ingredients.button("Cancel", use_container_width=True):
                st.session_state.pending_pantry_deletes = []
                st.rerun()

        selected_recipe_rows = [
            row
            for row in st.session_state.pantry_price_book_draft
            if int(row["id"]) in st.session_state.pantry_recipe_selection_ids
            and not row["Delete"]
        ]

        def start_recipe_from_pantry(selected_rows):
            st.session_state.recipe_name = ""
            st.session_state.product_type = None
            st.session_state.batch_yield = 0
            st.session_state.labor_rate = 0.0
            st.session_state.prep_hours = 0.0
            st.session_state.packaging_cost = 0.0
            st.session_state.target_margin = 65
            st.session_state.editing_recipe_id = None
            st.session_state.loaded_recipe_name = None
            reset_chosen_selling_price()
            (
                st.session_state.recipe_items,
                st.session_state.recipe_starter_message,
            ) = build_recipe_start_from_pantry(
                selected_rows,
                lambda: uuid4().hex,
            )
            request_tab_navigation(st.session_state, "Create a Recipe")

        st.button(
            "Start a New Recipe with Selected Ingredients",
            type="primary",
            use_container_width=True,
            disabled=not selected_recipe_rows,
            on_click=start_recipe_from_pantry,
            args=(selected_recipe_rows,),
        )
        if not selected_recipe_rows:
            st.caption(
                "Check Add to Recipe for one or more pantry ingredients to "
                "enable the new-recipe button."
            )

        with st.expander("Recipe conversion profiles"):
            st.caption(
                "Set or edit the confirmed conversion CrumbCost reuses when an "
                "ingredient's purchase unit and recipe unit are in different families."
            )
            conversion_options = {
                f"{item['name']} (#{item['id']})": item for item in pantry_records
            }
            conversion_label = st.selectbox(
                "Ingredient",
                options=list(conversion_options),
                key="conversion_profile_ingredient",
            ) if conversion_options else None
            if conversion_label:
                render_conversion_editor(
                    conversion_options[conversion_label],
                    "price_book_conversion",
                )

        pantry_options = {
            f"{item['name']} (#{item['id']})": item
            for item in visible_pantry
        }

        with st.expander("Ingredient price history"):
            history_label = st.selectbox(
                "Ingredient",
                options=list(pantry_options),
                key="history_ingredient",
            ) if pantry_options else None
            if history_label:
                history = get_price_history(
                    CURRENT_USER_ID,
                    pantry_options[history_label]["id"],
                )
                if history.empty:
                    st.caption("No price history recorded yet.")
                else:
                    st.dataframe(history, use_container_width=True, hide_index=True)
                    chart_data = history.copy()
                    chart_data["recorded_at"] = pd.to_datetime(
                        chart_data["recorded_at"]
                    )
                    price_axis = alt.Y(
                        "price:Q",
                        title="Package price ($)",
                        scale=alt.Scale(zero=False),
                    )
                    price_tooltip = [
                        alt.Tooltip("recorded_at:T", title="Recorded"),
                        alt.Tooltip("price:Q", title="Price", format="$.2f"),
                    ]
                    price_line = alt.Chart(chart_data).mark_line().encode(
                        x=alt.X("recorded_at:T", title="Recorded date"),
                        y=price_axis,
                        tooltip=price_tooltip,
                    )
                    price_points = alt.Chart(chart_data).mark_circle(size=80).encode(
                        x=alt.X("recorded_at:T", title="Recorded date"),
                        y=price_axis,
                        tooltip=price_tooltip,
                    )
                    st.altair_chart(
                        price_line + price_points,
                        use_container_width=True,
                    )

    else:

        st.info(
            "Your pantry is empty. "
            "Add your first ingredient below."
        )

    with st.expander(
        "Add Manual Ingredient"
    ):

        ca, cb, cc, cd = st.columns(4)

        new_name = ca.text_input(
            "Name"
        )

        new_qty = cb.number_input(
            "Package Qty",
            min_value=0.1,
            value=1.0,
        )

        new_unit = cc.text_input(
            "Unit (lb, oz, count, fl_oz)",
            value="lb",
        )

        new_price = cd.number_input(
            "Price Paid ($)",
            min_value=0.01,
            value=5.00,
        )

        new_density = st.number_input(
            "Custom density in grams per cup (optional)",
            min_value=0.0,
            value=0.0,
            step=1.0,
            help=(
                "Leave at 0 to use CrumbCost's built-in baking density. "
                "Enter a value only for an override or an unrecognized ingredient."
            ),
        )

        if st.button(
            "Save Ingredient"
        ):

            if new_name.strip():

                save_pantry_item(
                    CURRENT_USER_ID,
                    new_name,
                    new_qty,
                    new_unit,
                    new_price,
                    density_g_per_cup=new_density or None,
                )

                st.toast(
                    "Ingredient saved to your pantry!",
                    icon="💾",
                )

                st.rerun()

            else:

                st.warning(
                    "Please enter an ingredient name."
                )

    with st.expander("Database backup"):
        latest_backup = create_daily_backup()
        if latest_backup and latest_backup.exists():
            st.caption(
                f"Automatic daily backup: {latest_backup.name}. "
                "Backups are stored in the project's backups folder."
            )
            st.download_button(
                "Download Latest Backup",
                data=latest_backup.read_bytes(),
                file_name=latest_backup.name,
                mime="application/vnd.sqlite3",
            )
