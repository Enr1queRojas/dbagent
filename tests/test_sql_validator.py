import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import yaml

from core.sql_validator import SQLValidator


class SQLValidatorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.policy_path = Path(self.tmp.name) / "policy.yml"
        self.policy = yaml.safe_load((Path(__file__).parents[1] / "config/sql_policy.yml").read_text())
        self.policy.update(enabled=True, allowed_objects=["dbo.Customers", "dbo.Orders", "dbo.Order Details"])
        self.write_policy()
        self.context = json.dumps({"objects": {
            "[dbo].[Customers]": {"type": "BASE TABLE", "columns": [
                {"column_name": "CustomerID"}, {"column_name": "CompanyName"}, {"column_name": "City"}]},
            "[dbo].[Orders]": {"type": "BASE TABLE", "columns": [
                {"column_name": "OrderID"}, {"column_name": "CustomerID"}, {"column_name": "OrderDate"}]},
            "[dbo].[Order Details]": {"type": "BASE TABLE", "columns": [
                {"column_name": "OrderID"}, {"column_name": "ProductID"}, {"column_name": "UnitPrice"}]},
        }})

    def write_policy(self):
        self.policy_path.write_text(yaml.safe_dump(self.policy), encoding="utf-8")

    def validate(self, sql, params=()):
        return SQLValidator(self.policy_path).validate({"sql": sql, "params": params}, self.context)

    def assertRejected(self, sql, text=None, params=()):
        result = self.validate(sql, params)
        self.assertFalse(result.allowed, result)
        if text:
            self.assertIn(text, result.explanation)

    def test_disabled_by_default(self):
        result = SQLValidator().validate({"sql": "SELECT 1", "params": []}, self.context)
        self.assertFalse(result.allowed)
        self.assertIn("deshabilitada", result.explanation)

    def test_valid_northwind_join_aggregate_case_and_parameter(self):
        result = self.validate("""
            SELECT c.CompanyName, COUNT(*) AS Total,
                   CASE WHEN COUNT(*) > ? THEN 'frequent' ELSE 'other' END AS Kind
            FROM dbo.Customers AS c JOIN dbo.Orders AS o
              ON o.CustomerID = c.CustomerID
            WHERE c.City <> '?' -- this ? is not a marker
            GROUP BY c.CompanyName
        """, [3])
        self.assertTrue(result.allowed, result.explanation)

    def test_valid_non_recursive_cte_and_names_with_spaces(self):
        result = self.validate("""
            WITH totals AS (
              SELECT od.OrderID, SUM(od.UnitPrice) AS total
              FROM dbo.[Order Details] od GROUP BY od.OrderID
            ) SELECT o.OrderID, t.total FROM dbo.Orders o
              JOIN totals t ON t.OrderID = o.OrderID
        """)
        self.assertTrue(result.allowed, result.explanation)

    def test_multiple_statements_and_select_into(self):
        self.assertRejected("SELECT CustomerID FROM dbo.Customers; SELECT OrderID FROM dbo.Orders", "exactamente")
        self.assertRejected("SELECT CustomerID INTO dbo.Copy FROM dbo.Customers", "SELECT INTO")

    def test_comments_do_not_hide_writes_and_cte_must_end_in_select(self):
        self.assertRejected("/* SELECT */ DELETE FROM dbo.Customers", "Solo se autoriza")
        self.assertRejected("WITH x AS (SELECT CustomerID FROM dbo.Customers) DELETE FROM dbo.Orders", "Solo se autoriza")

    def test_false_alias_unknown_and_ambiguous_columns(self):
        self.assertRejected("SELECT x.CustomerID FROM dbo.Customers c", "resolver")
        self.assertRejected("SELECT CustomerID FROM dbo.Customers c JOIN dbo.Orders o ON o.CustomerID=c.CustomerID", "resolver")
        self.assertRejected("SELECT c.DoesNotExist FROM dbo.Customers c", "resolver")

    def test_external_exec_star_and_disallowed_function(self):
        self.assertRejected("SELECT x.CustomerID FROM OtherDb.dbo.Customers x", "externa")
        self.assertRejected("EXEC dbo.report", "Solo se autoriza")
        self.assertRejected("SELECT * FROM dbo.Customers", "SELECT *")
        self.assertRejected("SELECT HASHBYTES('SHA2_256', c.CustomerID) FROM dbo.Customers c", "Función no autorizada")

    def test_context_is_not_authorization(self):
        self.policy["allowed_objects"] = ["dbo.Customers"]
        self.write_policy()
        self.assertRejected("SELECT OrderID FROM dbo.Orders", "allowed_objects")

    def test_question_marks_in_literals_and_comments_are_ignored(self):
        self.assertTrue(self.validate("SELECT CustomerID FROM dbo.Customers WHERE City='?' -- ?\n").allowed)
        self.assertRejected("SELECT CustomerID FROM dbo.Customers WHERE City=? /* ? */", "marcadores")


if __name__ == "__main__":
    unittest.main()
