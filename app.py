import io
import os
import sqlite3
import pandas as pd
from PIL import Image
import pint
from pydantic import BaseModel
import streamlit as st
from fpdf import FPDF

from google import genai
from google.genai import types

# ---------------------------------------------------------
# 1. DATABASE SETUP
# ---------------------------------------------------------
DB_FILE = "bakery_calc.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # Pantry Table
    c.execute("""
        CREATE TABLE IF NOT EXISTS pantry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            quantity REAL,
            unit TEXT,
            price REAL
        )
    """)
    # Seed sample pantry items if empty
    c.execute("SELECT COUNT(*) FROM pantry")
    if c.fetchone()[0] == 0:
        samples = [
            ("All-Purpose Flour", 5.0, "lb", 4.50),
            ("Unsalted Butter", 16.0, "oz", 5.20),
            ("Granulated Sugar", 4.0, "lb", 3.80),
            ("Large Eggs", 12.0, "count", 3.50),
            ("Vanilla Extract", 2.0, "fl_oz", 7.00),
        ]
        c.executemany("INSERT INTO pantry (name, quantity, unit, price) VALUES (?, ?, ?, ?)", samples)
    conn.commit()
    conn.close()

def get_pantry():
    conn = sqlite3.connect(DB_FILE)
    df = pd.read_sql("SELECT name, quantity, unit, price FROM pantry ORDER BY name ASC", conn)
    conn.close()
    return df.to_dict(orient="records")

def save_pantry_item(name, qty, unit, price):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        INSERT INTO pantry (name, quantity, unit, price)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            quantity=excluded.quantity,
            unit=excluded.unit,
            price=excluded.price
    """, (name, qty, unit, price))
    conn.commit()
    conn.close()

init_db()

# ---------------------------------------------------------
# 2. UNIT CONVERSIONS & DENSITY
# ---------------------------------------------------------
ureg = pint.UnitRegistry()
ureg.define("count = 1 = item = piece = each = jar = can = bottle")

st.set_page_config(page_title="Baker & Caterer Cost Engine", page_icon="🧁", layout="wide")

DENSITY_MAP = {
    "flour": 120.0,
    "sugar": 200.0,
    "powdered sugar": 120.0,
    "brown sugar": 220.0,
    "butter": 227.0,
    "oil": 218.0,
    "milk": 240.0,
    "water": 236.6,
    "marinara": 245.0,
    "sauce": 245.0,
    "default_liquid": 236.6,
}

if "recipe_items" not in st.session_state:
    st.session_state.recipe_items = []
if "extracted_items" not in st.session_state:
    st.session_state.extracted_items = []
if "extracted_store" not in st.session_state:
    st.session_state.extracted_store = ""

# ---------------------------------------------------------
# 3. RECEIPT OCR ENGINE
# ---------------------------------------------------------
class ExtractedPantryItem(BaseModel):
    name: str
    quantity: float
    unit: str
    price: float

class ReceiptExtractResponse(BaseModel):
    store_name: str
    items: list[ExtractedPantryItem]

def parse_receipt_image(image_bytes):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        st.error("GEMINI_API_KEY environment variable is not set.")
        return None

    client = genai.Client()
    prompt = """
    Extract all grocery line items from this receipt.
    For each item:
    - name: clean ingredient or product name
    - quantity: numeric package size or weight (default to 1.0 if not listed)
    - unit: compatible unit string (e.g. 'lb', 'oz', 'g', 'kg', 'fl_oz', 'gallon', 'count')
    - price: total price paid for the item as a float
    """

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"), prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=ReceiptExtractResponse,
                temperature=0.1,
            ),
        )
        return response.parsed
    except Exception:
        try:
            response = client.models.generate_content(
                model="gemini-1.5-flash",
                contents=[types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"), prompt],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=ReceiptExtractResponse,
                    temperature=0.1,
                ),
            )
            return response.parsed
        except Exception as err:
            st.error(f"API busy. Please retry in a few seconds. ({err})")
            return None

def calculate_ingredient_cost(pantry_item, recipe_qty, recipe_unit):
    try:
        p_unit_str = str(pantry_item["unit"]).lower().strip()
        r_unit_str = str(recipe_unit).lower().strip()

        try:
            pantry_total = pantry_item["quantity"] * ureg(p_unit_str)
            recipe_used = recipe_qty * ureg(r_unit_str)
            converted_qty = recipe_used.to(pantry_total.units)
            unit_cost = pantry_item["price"] / pantry_total.magnitude
            return converted_qty.magnitude * unit_cost
        except pint.DimensionalityError:
            pass

        item_name = pantry_item["name"].lower()
        density_g_per_cup = DENSITY_MAP["default_liquid"]
        for key, val in DENSITY_MAP.items():
            if key in item_name:
                density_g_per_cup = val
                break

        recipe_vol = recipe_qty * ureg(r_unit_str)
        cups_used = recipe_vol.to(ureg.cup).magnitude
        grams_used = cups_used * density_g_per_cup

        pantry_wt = pantry_item["quantity"] * ureg(p_unit_str)
        pantry_grams = pantry_wt.to(ureg.gram).magnitude

        cost_per_gram = pantry_item["price"] / pantry_grams
        return grams_used * cost_per_gram
    except Exception:
        return None

# ---------------------------------------------------------
# 4. PDF QUOTE GENERATOR
# ---------------------------------------------------------
def generate_pdf_quote(recipe_title, yield_count, items, total_cogs, cost_per_unit, srp_batch, srp_unit):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 10, "Bakery & Catering Cost Sheet", ln=True, align="C")
    pdf.ln(5)

    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, f"Recipe / Item: {recipe_title or 'Custom Order'}", ln=True)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, f"Batch Yield: {yield_count} servings/units", ln=True)
    pdf.ln(4)

    # Table Header
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_fill_color(240, 240, 240)
    pdf.cell(100, 8, "Ingredient", border=1, fill=True)
    pdf.cell(40, 8, "Amount", border=1, fill=True)
    pdf.cell(40, 8, "Cost", border=1, fill=True, ln=True)

    # Table Body
    pdf.set_font("Helvetica", "", 10)
    for itm in items:
        pdf.cell(100, 7, str(itm["Ingredient"]), border=1)
        pdf.cell(40, 7, str(itm["Amount"]), border=1)
        pdf.cell(40, 7, str(itm["Cost"]), border=1, ln=True)

    pdf.ln(6)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, f"Total Batch COGS: ${total_cogs:.2f}", ln=True)
    pdf.cell(0, 7, f"Cost Per Unit: ${cost_per_unit:.2f}", ln=True)
    pdf.cell(0, 7, f"Suggested Batch Price: ${srp_batch:.2f}", ln=True)
    pdf.cell(0, 7, f"Suggested Unit Price: ${srp_unit:.2f}", ln=True)

    return bytes(pdf.output())

# ---------------------------------------------------------
# 5. UI TABS
# ---------------------------------------------------------
st.title("🧁 Bakery & Catering Cost Calculator")

tab_builder, tab_receipt, tab_pantry = st.tabs(
    ["Recipe Cost Engine", "Scan Grocery Receipt", "Pantry Inventory"]
)

# --- TAB 1: RECIPE BUILDER ---
with tab_builder:
    col_left, col_right = st.columns([1.2, 0.8], gap="large")
    current_pantry = get_pantry()

    with col_left:
        st.subheader("Recipe Formulation")
        recipe_name = st.text_input("Recipe Title", placeholder="e.g. Vanilla Cupcake Batch")
        batch_yield = st.number_input("Batch Yield (Total Servings/Units)", min_value=1, value=12, step=1)

        st.markdown("**Add Ingredients**")
        pantry_names = [item["name"] for item in current_pantry]

        if pantry_names:
            c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
            selected_ing = c1.selectbox("Ingredient", options=pantry_names, label_visibility="collapsed")
            ing_qty = c2.number_input("Qty", min_value=0.01, value=1.0, step=0.25, label_visibility="collapsed")
            ing_unit = c3.selectbox("Unit", options=["cup", "tbsp", "tsp", "oz", "g", "lb", "fl_oz", "count"], label_visibility="collapsed")

            if c4.button("Add to Recipe", use_container_width=True):
                st.session_state.recipe_items.append({"name": selected_ing, "qty": ing_qty, "unit": ing_unit})

        table_rows = []
        total_ingredients_cost = 0.0

        if st.session_state.recipe_items:
            for item in st.session_state.recipe_items:
                p_item = next((p for p in current_pantry if p["name"] == item["name"]), None)
                line_cost = calculate_ingredient_cost(p_item, item["qty"], item["unit"]) if p_item else None

                if line_cost is not None:
                    total_ingredients_cost += line_cost
                    cost_display = f"${line_cost:.2f}"
                else:
                    cost_display = "⚠️ Unit Mismatch"

                table_rows.append({"Ingredient": item["name"], "Amount": f"{item['qty']} {item['unit']}", "Cost": cost_display})

            st.dataframe(pd.DataFrame(table_rows), use_container_width=True)

            if st.button("Clear Recipe"):
                st.session_state.recipe_items = []
                st.rerun()
        else:
            st.info("No ingredients added yet. Select an ingredient above.")

    with col_right:
        st.subheader("Overhead, Labor & Margins")
        labor_rate = st.number_input("Baker Labor Rate ($/hour)", min_value=0.0, value=0.00, step=1.0)
        prep_hours = st.number_input("Prep & Bake Time (Hours)", min_value=0.0, value=0.00, step=0.25)
        packaging_cost = st.number_input("Packaging / Boxes ($)", min_value=0.0, value=0.00, step=0.50)
        target_margin = st.slider("Target Gross Margin (%)", min_value=10, max_value=85, value=65) / 100.0

        labor_cost = labor_rate * prep_hours
        total_cogs = total_ingredients_cost + packaging_cost + labor_cost
        cost_per_unit = total_cogs / batch_yield if batch_yield else 0
        srp_batch = total_cogs / (1.0 - target_margin) if target_margin < 1.0 else 0
        srp_unit = srp_batch / batch_yield if batch_yield else 0
        net_profit = srp_batch - total_cogs

        st.divider()
        m1, m2 = st.columns(2)
        m1.metric("Batch Cost (COGS)", f"${total_cogs:.2f}")
        m2.metric("Cost Per Unit", f"${cost_per_unit:.2f}")

        m3, m4 = st.columns(2)
        m3.metric("Suggested Price (Batch)", f"${srp_batch:.2f}")
        m4.metric("Suggested Price (Each)", f"${srp_unit:.2f}")
        st.success(f"**Estimated Batch Net Profit:** ${net_profit:.2f}")

        # PDF Export Button
        if table_rows:
            pdf_data = generate_pdf_quote(recipe_name, batch_yield, table_rows, total_cogs, cost_per_unit, srp_batch, srp_unit)
            st.download_button(
                label="📄 Download Recipe Quote (PDF)",
                data=pdf_data,
                file_name=f"{(recipe_name or 'Recipe').replace(' ', '_')}_quote.pdf",
                mime="application/pdf",
                use_container_width=True
            )

# --- TAB 2: RECEIPT SCANNER ---
with tab_receipt:
    st.subheader("AI Grocery Receipt Scanner")
    uploaded_file = st.file_uploader("Upload Receipt Image", type=["jpg", "jpeg", "png"])

    if uploaded_file:
        img = Image.open(uploaded_file)
        st.image(img, caption="Uploaded Receipt", width=300)

        if st.button("Extract Ingredients via Gemini"):
            with st.spinner("Analyzing receipt..."):
                buf = io.BytesIO()
                img.save(buf, format="JPEG")
                result = parse_receipt_image(buf.getvalue())
                if result and result.items:
                    st.session_state.extracted_store = result.store_name
                    st.session_state.extracted_items = [item.model_dump() for item in result.items]
                    st.success(f"Extracted from {result.store_name}!")

    if st.session_state.extracted_items:
        st.dataframe(pd.DataFrame(st.session_state.extracted_items), use_container_width=True)
        if st.button("Save All to SQLite Pantry", type="primary"):
            for itm in st.session_state.extracted_items:
                save_pantry_item(itm["name"], itm["quantity"], itm["unit"], itm["price"])
            st.session_state.extracted_items = []
            st.toast("✅ Saved permanently to pantry!", icon="💾")
            st.rerun()

# --- TAB 3: PANTRY INVENTORY ---
with tab_pantry:
    st.subheader("Saved Pantry Inventory")
    pantry_records = get_pantry()
    st.dataframe(pd.DataFrame(pantry_records), use_container_width=True)

    with st.expander("Add Manual Ingredient"):
        ca, cb, cc, cd = st.columns(4)
        new_name = ca.text_input("Name")
        new_qty = cb.number_input("Package Qty", min_value=0.1, value=1.0)
        new_unit = cc.text_input("Unit (lb, oz, count, fl_oz)", value="lb")
        new_price = cd.number_input("Price Paid ($)", min_value=0.01, value=5.00)

        if st.button("Save Ingredient"):
            if new_name:
                save_pantry_item(new_name, new_qty, new_unit, new_price)
                st.toast("✅ Ingredient saved!", icon="💾")
                st.rerun()