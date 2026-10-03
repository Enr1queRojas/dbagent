import json
from pathlib import Path
import re
import sqlite3
from tempfile import TemporaryDirectory
import unittest

import yaml

from agents.answer_agent import AnswerAgent
from agents.sql_agent import SQLAgent
from core.application import Application
from core.database_client import QueryResult
from core.query_service import QueryService
from core.sql_validator import SQLValidator
from tools.report_tools import ReportTools


ROOT = Path(__file__).parents[1]
QUESTION = "Muéstrame los 10 productos con mayor venta neta durante 1997"
GENERATED_SQL = """SELECT TOP (?) p.ProductName,
SUM(od.UnitPrice * od.Quantity * (1 - od.Discount)) AS NetSales
FROM dbo.Products AS p
JOIN dbo.[Order Details] AS od ON od.ProductID = p.ProductID
JOIN dbo.Orders AS o ON o.OrderID = od.OrderID
WHERE o.OrderDate >= ? AND o.OrderDate < ?
GROUP BY p.ProductName
ORDER BY NetSales DESC, p.ProductName"""
REFERENCE_SQL = """SELECT p.ProductName,
SUM(d.UnitPrice * d.Quantity * (1 - d.Discount)) AS NetSales
FROM Products p, OrderDetails d, Orders o
WHERE p.ProductID=d.ProductID AND o.OrderID=d.OrderID
  AND date(o.OrderDate) BETWEEN date('1997-01-01') AND date('1997-12-31')
GROUP BY p.ProductID, p.ProductName
ORDER BY NetSales DESC, p.ProductName LIMIT 10"""


class ChatResponse:
    done_reason = "stop"
    def __init__(self, content): self.content = content


class FakeModel:
    def __init__(self, content): self.content = content
    def chat(self, messages): return ChatResponse(self.content)


class Retriever:
    def __init__(self, context): self.context = context
    def retrieve(self, question):
        return {"context": self.context, "selected_objects": ["Products", "Order Details", "Orders"]}


class SQLiteQueryClient:
    """Fake database boundary: executes fixture data only, never a configured DB."""
    def __init__(self, connection): self.connection = connection
    def query(self, sql, params):
        translated = re.sub(r"SELECT\s+TOP\s*\(\?\)", "SELECT", sql, count=1,
                            flags=re.IGNORECASE)
        translated = translated.replace("dbo.[Order Details]", "OrderDetails")
        translated = translated.replace("dbo.Products", "Products").replace("dbo.Orders", "Orders")
        translated += " LIMIT ?"
        reordered = tuple(params[1:]) + (params[0],)
        cursor = self.connection.execute(translated, reordered)
        rows = [tuple(row) for row in cursor.fetchall()]
        return QueryResult([item[0] for item in cursor.description], rows, False, 0.001)


class FullFlowRegressionTests(unittest.TestCase):
    def setUp(self):
        fixture = json.loads((ROOT / "tests/fixtures/net_sales_1997.json").read_text(encoding="utf-8"))
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.executescript("""
          CREATE TABLE Products(ProductID INTEGER, ProductName TEXT);
          CREATE TABLE Orders(OrderID INTEGER, OrderDate TEXT);
          CREATE TABLE OrderDetails(OrderID INTEGER, ProductID INTEGER, UnitPrice NUMERIC,
                                    Quantity INTEGER, Discount NUMERIC);
        """)
        self.db.executemany("INSERT INTO Products VALUES (?,?)", fixture["products"])
        self.db.executemany("INSERT INTO Orders VALUES (?,?)", fixture["orders"])
        self.db.executemany("INSERT INTO OrderDetails VALUES (?,?,?,?,?)", fixture["order_details"])
        self.context = json.dumps({"objects": {
          "[dbo].[Products]": {"type": "BASE TABLE", "columns": [{"column_name": "ProductID"}, {"column_name": "ProductName"}]},
          "[dbo].[Order Details]": {"type": "BASE TABLE", "columns": [{"column_name": x} for x in ("OrderID", "ProductID", "UnitPrice", "Quantity", "Discount")]},
          "[dbo].[Orders]": {"type": "BASE TABLE", "columns": [{"column_name": "OrderID"}, {"column_name": "OrderDate"}]}
        }})

    def test_question_to_export_matches_independent_reference_query(self):
        with TemporaryDirectory() as tmp:
            policy = yaml.safe_load((ROOT / "config/sql_policy.yml").read_text(encoding="utf-8"))
            policy.update(enabled=True, allowed_objects=["dbo.Products", "dbo.Order Details", "dbo.Orders"])
            policy_path = Path(tmp) / "policy.yml"
            policy_path.write_text(yaml.safe_dump(policy), encoding="utf-8")
            plan_json = json.dumps({"sql": GENERATED_SQL,
                                    "params": [10, "1997-01-01", "1998-01-01"]})
            app = Application(Retriever(self.context), SQLAgent(FakeModel(plan_json)),
                              SQLValidator(policy_path), QueryService(SQLiteQueryClient(self.db)),
                              AnswerAgent(FakeModel("Resultados calculados desde las filas.")))
            response = app.handle({"question": QUESTION, "execute": True})
            self.assertEqual(response.status, "completed", response.to_dict())
            reference = [tuple(row) for row in self.db.execute(REFERENCE_SQL).fetchall()]
            self.assertEqual(response.data["rows"], reference)
            self.assertEqual(reference, [("Alpha", 26), ("Beta", 10)])
            artifact = ReportTools(output_dir=tmp).export(
                {key: response.data[key] for key in ("columns", "rows", "truncated")}, "csv")
            self.assertTrue(artifact.path.is_file())


if __name__ == "__main__":
    unittest.main()
