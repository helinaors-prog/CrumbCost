import ast
import math
from pathlib import Path
import sqlite3
import tempfile
import unittest
from contextlib import closing

import pandas as pd
import streamlit as st
from streamlit.testing.v1 import AppTest

import crumbcost_core


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def app_functions(database):
    names = {
        "get_connection", "init_db", "save_pantry_item", "get_pantry",
        "save_conversion_profile", "save_recipe", "get_recipe_items",
        "display_unit", "positive_value", "render_conversion_editor",
        "close_recipe_conversion", "render_recipe_conversion_dialog",
    }
    tree = ast.parse(APP_PATH.read_text(encoding="utf-8"))
    functions = ast.Module(
        body=[node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names],
        type_ignores=[],
    )
    namespace = {
        **vars(crumbcost_core), "st": st, "pd": pd, "sqlite3": sqlite3,
        "math": math, "DB_FILE": database, "CURRENT_USER_ID": 1,
        "ureg": crumbcost_core.build_unit_registry(),
        "RECIPE_UNITS": ["tsp", "tbsp", "cup", "g", "lb", "item"],
    }
    exec(compile(functions, str(APP_PATH), "exec"), namespace)
    return namespace


def conversion_test_app(database, dialog=False):
    import streamlit as st
    import crumbcost_core
    from test_conversion_panel import app_functions

    namespace = app_functions(database)
    pantry_item = namespace["get_pantry"](1)[0]
    recipe = namespace["get_recipe_items"](1, 1)
    st.session_state.setdefault("conversion_setup_pantry_id", pantry_item["id"])
    st.session_state.setdefault("active_main_tab", "Create a Recipe")
    st.session_state.setdefault("recipe_items", recipe)
    st.metric(
        "Ingredient cost",
        str(crumbcost_core.calculate_ingredient_cost(pantry_item, 1, "tsp", namespace["ureg"])),
    )
    if st.session_state.conversion_setup_pantry_id is not None:
        if dialog:
            namespace["render_recipe_conversion_dialog"](pantry_item, "tsp")
        else:
            namespace["render_conversion_editor"](pantry_item, "test", "tsp")


class ConversionPanelTests(unittest.TestCase):
    def test_native_dialog_save_closes_and_persists(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "test.db"
            namespace = app_functions(database)
            namespace["init_db"]()
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("INSERT INTO users (id, google_id, email, name) VALUES (1, 'qa', 'test@example.invalid', 'Test')")
                connection.commit()
            namespace["save_pantry_item"](1, "SESAME SEED OIL", 1, "lb", 4)
            namespace["save_recipe"](1, "QA recipe", "Cookies", 24, 0, 0, 0, 0.55,
                                       [{"name": "SESAME SEED OIL", "qty": 1, "unit": "tsp"}])
            app = AppTest.from_function(conversion_test_app, args=(database, True)).run()
            self.assertFalse(app.exception)
            app.number_input(key="recipe_conversion_label_quantity_1").set_value(1).run()
            app.selectbox(key="recipe_conversion_label_unit_1").select("teaspoon").run()
            app.number_input(key="recipe_conversion_label_grams_1").set_value(4).run()
            self.assertIsNone(app.selectbox(key="recipe_conversion_advanced_fact_1").value)
            app.button(key="recipe_conversion_save_label_1").click().run()
            self.assertFalse(app.exception)
            self.assertIsNone(app.session_state.conversion_setup_pantry_id)
            self.assertEqual(namespace["get_pantry"](1)[0]["grams_per_teaspoon"], 4)
            self.assertAlmostEqual(float(app.metric[0].value), 16 / 453.59237)

    def test_label_widgets_save_without_advanced_and_reopen_from_database(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "test.db"
            namespace = app_functions(database)
            namespace["init_db"]()
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("INSERT INTO users (id, google_id, email, name) VALUES (1, 'qa', 'test@example.invalid', 'Test')")
                connection.commit()
            namespace["save_pantry_item"](1, "QA measurement ingredient", 1, "lb", 4)
            namespace["save_recipe"](1, "QA recipe", "Cookies", 24, 0, 0, 0, 55,
                                       [{"name": "QA measurement ingredient", "qty": 1, "unit": "tsp"}])
            app = AppTest.from_function(conversion_test_app, args=(database,)).run()
            self.assertFalse(app.exception)
            self.assertTrue(app.button(key="test_save_label_1").disabled)
            self.assertIn("Enter a quantity", " ".join(item.value for item in app.caption))
            app.number_input(key="test_label_quantity_1").set_value(1).run()
            app.selectbox(key="test_label_unit_1").select("teaspoon").run()
            self.assertTrue(app.button(key="test_save_label_1").disabled)
            app.number_input(key="test_label_grams_1").set_value(4).run()
            self.assertFalse(app.button(key="test_save_label_1").disabled)
            self.assertIsNone(app.selectbox(key="test_advanced_fact_1").value)
            app.run()
            self.assertEqual(app.number_input(key="test_label_grams_1").value, 4)
            app.button(key="test_save_label_1").click().run()
            self.assertFalse(app.exception)
            self.assertIsNone(app.session_state.conversion_setup_pantry_id)
            self.assertEqual(app.session_state.active_main_tab, "Create a Recipe")
            saved = namespace["get_pantry"](1)[0]
            self.assertEqual(saved["grams_per_teaspoon"], 4)
            self.assertEqual(saved["conversion_source"], "package label confirmed by user")
            reopened = AppTest.from_function(conversion_test_app, args=(database,)).run()
            self.assertFalse(reopened.exception)
            self.assertIn("Saved conversion: 4 g per teaspoon", [item.value for item in reopened.caption])
            self.assertAlmostEqual(float(reopened.metric[0].value), 16 / 453.59237)


if __name__ == "__main__":
    unittest.main()
