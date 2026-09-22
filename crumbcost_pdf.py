from datetime import date

from fpdf import FPDF

from crumbcost_core import clean_pdf_text


NAVY = (31, 42, 55)
CORAL = (226, 112, 82)
CREAM = (250, 247, 242)
INK = (43, 47, 54)
MUTED = (103, 111, 122)
LINE = (220, 221, 224)


def _money(value):
    return f"${value:,.2f}"


def order_price_label(selling_format):
    order_type = clean_pdf_text(selling_format).strip()
    if order_type.casefold() == "individual item":
        return "Price per item"
    return f"Price per {order_type.casefold()}"


def format_order_calculation(package_count, selling_format, price, total):
    if package_count <= 1:
        return None

    order_type = clean_pdf_text(selling_format).strip().casefold()
    if order_type == "individual item":
        order_type = "items"
    elif order_type.endswith(("box", "batch")):
        order_type = f"{order_type}es"
    elif order_type == "loaf":
        order_type = "loaves"
    else:
        order_type = f"{order_type}s"
    return f"{package_count:,} {order_type} × {_money(price)} = {_money(total)}"


def generate_recipe_estimate(
    recipe_title,
    yield_count,
    items,
    ingredient_cost,
    labor_cost,
    packaging_cost,
    total_cogs,
    cost_per_unit,
    srp_batch,
    srp_unit,
    pricing_label,
    net_profit,
):
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    # Brand header
    pdf.set_fill_color(*NAVY)
    pdf.rect(0, 0, 210, 35, style="F")
    pdf.set_xy(15, 9)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 20)
    pdf.cell(0, 8, "CRUMBCOST", ln=True)
    pdf.set_x(15)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(226, 229, 233)
    pdf.cell(0, 5, "Recipe pricing made clear")
    pdf.set_fill_color(*CORAL)
    pdf.rect(0, 35, 210, 2.5, style="F")

    pdf.set_xy(15, 48)
    pdf.set_text_color(*INK)
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(120, 8, "Recipe Estimate")
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*MUTED)
    pdf.cell(60, 8, f"Prepared {date.today().strftime('%B %d, %Y')}", align="R", ln=True)

    # Recipe overview card
    pdf.set_fill_color(*CREAM)
    pdf.set_draw_color(*LINE)
    pdf.rect(15, 62, 180, 27, style="DF")
    pdf.set_xy(21, 68)
    pdf.set_text_color(*MUTED)
    pdf.set_font("Helvetica", "B", 8)
    pdf.cell(112, 5, "RECIPE")
    pdf.cell(52, 5, "ONE BATCH PRODUCES", ln=True)
    pdf.set_x(21)
    pdf.set_text_color(*INK)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(112, 8, clean_pdf_text(recipe_title) or "Untitled Recipe")
    pdf.cell(52, 8, f"{yield_count} sellable items")

    # Ingredient table
    pdf.set_xy(15, 100)
    pdf.set_text_color(*INK)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, "Ingredient estimate", ln=True)
    pdf.ln(2)

    widths = (100, 45, 35)
    pdf.set_fill_color(*NAVY)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 9)
    for width, label in zip(widths, ("Ingredient", "Recipe amount", "Est. cost")):
        pdf.cell(width, 8, label, border=0, fill=True)
    pdf.ln()

    pdf.set_text_color(*INK)
    pdf.set_font("Helvetica", "", 9)
    for index, item in enumerate(items):
        fill = index % 2 == 0
        if fill:
            pdf.set_fill_color(247, 248, 249)
        pdf.cell(widths[0], 8, clean_pdf_text(item["Ingredient"])[:54], border="B", fill=fill)
        pdf.cell(widths[1], 8, clean_pdf_text(item["Amount"]), border="B", fill=fill)
        pdf.cell(widths[2], 8, clean_pdf_text(item["Cost"]), border="B", fill=fill, ln=True)

    pdf.ln(10)
    section_y = pdf.get_y()
    if section_y > 195:
        pdf.add_page()
        pdf.set_xy(15, 20)
        pdf.set_text_color(*INK)
        pdf.set_font("Helvetica", "B", 15)
        pdf.cell(0, 8, "Recipe Estimate - Pricing Summary", ln=True)
        pdf.ln(4)
        section_y = pdf.get_y()

    # Cost breakdown card
    pdf.set_fill_color(247, 248, 249)
    pdf.set_draw_color(*LINE)
    pdf.rect(15, section_y, 86, 57, style="DF")
    pdf.set_xy(21, section_y + 6)
    pdf.set_text_color(*INK)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(74, 6, "Cost breakdown", ln=True)
    breakdown = (
        ("Ingredients", ingredient_cost),
        ("Labor", labor_cost),
        ("Packaging", packaging_cost),
    )
    pdf.set_font("Helvetica", "", 9)
    for label, value in breakdown:
        pdf.set_x(21)
        pdf.set_text_color(*MUTED)
        pdf.cell(47, 7, label)
        pdf.set_text_color(*INK)
        pdf.cell(27, 7, _money(value), align="R", ln=True)
    pdf.set_x(21)
    pdf.set_draw_color(*LINE)
    pdf.cell(74, 1, "", border="T", ln=True)
    pdf.set_x(21)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(47, 8, "Whole batch cost")
    pdf.cell(27, 8, _money(total_cogs), align="R")

    # Pricing estimate card
    pdf.set_fill_color(*NAVY)
    pdf.set_draw_color(*NAVY)
    pdf.rect(109, section_y, 86, 57, style="DF")
    pdf.set_xy(115, section_y + 6)
    pdf.set_text_color(226, 229, 233)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(74, 5, f"ESTIMATED PRICE - {clean_pdf_text(pricing_label).upper()}", ln=True)
    pdf.set_x(115)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 20)
    pdf.cell(74, 11, _money(srp_batch), ln=True)
    pdf.set_x(115)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(226, 229, 233)
    pdf.cell(42, 7, "Per sellable item")
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(32, 7, _money(srp_unit), align="R", ln=True)
    pdf.set_x(115)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(226, 229, 233)
    pdf.cell(42, 7, "Cost per item")
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(32, 7, _money(cost_per_unit), align="R", ln=True)
    pdf.set_x(115)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(226, 229, 233)
    pdf.cell(42, 7, "Estimated profit")
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(32, 7, _money(net_profit), align="R")

    pdf.set_xy(15, section_y + 66)
    pdf.set_text_color(*MUTED)
    pdf.set_font("Helvetica", "I", 8)
    pdf.multi_cell(
        180,
        5,
        "Planning estimate only. Prices are based on current pantry costs, labor, "
        "packaging, yield, and the selected target margin. Review before quoting a customer.",
    )

    pdf.set_auto_page_break(auto=False)
    pdf.set_y(-14)
    pdf.set_x(15)
    pdf.set_draw_color(*LINE)
    pdf.cell(180, 1, "", border="T", ln=True)
    pdf.set_x(15)
    pdf.set_text_color(*MUTED)
    pdf.set_font("Helvetica", "", 8)
    pdf.cell(90, 6, "CrumbCost Recipe Estimator")
    pdf.cell(90, 6, f"Page {pdf.page_no()}", align="R")

    return bytes(pdf.output())


def generate_customer_estimate(
    recipe_title,
    customer_name,
    selling_format,
    package_count,
    items_requested,
    batches_required,
    price_per_package,
    order_total,
):
    pdf = FPDF()
    pdf.set_auto_page_break(auto=False)
    pdf.add_page()

    pdf.set_fill_color(*NAVY)
    pdf.rect(0, 0, 210, 42, style="F")
    pdf.set_xy(15, 10)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 20)
    pdf.cell(0, 8, "CRUMBCOST", ln=True)
    pdf.set_x(15)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(226, 229, 233)
    pdf.cell(0, 6, "Customer Order Estimate")
    pdf.set_fill_color(*CORAL)
    pdf.rect(0, 42, 210, 2.5, style="F")

    pdf.set_xy(15, 57)
    pdf.set_text_color(*INK)
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(115, 8, "Customer Estimate")
    pdf.set_text_color(*MUTED)
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(65, 8, f"Prepared {date.today().strftime('%B %d, %Y')}", align="R", ln=True)

    pdf.set_fill_color(*CREAM)
    pdf.set_draw_color(*LINE)
    pdf.rect(15, 75, 180, 42, style="DF")
    pdf.set_xy(21, 82)
    pdf.set_text_color(*MUTED)
    pdf.set_font("Helvetica", "B", 8)
    pdf.cell(90, 5, "PREPARED FOR")
    pdf.cell(74, 5, "RECIPE", ln=True)
    pdf.set_x(21)
    pdf.set_text_color(*INK)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(90, 8, clean_pdf_text(customer_name) or "Customer")
    pdf.cell(74, 8, clean_pdf_text(recipe_title) or "Custom Recipe", ln=True)
    pdf.ln(4)
    pdf.set_x(21)
    pdf.set_text_color(*MUTED)
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(164, 6, "This estimate is valid for 14 days from the prepared date.")

    pdf.set_xy(15, 132)
    pdf.set_text_color(*INK)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, "Order summary", ln=True)
    pdf.ln(2)
    pdf.set_fill_color(*NAVY)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 9)
    columns = (("Order type", 60), ("Quantity", 40), ("Items", 40), ("Batches", 40))
    for label, width in columns:
        pdf.cell(width, 8, label, fill=True)
    pdf.ln()
    pdf.set_text_color(*INK)
    pdf.set_font("Helvetica", "", 10)
    values = (
        (clean_pdf_text(selling_format), 60),
        (str(package_count), 40),
        (str(items_requested), 40),
        (str(batches_required), 40),
    )
    for value, width in values:
        pdf.cell(width, 10, value, border="B")
    pdf.ln()

    pdf.set_fill_color(247, 248, 249)
    pdf.set_draw_color(*LINE)
    pdf.rect(15, 174, 180, 62, style="DF")
    pdf.set_xy(23, 184)
    pdf.set_text_color(*MUTED)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(95, 6, order_price_label(selling_format))
    pdf.cell(61, 6, "ESTIMATED ORDER TOTAL", align="R", ln=True)
    pdf.set_x(23)
    pdf.set_text_color(*INK)
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(95, 13, _money(price_per_package))
    pdf.set_text_color(*CORAL)
    pdf.set_font("Helvetica", "B", 23)
    pdf.cell(61, 13, _money(order_total), align="R", ln=True)
    calculation = format_order_calculation(
        package_count,
        selling_format,
        price_per_package,
        order_total,
    )
    if calculation:
        pdf.set_x(23)
        pdf.set_text_color(*INK)
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(156, 6, calculation, ln=True)
    pdf.set_x(23)
    pdf.set_text_color(*MUTED)
    pdf.set_font("Helvetica", "", 9)
    pdf.multi_cell(
        156,
        5,
        "Final pricing may change if order details, ingredient costs, delivery, "
        "taxes, or special requests change.",
    )

    pdf.set_y(-14)
    pdf.set_x(15)
    pdf.set_draw_color(*LINE)
    pdf.cell(180, 1, "", border="T", ln=True)
    pdf.set_x(15)
    pdf.set_text_color(*MUTED)
    pdf.set_font("Helvetica", "", 8)
    pdf.cell(90, 6, "CrumbCost Customer Estimate")
    pdf.cell(90, 6, "Page 1", align="R")
    return bytes(pdf.output())
